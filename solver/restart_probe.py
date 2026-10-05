"""Small, isolated cold-restart/branching experiment (never edits source runs)."""

import argparse
import json
import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree as ET


def write_xml(root, path):
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def execute(exe, directory, input_name, time, restart=False):
    result = subprocess.run(
        [str(exe), "-r" if restart else "-i", input_name,
         "-dump", "state.dmp", "-break", str(time)],
        cwd=directory, input="quit\n", capture_output=True, text=True,
        errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    (directory / "stdout.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(directory.name, result.returncode, "dump", (directory / "state.dmp").exists(), flush=True)
    if (result.returncode != 1 or "breakpoint" not in result.stdout
            or "Early termination by user request" not in result.stdout + result.stderr):
        raise RuntimeError(f"Restart probe did not reach a controlled breakpoint: {directory}")
    return result


def branch(exe, parent, destination, time0, u0, time1, u1):
    destination.mkdir()
    for name in ("state.dmp", "solver.log", "model.xplt"):
        shutil.copy2(parent / name, destination / name)
    root = ET.Element("febio_restart", version="4.0")
    ET.SubElement(root, "Archive").text = "state.dmp"
    loads = ET.SubElement(root, "LoadData")
    curve = ET.SubElement(loads, "load_controller", id="2", type="loadcurve")
    points = ET.SubElement(curve, "points")
    for t, u in ((0, 0), (1, 0), (time0, u0), (time1, u1), (1001, u1)):
        ET.SubElement(points, "point").text = f"{t:.12g},{u:.12g}"
    write_xml(root, destination / "restart.feb")
    execute(exe, destination, "restart.feb", time1, True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--febio", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    seed = args.output / "seed"
    seed.mkdir()
    root = ET.parse(args.source / "model.feb").getroot()
    # FEBio 4.13 face_data loses its surface on cold restart (access violation
    # in contact_state output). Isolate mechanical restart without this logger.
    logfile = root.find("./Output/logfile")
    for face_logger in list(logfile.findall("face_data")):
        logfile.remove(face_logger)
    control = root.find("./Step/step[@id='2']/Control")
    control.find("time_steps").text = "20000"
    control.find("step_size").text = "0.05"
    root.find("./Step/step[@id='2']/Boundary/bc/value").text = "1"
    points = root.find("./LoadData/load_controller[@id='2']/points")
    points.clear()
    for t in (0, 1, 1001):
        ET.SubElement(points, "point").text = f"{t},0"
    write_xml(root, seed / "model.feb")
    execute(args.febio, seed, "model.feb", 1.05)
    seed_hash = sha256((seed / "state.dmp").read_bytes()).hexdigest()
    branch(args.febio, seed, args.output / "large", 1.05, 0, 1.10, 1.0)
    branch(args.febio, seed, args.output / "small", 1.05, 0, 1.10, 0.8)
    summary = {}
    from model.parameters import SimulationParameters
    from solver.prototype import export_displacements, export_cavity, parse_reaction_log
    params = SimulationParameters.from_dict(json.loads((args.source / "case.json").read_text())["parameters"])
    support = set(map(int, root.find("./Mesh/NodeSet[@name='measurement_support']").text.split(",")))
    nodes = {int(n.attrib["id"]) for n in root.findall("./Mesh/Nodes/node")}
    for name in ("seed", "large", "small"):
        directory = args.output / name
        if name != "seed":
            shutil.copy2(seed / "model.feb", directory / "model.feb")
        log = (directory / "solver.log").read_text(errors="replace")
        end = float(re.findall(r"Time\s*=\s*([\d.eE+-]+)", log)[-1])
        export_displacements(directory / "solver.log", directory / "model.feb", nodes, end_time=end)
        summary[name] = export_cavity(directory / "solver.log", directory / "model.feb", params)
        summary[name]["reaction"] = parse_reaction_log(directory / "solver.log", support, params.wedge_angle_deg)[-1]
    assert sha256((seed / "state.dmp").read_bytes()).hexdigest() == seed_hash
    assert abs(summary["large"]["reaction"][1] - 1.0) < 1e-9
    assert abs(summary["small"]["reaction"][1] - .8) < 1e-9
    assert summary["large"]["reaction"][2] > summary["small"]["reaction"][2] > 0
    assert all(abs(s["filled_volume_full_mm3"] / summary["seed"]["filled_volume_full_mm3"] - 1) < 1e-10
               for s in summary.values())
    (args.output / "probe.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
