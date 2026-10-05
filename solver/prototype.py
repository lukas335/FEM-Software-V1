"""Run the isolated FEBio baseline from the command line.

Examples:
    python -m solver.prototype --generate-only
    python -m solver.prototype --febio C:\\FEBio\\bin\\febio4.exe
"""

import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from hashlib import sha256
from math import isfinite
from xml.etree import ElementTree as ET
from pathlib import Path
import re
import subprocess
import numpy as np

from geometry.mesh_generator import generate_wedge
from model.parameters import ShellParameters, SimulationParameters
from model.materials import material_snapshot
from model.deformation import read_mesh, read_deformation
from model.units import mmhg_to_mpa
from model.cavity import assess_volume_error, VOLUME_LIMIT_REL
from solver.febio_writer import write_febio
from solver.cavity_logging import cavity_constraint_id
from solver.accepted_history import check_separated_preload, read_records


def baseline() -> SimulationParameters:
    return SimulationParameters(
        ShellParameters(8.0, 1.25, "silicone_A30"),
        ShellParameters(22.0, 1.0, "silicone_A30"),
        pressure_mmhg=15.0,
        max_displacement_mm=1.0,
        fit_start_mm=0.75,
        fit_end_mm=1.0,
        wedge_angle_deg=3.0,
        mesh_quality="coarse",
    )


