"""Atomic checkpoints; immutable definitions and non-destructive run attempts."""

import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
import shutil
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from model.materials import material_snapshot as parameter_material_snapshot
from model.parameters import SimulationParameters
from model.study import StudyConfig
from model.study_results import analyze_case, rank_geometries, refresh_automatic_fits


def atomic_text(path: Path, value: str):
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                            prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows scanners/editors can briefly hold the target without delete sharing.
    # Never truncate the existing checkpoint as a fallback.
    for delay in (0.05, 0.1, 0.2, 0.4, None):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def material_snapshot(config):
    return parameter_material_snapshot(config.base_parameters)


class StudyStorage:
    def __init__(self, directory, definition, cases, phase="references"):
        self.directory = Path(directory).resolve()
        self.definition = definition
        self.config = StudyConfig.from_dict(definition["config"])
        self.cases = cases
        self.phase = phase
        self.warnings = []

    @classmethod
    def create(cls, parent: Path, config: StudyConfig):
        cases = config.cases()
        directory = parent / (datetime.now().strftime("study_%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        definition = {"version": 1, "analysis_version": 2,
                      "created_utc": datetime.now(timezone.utc).isoformat(),
                      "config": asdict(config), "materials": material_snapshot(config)}
        atomic_text(directory / "study.json", json.dumps(definition, indent=2, allow_nan=False))
        result = cls(directory, definition, cases)
        result.save()
        return result

    @classmethod
    def open(cls, directory: Path):
        definition = json.loads((directory / "study.json").read_text(encoding="utf-8"))
        if definition.get("version") != 1 or definition.get("analysis_version") not in (1, 2):
            raise ValueError("Nicht unterstützte Studien-/Auswertungsversion.")
        # Versioned checkpoints are authoritative; cases.json and CSVs are mirrors
        # that may be temporarily locked by other Windows applications.
        checkpoints = sorted((directory / "checkpoints").glob("state_*.json"), reverse=True)
        state_path = checkpoints[0] if checkpoints else directory / "cases.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        result = cls(directory, definition, state["cases"], state["phase"])
        expected = {case["id"]: case for case in result.config.cases()}
        if len(result.cases) != len(expected) or {c["id"] for c in result.cases} != set(expected):
            raise ValueError("Fallliste passt nicht zur gespeicherten Studie.")
        for case in result.cases:
            for key in ("parameters", "references", "in_grid"):
                actual, wanted = case[key], expected[case["id"]][key]
                if key == "parameters":
                    actual, wanted = SimulationParameters.from_dict(actual), SimulationParameters.from_dict(wanted)
                if actual != wanted:
                    raise ValueError("Veränderte Fallparameter in gespeicherter Studie.")
            if case["status"] not in ("pending", "running", "success", "invalid", "failed"):
                raise ValueError("Unbekannter Fallstatus.")
            for attempt in case["attempts"]:
                result.run_path(attempt)
            if case["status"] == "running":
                case.update(status="pending", error="Unterbrochener Lauf; neuer Versuch beim Fortsetzen.")
            if case["status"] in ("pending", "failed") and case["attempts"]:
                # A solver may have finished before the old checkpoint swap failed.
                # Recover from its own immutable parameter snapshot, never rerun it blindly.
                path = result.run_path(case["attempts"][-1])
                try:
                    metadata = json.loads((path / "case.json").read_text(encoding="utf-8"))
                    parameters_match = SimulationParameters.from_dict(metadata["parameters"]) == SimulationParameters.from_dict(case["parameters"])
                    if metadata.get("febio_status") in ("success", "partial") and parameters_match:
                        from model.deformation import read_mesh, read_deformation
                        from model.cavity import read_cavity
                        mesh = read_mesh(path / "model.feb")
                        read_deformation(path / "nodal_displacements.csv", mesh)
                        if metadata.get("cavity_model"):
                            read_cavity(path / "cavity_history.csv", mesh.input_hash)
                        try:
                            analysis = analyze_case(path, result.config)
                            case.update(status="success", result=analysis, error="",
                                        auto_fit_eligible=result.config.fit_mode == "automatic",
                                        fingerprints=result.fingerprints(path))
                        except ValueError as exc:
                            case.update(status="invalid", result=None, error=str(exc))
                        result.warnings.append(f"Vorhandenen Lauf wiederhergestellt: {case['id']}")
                except (OSError, ValueError, KeyError):
                    pass
                if not path.exists() and "WinError" in case["error"]:
                    case.update(status="pending", error="Vor Solverstart am Speichern gescheitert; noch ausstehend.")
            if case["status"] == "success" or case.get("auto_fit_eligible"):
                try:
                    path = result.run_path(case["attempts"][-1])
                    if result.fingerprints(path) != case["fingerprints"]:
                        raise ValueError("Ergebnisdateien verändert")
                except (OSError, ValueError, KeyError, IndexError) as exc:
                    case.update(status="failed", result=None, error=f"Ergebnisprüfung: {exc}", auto_fit_eligible=False)
        refresh_automatic_fits(result.cases, result.config, result.run_path)
        return result

    @staticmethod
    def fingerprints(path):
        return {name: sha256((path / name).read_bytes()).hexdigest()
                for name in ("model.feb", "force_displacement.csv", "case.json")}

    def assert_resumable(self):
        if self.definition["materials"] != material_snapshot(self.config):
            raise ValueError("Materialkoeffizienten wurden geändert. Neue Studie anlegen; kein gemischtes Fortsetzen.")

    def automatic_copy(self):
        """Re-analyze saved solver outputs in a separate study, never modify source.

        No solver is started. Use independent file copies so future edits cannot
        silently affect both studies through a hard link.
        """
        self.assert_resumable()
        derived = StudyStorage.create(self.directory.parent, replace(self.config, fit_mode="automatic"))
        source_cases = {case["id"]: case for case in self.cases}
        artifacts = ("parameters.json", "case.json", "model.feb", "solver.log",
                     "force_displacement.csv", "nodal_displacements.csv", "cavity_history.csv")
        for case in derived.cases:
            source = source_cases[case["id"]]
            if not source["attempts"]:
                continue
            source_path = self.run_path(source["attempts"][-1])
            metadata_path = source_path / "case.json"
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("febio_status") != "success":
                    continue
                if SimulationParameters.from_dict(metadata["parameters"]) != SimulationParameters.from_dict(case["parameters"]):
                    raise ValueError("Ergebnisparameter passen nicht zum Studienfall.")
                from model.deformation import read_mesh, read_deformation
                from model.cavity import read_cavity
                mesh = read_mesh(source_path / "model.feb")
                read_deformation(source_path / "nodal_displacements.csv", mesh)
                if metadata.get("cavity_model"):
                    read_cavity(source_path / "cavity_history.csv", mesh.input_hash)
            except (OSError, ValueError, KeyError) as exc:
                case.update(status="failed", error=f"Gespeichertes Ergebnis nicht verwendbar: {exc}")
                continue
            target = derived.new_attempt(case)
            target.mkdir(parents=True, exist_ok=False)
            # Essential source artifacts above have been validated. Large FEBio
            # binary plots are unnecessary for the app's CSV-driven animation.
            for name in artifacts:
                if (source_path / name).is_file():
                    shutil.copy2(source_path / name, target / name)
            case["source_study"] = str(self.directory)
            case["fingerprints"] = derived.fingerprints(target)
            try:
                analysis = analyze_case(target, derived.config)
                case.update(status="success", result=analysis, error="", auto_fit_eligible=True)
            except ValueError as exc:
                case.update(status="invalid", error=str(exc))
            derived.save()
        derived.phase = self.phase
        derived.save()
        return derived

    def run_path(self, relative):
        path = (self.directory / relative).resolve()
        if not path.is_relative_to(self.directory / "runs") or path == self.directory / "runs":
            raise ValueError("Ungültiger Laufpfad in Studie.")
        return path

    def new_attempt(self, case):
        relative = f"runs/{case['id']}/attempt_{uuid4().hex[:10]}"
        case["attempts"].append(relative)
        return self.run_path(relative)

    def save(self):
        self.warnings = []
        refresh_automatic_fits(self.cases, self.config, self.run_path)
        state = json.dumps({"phase": self.phase, "cases": self.cases}, indent=2, allow_nan=False)
        checkpoints = self.directory / "checkpoints"
        checkpoints.mkdir(exist_ok=True)
        existing = sorted(checkpoints.glob("state_*.json"))
        revision = int(existing[-1].stem.split("_")[1]) + 1 if existing else 1
        atomic_text(checkpoints / f"state_{revision:08d}_{uuid4().hex[:8]}.json", state)
        try:
            atomic_text(self.directory / "cases.json", state)
        except PermissionError:
            self.warnings.append("cases.json gesperrt; Studienstand im versionierten Checkpoint gesichert.")
        results = []
        for case in self.cases:
            shell = case["parameters"]["measurement_shell"]
            thickness_x = shell.get("thickness_x_mm", shell.get("thickness_mm"))
            thickness_y = shell.get("thickness_y_mm", thickness_x)
            results.append({"id": case["id"], "radius_mm": shell["radius_mm"],
                "thickness_mm": thickness_x, "thickness_x_mm": thickness_x, "thickness_y_mm": thickness_y,
                "pressure_mmhg": case["parameters"]["pressure_mmhg"],
                "status": case["status"], "error": case["error"], "references": case["references"],
                "run": case["attempts"][-1] if case["attempts"] else "", **(case["result"] or {})})
        for name, rows in (("results.csv", results), ("ranking.csv", rank_geometries(self.cases, self.config))):
            keys = list(dict.fromkeys(key for row in rows for key in row))
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=keys)
            writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                              for k, v in row.items()} for row in rows)
            try:
                atomic_text(self.directory / name, buffer.getvalue())
            except PermissionError:
                self.warnings.append(f"{name} gesperrt; Export nicht aktualisiert. Studienstand ist gesichert.")
