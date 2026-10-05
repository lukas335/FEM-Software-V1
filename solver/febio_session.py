"""Immutable cold checkpoints; each trial runs in its own FEBio directory."""

from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import shutil
import subprocess
import time
from xml.etree import ElementTree as ET

from solver.accepted_history import read_records, check_separated_preload
from solver.force_controller import AcceptedState
from solver.cavity_logging import cavity_constraint_id


class TrialFailed(RuntimeError):
    pass


def _febio_failure_summary(text):
    """Return a short actionable FEBio cause for the GUI/status message."""
    negative = re.findall(r"(\d+)\s+negative jacobians? detected", text, re.I)
    retries = re.search(r"Max\. nr of retries reached", text, re.I)
    if negative:
        count = negative[-1]
        suffix = "; automatische Wiederholungen ausgeschöpft" if retries else ""
        return f"{count} negative Jacobians{suffix}"
    if retries:
        return "automatische Wiederholungen ausgeschöpft"
    for pattern, summary in (
        (r"ERROR TERMINATION", "FEBio error termination"),
        (r"Failed creating restart file", "Restart-Datei konnte nicht erstellt werden"),
    ):
        if re.search(pattern, text, re.I):
            return summary
    return None


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


class FEBioSession:
    def __init__(self, directory, params, executable, timeout=900, *, retain_visualization=True):
        self.directory = Path(directory).resolve()
        self.params = params
        self.executable = Path(executable).resolve()
        self.deadline = time.monotonic() + timeout
        self.entries = {}
        self.counter = 0
        self.retain_visualization = retain_visualization
        (self.directory / "checkpoints").mkdir(exist_ok=True)
        (self.directory / "trials").mkdir(exist_ok=True)
        self.root = ET.parse(self.directory / "model.feb").getroot()
        self.nodes = {int(n.attrib["id"]) for n in self.root.findall("./Mesh/Nodes/node")}
        self.support = set(map(int, self.root.find("./Mesh/NodeSet[@name='measurement_support']").text.split(",")))
        pole_set = self.root.find("./Mesh/NodeSet[@name='measurement_inner_pole']")
        self.measurement_pole = int(pole_set.text.strip()) if pole_set is not None else None

    def initialize(self):
        trial = self.directory / "trials" / "preload"
        trial.mkdir()
        shutil.copy2(self.directory / "model.feb", trial / "model.feb")
        state = self._run(trial, "model.feb", 1.05, 0, None)
        self.preload_gap_mm = (check_separated_preload(trial / "model.feb", read_records(trial / "solver.log"))
                               if self.retain_visualization else None)
        return state

    def advance(self, source_id, target_drive):
        parent = self.entries[source_id]
        if not parent["drive_mm"] < target_drive <= self.params.max_displacement_mm + 1e-10:
            raise ValueError("Drive increment violates monotonicity or displacement limit")
        checkpoint = self.directory / parent["archive"]
        if fingerprint(checkpoint) != parent["archive_sha256"]:
            raise ValueError("Checkpoint was modified")
        self.counter += 1
        trial = self.directory / "trials" / f"trial_{self.counter:04d}"
        trial.mkdir()
        # FEBio is only ever allowed to modify the trial copy, not the source.
        shutil.copy2(checkpoint, trial / "parent.dmp")
        end_time = parent["solver_time"] + .05
        root = ET.Element("febio_restart", version="4.0")
        ET.SubElement(root, "Archive").text = "parent.dmp"
        loads = ET.SubElement(root, "LoadData")
        curve = ET.SubElement(loads, "load_controller", id="2", type="loadcurve")
        points = ET.SubElement(curve, "points")
        for t, u in ((0, 0), (1, 0), (parent["solver_time"], parent["drive_mm"]),
                     (end_time, target_drive), (1001, target_drive)):
            ET.SubElement(points, "point").text = f"{t:.12g},{u:.12g}"
        ET.indent(root)
        ET.ElementTree(root).write(trial / "restart.feb", encoding="utf-8", xml_declaration=True)
        return self._run(trial, "restart.feb", end_time, target_drive, source_id)

    def _run(self, trial, input_name, break_time, target_drive, parent):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(str(self.executable), 0)
        restart = parent is not None
        command = [str(self.executable), "-r" if restart else "-i", input_name,
                   "-dump", "state.dmp", "-break", f"{break_time:.12g}"]
        # FEBio 4.13 diagnostic output: include element IDs when a trial state
        # has negative Jacobians. This is stdout-only and does not affect the
        # solved model or the production contact logger.
        command += ["-output_negative_jacobians=100"]
        if restart:
            # The restart importer appends to the log even with -noappend.
            # Pre-create an EMPTY local log; never copy a rejected branch.
            (trial / "solver.log").touch()
            command += ["-noappend", "-o", "solver.log", "-p", "model.xplt"]
        with (trial / "stdout.txt").open("w", encoding="utf-8") as stdout, (trial / "stderr.txt").open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(command, cwd=trial, input="quit\n", text=True,
                stdout=stdout, stderr=stderr, timeout=remaining,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (trial / "stdout.txt").read_text(encoding="utf-8", errors="replace")
        output += (trial / "stderr.txt").read_text(encoding="utf-8", errors="replace")
        # Intentional quit AFTER a converged breakpoint exits 1 in FEBio 4.13.
        # Neither an existing dump nor exit 1 alone proves convergence.
        if (completed.returncode != 1 or "breakpoint" not in output or "reached" not in output
                or "Early termination by user request" not in output or not (trial / "state.dmp").is_file()):
            cause = _febio_failure_summary(output)
            detail = f": {cause}" if cause else f" (exit {completed.returncode})"
            raise TrialFailed(f"Druckaufbau fehlgeschlagen{detail}; Protokoll: {trial}")
        convergence = list(re.finditer(r"converged at time\s*:\s*([\d.eE+-]+)", output))
        if (not convergence or "Restart point created. Archive name is state.dmp." not in output[convergence[-1].end():]
                or "Failed creating restart file" in output or (trial / "state.dmp").stat().st_size == 0):
            raise TrialFailed(f"No confirmed final restart checkpoint: {trial}")
        try:
            records = read_records(trial / "solver.log")
            end = max(r.time for r in records)
            if abs(end - float(convergence[-1][1])) > max(1e-5, end * 1e-5):
                raise ValueError("Dump and logged state refer to different endpoints")
            if end < break_time - 1e-7 or end > break_time + .051:
                raise ValueError("Breakpoint endpoint missing")
            final = {r.name: r.values for r in records if r.time == end}
            reaction = final["support_reaction"]
            if self.retain_visualization:
                nodal = final["nodal_displacement"]
                if set(nodal) != self.nodes or any(len(v) != 3 for v in nodal.values()):
                    raise ValueError("Incomplete endpoint nodal data")
            if set(reaction) != self.support:
                raise ValueError("Incomplete endpoint reaction data")
            if any(len(v) != 2 or abs(v[0] - target_drive) > 1e-7 for v in reaction.values()):
                raise ValueError("Incorrect endpoint drive")
            if not self.params.constant_pressure:
                cavity_id = cavity_constraint_id(self.root)
                if set(final["cavity_state"]) != {cavity_id} or len(final["cavity_state"][cavity_id]) != 2:
                    raise ValueError("Incomplete cavity endpoint")
            force = -sum(v[1] for v in reaction.values()) * 360 / self.params.wedge_angle_deg
            if not isfinite(force):
                raise ValueError("Invalid force")
        except (ValueError, KeyError) as exc:
            raise TrialFailed(f"Invalid trial result: {exc}") from exc
        state = AcceptedState(trial.name, target_drive, force, end)
        archive = self.directory / "checkpoints" / f"{state.checkpoint_id}.dmp"
        if archive.exists():
            raise ValueError("Checkpoint already exists")
        shutil.copy2(trial / "state.dmp", archive)
        entry = dict(asdict(state), parent=parent, archive=str(archive.relative_to(self.directory)),
                     archive_sha256=fingerprint(archive), log=str((trial / "solver.log").relative_to(self.directory)),
                     input_sha256=fingerprint(trial / input_name))
        self.entries[state.checkpoint_id] = entry
        (trial / "checkpoint.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
        return state