def parse_reaction_log(path: Path, expected_nodes: set[int], wedge_angle_deg: float,
                       displacement_node: int | None = None) -> list[tuple[float, float, float]]:
    """Return (time, relative indentation mm, full-rotation reaction N).

    With ``displacement_node`` set, the positive indentation is the mean ux of
    the rear-edge/support nodes minus the inner-pole ux. The latter moves in
    negative x during indentation, so this convention produces a positive
    increasing indentation distance.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    reactions = {}
    pole_displacements = {}
    support_displacements = {}
    # FEBio writes a Data Record for each converged time step.
    for block in re.split(r"(?=Data Record\s*#\d+)", content):
        match = re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block)
        if not match:
            continue
        time = float(match.group(1))
        if time < 1 - 1e-8:
            continue
        if re.search(r'Data\s*=\s*["\']?measurement_inner_pole', block):
            rows = [line.split() for line in block.splitlines()]
            values = [float(row[1]) for row in rows if len(row) == 2 and row[0].isdigit()
                      and (displacement_node is None or int(row[0]) == displacement_node)]
            if len(values) == 1:
                pole_displacements[time] = values[0]
            continue
        if not re.search(r'Data\s*=\s*["\']?support_reaction', block):
            continue
        values: dict[int, tuple[float, float]] = {}
        for line in block.splitlines():
            fields = line.replace(",", " ").split()
            if len(fields) != 3 or not fields[0].isdigit():
                continue
            node = int(fields[0])
            if node in expected_nodes:
                try:
                    values[node] = float(fields[1]), float(fields[2])
                except ValueError:
                    pass
        if set(values) != expected_nodes:
            raise ValueError(f"Incomplete support reaction record at t={time}")
        # FEBio's support reaction opposes the positive-x drive. Report
        # compression force as positive and scale the 3D wedge to 360°.
        support_displacements[time] = sum(pair[0] for pair in values.values()) / len(values)
        wedge_force = -sum(pair[1] for pair in values.values())
        reactions[time] = wedge_force * 360.0 / wedge_angle_deg
    records = [(time, support_displacements[time] - pole_displacements[time], reactions[time])
               for time in sorted(reactions)
               if displacement_node is not None and time in pole_displacements and time in support_displacements]
    if displacement_node is None:
        records = [(time, support_displacements[time], reactions[time])
                   for time in sorted(reactions) if time in support_displacements]
    if len(records) < 2:
        raise ValueError("No indentation reaction history found in FEBio log")
    return records


def export_displacements(log_path: Path, input_file: Path, expected_nodes: set[int], *, end_time: float = 2.0) -> int:
    """Export every converged state, plus the known unloaded reference at t=0."""
    frames = {0.0: {node: (0.0, 0.0, 0.0) for node in expected_nodes}}
    for block in re.split(r"(?=Data Record\s*#\d+)", log_path.read_text(encoding="utf-8", errors="replace")):
        if not re.search(r'Data\s*=\s*["\']?nodal_displacement\b', block):
            continue
        match = re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block)
        if not match:
            raise ValueError("Nodal displacement record has no time")
        time = float(match.group(1))
        values = {}
        for line in block.splitlines():
            fields = line.split()
            if len(fields) != 4 or not fields[0].isdigit():
                continue
            node = int(fields[0])
            if node in expected_nodes:
                values[node] = tuple(map(float, fields[1:]))
        if set(values) != expected_nodes or not all(isfinite(v) for xyz in values.values() for v in xyz):
            raise ValueError(f"Incomplete or invalid displacement state at t={time}")
        if time in frames and frames[time] != values:
            raise ValueError(f"Conflicting displacement states at t={time}")
        frames[time] = values
    if len(frames) < 3 or abs(max(frames) - end_time) > 1e-7:
        raise ValueError(f"Missing complete nodal history through t={end_time}")
    destination = input_file.with_name("nodal_displacements.csv")
    with destination.open("w", newline="", encoding="utf-8") as stream:
        stream.write(f"# model_sha256={sha256(input_file.read_bytes()).hexdigest()}\n")
        writer = csv.writer(stream)
        writer.writerow(("time", "node_id", "ux_mm", "uy_mm", "uz_mm"))
        for time, frame in sorted(frames.items()):
            writer.writerows((time, node, *frame[node]) for node in sorted(frame))
    return len(frames)


def export_cavity(log_path: Path, input_file: Path, params: SimulationParameters) -> dict:
    """Export constraint pressure + P0, or prescribed pressure in reservoir mode.

    Independent volume integration checks FEBio's step-2 reference volume.
    Artificial wedge caps lie on planes through the axis (zero volume flux).
    """
    mesh = read_mesh(input_file)
    history = read_deformation(input_file.with_name("nodal_displacements.csv"), mesh)
    root = ET.parse(input_file).getroot()
    lookup = {node: i for i, node in enumerate(mesh.node_ids)}
    faces = np.asarray([[lookup[int(node)] for node in face.text.split(",")]
                        for face in root.find("./Mesh/Surface[@name='fluid_cavity']")])
    positions = mesh.coordinates[None, ...] + history.displacements
    a, b, c = (positions[:, faces[:, j]] for j in range(3))
    volumes = np.einsum("tfi,tfi->tf", a, np.cross(b, c)).sum(axis=1) / 6
    if not (volumes > 0).all():
        raise ValueError("Cavity volume must be positive; check surface orientation")
    indices = np.flatnonzero(np.isclose(history.times, 1.0, atol=1e-8))
    if len(indices) != 1:
        raise ValueError("Missing pressure-equilibrium state at t=1")
    target = volumes[indices[0]]
    records = {}
    cavity_id = str(cavity_constraint_id(root)) if not params.constant_pressure else None
    for block in re.split(r"(?=Data Record\s*#\d+)", log_path.read_text(encoding="utf-8", errors="replace")):
        if not re.search(r'Data\s*=\s*["\']?cavity_state\b', block):
            continue
        time = float(re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block).group(1))
        rows = [line.split() for line in block.splitlines()]
        values = [tuple(map(float, row[1:])) for row in rows if len(row) == 3 and row[0] == cavity_id]
        if len(values) != 1 or not all(isfinite(value) for value in values[0]):
            raise ValueError(f"Invalid cavity constraint record at t={time}")
        records[time] = values[0]
    pressure = []
    errors = []
    for time, volume in zip(history.times, volumes):
        if time <= 1.0 + 1e-8:
            pressure.append(params.pressure_mmhg * time)
            errors.append(0.0)  # Not constrained during filling.
        elif params.constant_pressure:
            pressure.append(params.pressure_mmhg)
            errors.append((volume - target) / target)  # Allowed change, NOT a constraint error.
        else:
            if time not in records:
                raise ValueError(f"Missing cavity pressure at t={time}")
            delta_p, logged_volume = records[time]
            if not np.isclose(logged_volume, volume, rtol=1e-5, atol=1e-8):
                raise ValueError("Constraint volume does not match the cavity mesh")
            pressure.append(params.pressure_mmhg + delta_p / mmhg_to_mpa(1.0))
            errors.append((volume - target) / target)
    max_error = max(abs(error) for error in errors)
    warnings = assess_volume_error(max_error, constant_pressure=params.constant_pressure)
    for warning in warnings:
        print(f"STATUS: Warnung: {warning}", flush=True)
    with input_file.with_name("cavity_history.csv").open("w", newline="", encoding="utf-8") as stream:
        stream.write(f"# model_sha256={mesh.input_hash}\n")
        writer = csv.writer(stream)
        writer.writerow(("time", "pressure_mmhg", "volume_full_mm3", "relative_volume_error"))
        writer.writerows(zip(history.times, pressure, volumes * 360 / params.wedge_angle_deg, errors))
    volume_key = "max_relative_volume_change" if params.constant_pressure else "max_relative_volume_error"
    return {"filled_volume_full_mm3": float(target * 360 / params.wedge_angle_deg),
            volume_key: float(max_error), "final_pressure_mmhg": float(pressure[-1]),
            "warnings": warnings, "volume_error_limit_relative": None if params.constant_pressure else VOLUME_LIMIT_REL}


def failure_summary(*texts: str) -> str:
    text = "\n".join(texts)
    negative = re.findall(r"(\d+)\s+negative jacobians? detected", text, re.I)
    if negative:
        summary = f"{negative[-1]} negative Jacobians"
        if re.search(r"Max\. nr of retries reached", text, re.I):
            summary += "; automatische Wiederholungen ausgeschöpft"
        return summary
    if re.search(r"Max\. nr of retries reached", text, re.I):
        return "automatische Wiederholungen ausgeschöpft"
    for pattern, label in ((r"ERROR TERMINATION", "FEBio error termination"),
                           (r"inverted elements|negative jacobians", "Elementumkehr"),
                           (r"error|failed", "FEBio-Fehler")):
        if re.search(pattern, text, re.I):
            return label
    return "Unbekannter FEBio-Fehler"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate/run the isolated FEBio baseline")
    parser.add_argument("--febio", type=Path, help="Path to FEBio 4 executable")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("work/simulations/baseline_prototype"))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--parameters", type=Path, help="JSON parameter snapshot from the desktop app")
    parser.add_argument("--initial-gap-mm", type=float,
                        help="Unloaded tip-to-tip gap in mm (default: 1.0)")
    parser.add_argument("--compact", action="store_true",
                        help="Keine Mesh-Zeitreihen und keine XPLT-Datei speichern")
    args = parser.parse_args()
    if not args.generate_only and not args.febio:
        parser.error("Specify --febio or --generate-only")
    try:
        params = (SimulationParameters.from_dict(json.loads(args.parameters.read_text(encoding="utf-8")))
                  if args.parameters else baseline())
        if args.initial_gap_mm is not None:
            params = replace(params, initial_gap_mm=args.initial_gap_mm)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(f"Invalid parameter file: {exc}")
    if errors := params.validate():
        parser.error("; ".join(errors))
    if not args.generate_only and not args.febio.is_file():
        parser.error(f"FEBio executable not found: {args.febio}")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    print("STATUS: Netz wird erzeugt …", flush=True)
    mesh = generate_wedge(params)
    input_file = directory / "model.feb"
    retain_visualization = not args.compact
    write_febio(mesh, params, input_file, retain_visualization=retain_visualization)
    metadata = {"parameters": asdict(params), "created_utc": datetime.now(timezone.utc).isoformat(),
                "nodes": len(mesh.nodes), "elements": {name: len(items) for name, items in mesh.elements.items()},
                "febio_status": "not_run", "visualization_data": retain_visualization}
    metadata["materials"] = material_snapshot(params)
    metadata["measurement_geometry_model"] = "tangent_inner_contour_v2"
    if params.cover_shell.enabled:
        from geometry.cover_mesh_sizing import REAR_SIZE_FACTOR, TRANSITION_ANGLE_DEG
        metadata["cover_mesh_sizing"] = dict(version="matched_contact_v2", rear_size_factor=REAR_SIZE_FACTOR,
                                            transition_angle_deg=TRANSITION_ANGLE_DEG)
    metadata["geometry_model"] = ("bonded_cover_frictionless_sphere_v1" if params.cover_shell.enabled
                                  else "full_sphere_rear_half_fixed_v1")
    metadata["cavity_model"] = ("P0_preload_then_constant_pressure" if params.constant_pressure
                                else "P0_preload_then_constant_inflated_volume")
    (directory / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Generated {input_file} ({len(mesh.nodes)} nodes)")
    if args.generate_only:
        return 0
    print("STATUS: FEBio rechnet …", flush=True)
    version_info = subprocess.run([str(args.febio.resolve()), "-info", "-norun"],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace", timeout=10, check=False,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    version_match = re.search(r"FEBio version\s*=\s*(\S+)", version_info.stdout)
    metadata["febio_version"] = version_match.group(1) if version_match else "unknown"
    metadata["febio_executable"] = str(args.febio.resolve())
    if params.drive_control == "target_force":
        from solver.target_force import run_target_force
        return run_target_force(directory, params, args.febio, metadata, args.timeout,
                                retain_visualization=retain_visualization)
    try:
        completed = subprocess.run([str(args.febio.resolve()), "-i", str(input_file)], cwd=directory,
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   errors="replace", timeout=args.timeout, check=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        metadata["febio_status"] = "timeout"
        (directory / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        raise RuntimeError(f"FEBio timed out after {args.timeout} s") from exc
    (directory / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (directory / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    log_path = directory / "solver.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if completed.returncode != 0 or "N O R M A L   T E R M I N A T I O N" not in log_text:
        metadata["febio_status"] = "solver_failed"
        metadata["failure_summary"] = failure_summary(completed.stdout, completed.stderr, log_text)
        metadata["log_files"] = [name for name in ("stdout.txt", "stderr.txt", "solver.log")
                                  if (directory / name).is_file()]
        (directory / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        raise RuntimeError(f"{metadata['failure_summary']} (Exit {completed.returncode}); Logs: {directory}")
    try:
        print("STATUS: Ergebnisse werden aufbereitet …", flush=True)
        if params.cover_shell.enabled:
            metadata["preload_gap_mm"] = check_separated_preload(input_file, read_records(log_path))
        history = parse_reaction_log(log_path, set(mesh.node_sets["measurement_support"]), params.wedge_angle_deg,
                                     mesh.node_sets["measurement_inner_pole"][0])
        if retain_visualization:
            metadata["deformation_frames"] = export_displacements(log_path, input_file, set(mesh.nodes))
            metadata["cavity_results"] = export_cavity(log_path, input_file, params)
    except (OSError, ValueError) as exc:
        metadata["febio_status"] = "result_parse_failed"
        metadata["failure_summary"] = f"Ergebnisaufbereitung fehlgeschlagen: {exc}"
        metadata["log_files"] = [name for name in ("stdout.txt", "stderr.txt", "solver.log")
                                  if (directory / name).is_file()]
        (directory / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        raise
    with (directory / "force_displacement.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time", "drive_mm", "reaction_full_N"))
        writer.writerows(history)
    metadata["febio_status"] = "success"
    metadata["log_files"] = [name for name in ("stdout.txt", "stderr.txt", "solver.log")
                              if (directory / name).is_file()]
    (directory / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {len(history)} indentation records to {directory / 'force_displacement.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
