"""Non-blocking, isolated solver runs from immutable GUI parameter snapshots."""

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from model.deformation import read_deformation, read_mesh
from model.cavity import read_cavity
from model.parameters import SimulationParameters
from model.results import read_force_curve


def find_febio() -> Path | None:
    candidates = [os.environ.get("FEBIO_EXE"), shutil.which("febio4"),
                  str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "FEBioStudio/bin/febio4.exe")]
    return next((Path(path) for path in candidates if path and Path(path).is_file()), None)


class SimulationRunner(QObject):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)
    mesh_ready = Signal(object)
    prepared = Signal(object)

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.busy = False
        self.directory: Path | None = None
        self.output = ""
        self._pending = ""
        self.generate_only = False
        self._mesh_announced = False

    def start(self, params: SimulationParameters, febio: Path | None, *, directory: Path | None = None,
              compact: bool = False, final_only_visualization: bool = False, generate_only: bool = False):
        if self.busy:
            raise RuntimeError("Eine Simulation läuft bereits.")
        if errors := params.validate():
            raise ValueError(" ".join(errors))
        if not generate_only and (febio is None or not febio.is_file()):
            raise ValueError("FEBio wurde nicht gefunden. Bitte Solver auswählen.")
        directory = directory or self.project_root / ("work/mesh_previews" if generate_only else "work/simulations") / (
            datetime.now().strftime("run_%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        snapshot = directory / "parameters.json"
        snapshot.write_text(json.dumps(asdict(params), indent=2, allow_nan=False), encoding="utf-8")
        self.directory = directory
        self.generate_only = generate_only
        self._mesh_announced = False
        self.output = self._pending = ""
        environment = QProcessEnvironment.systemEnvironment()
        vendor = self.project_root / ".vendor"
        if vendor.is_dir():
            environment.insert("PYTHONPATH", str(vendor) + os.pathsep + environment.value("PYTHONPATH", ""))
        environment.insert("PYTHONIOENCODING", "utf-8")
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.project_root))
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        self.busy = True
        self.busy_changed.emit(True)
        self.progress.emit("Netzprüfung wird gestartet …" if generate_only else "Simulation wird gestartet …")
        arguments = ["-u", "-m", "solver.prototype", "--parameters", str(snapshot),
                     "--output", str(directory)]
        arguments += ["--generate-only"] if generate_only else ["--febio", str(febio.resolve())]
        if compact:
            arguments.append("--compact")
        elif final_only_visualization:
            arguments.append("--final-only-visualization")
        self.process.start(str(python), arguments)

    def _read_output(self):
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.output += chunk
        self._pending += chunk
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.startswith("STATUS: "):
                if line.strip() == 'STATUS: Eingabenetz bereit' and not self._mesh_announced:
                    self._mesh_announced = True
                    self.mesh_ready.emit(self.directory / 'model.feb')
                self.progress.emit(line[8:].strip())

    def _end(self):
        self.busy = False
        self.busy_changed.emit(False)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.busy:
            self._end()
            self.failed.emit(self.process.errorString())

    def _finished(self, exit_code, exit_status):
        if not self.busy:
            return
        self._read_output()
        try:
            (self.directory / "launcher.log").write_text(self.output, encoding="utf-8")
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                failure = {}
                try:
                    failure = json.loads((self.directory / "case.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                summary = failure.get("failure_summary")
                if summary:
                    raise ValueError(f"{summary} (Exit {exit_code}). Logs: {self.directory}")
                raise ValueError(f"Solverlauf fehlgeschlagen (Exit {exit_code}).\n{self.output[-5000:]}")
            metadata = json.loads((self.directory / "case.json").read_text(encoding="utf-8"))
            if self.generate_only:
                path = self.directory / 'model.feb'
                read_mesh(path)
                self._end()
                self.prepared.emit(path)
                return
            if metadata.get("febio_status") not in ("success", "partial"):
                raise ValueError("Der Solverlauf ist nicht erfolgreich abgeschlossen.")
            curve_path = self.directory / "force_displacement.csv"
            read_force_curve(curve_path)
            mesh = read_mesh(self.directory / "model.feb")
            if metadata.get("visualization_data", True):
                read_deformation(self.directory / "nodal_displacements.csv", mesh)
            if metadata.get("cavity_model") and (self.directory / "cavity_history.csv").is_file():
                read_cavity(self.directory / "cavity_history.csv", mesh.input_hash)
        except (OSError, ValueError, KeyError) as exc:
            self._end()
            self.failed.emit(f"{exc}\n\nLaufverzeichnis: {self.directory}")
            return
        self._end()
        self.succeeded.emit(curve_path)
