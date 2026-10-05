"""Strict log records and accepted-branch assembly, independent of FEBio processes."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class Record:
    time: float
    name: str
    values: dict[int, tuple[float, ...]]
    text: str


def read_records(path: Path) -> list[Record]:
    records = []
    for block in re.split(r"(?=Data Record\s*#\d+)", path.read_text(encoding="utf-8", errors="replace")):
        name = re.search(r"(?m)^\s*Data\s*=\s*([\w]+)", block)
        time = re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block)
        if not name or not time:
            continue
        values = {}
        rows = []
        for line in block[time.end():].splitlines():
            fields = line.split()
            if len(fields) < 2 or not fields[0].isdigit():
                continue
            try:
                row = tuple(map(float, fields[1:]))
            except ValueError:
                continue
            key = int(fields[0])
            if key in values or not all(isfinite(v) for v in row):
                raise ValueError("Duplicate or non-finite log record")
            values[key] = row
            rows.append(line)
        t = float(time[1])
        if not isfinite(t) or not values:
            raise ValueError("Empty or invalid log record")
        text = f"Data Record #1\nTime = {t:.12g}\nData = {name[1]}\n" + "\n".join(rows) + "\n"
        records.append(Record(t, name[1], values, text))
    return records


def check_separated_preload(input_file: Path, records: list[Record]) -> float:
    """Conservative no-contact proof: a separating axial plane at every preload state.

    This avoids FEBio 4.13's non-serialized FaceDataRecord surface pointer.
    A positive gap between extrema proves separation of the linear triangular
    surfaces. Failure is conservative: it does NOT infer pressure from the gap.
    """
    root = ET.parse(input_file).getroot()
    nodes = {int(n.attrib["id"]): float(n.text.split(",")[0]) for n in root.findall("./Mesh/Nodes/node")}
    surfaces = []
    opposing = ("cover_outer_contact" if root.find("./Mesh/Surface[@name='cover_outer_contact']") is not None
                else "pressure_contact")
    for name in ("measurement_contact", opposing):
        surface = root.find(f"./Mesh/Surface[@name='{name}']")
        if surface is None or not len(surface):
            raise ValueError("Kontaktfläche fehlt")
        surfaces.append({int(n) for face in surface for n in face.text.split(",")})
    frames = [r for r in records if r.name == "nodal_displacement" and r.time <= 1 + 1e-8]
    if not frames or abs(frames[-1].time - 1) > 1e-8:
        raise ValueError("Druckgleichgewicht bei t=1 fehlt")
    gaps = []
    for frame in frames:
        if set(frame.values) != set(nodes) or any(len(v) != 3 for v in frame.values.values()):
            raise ValueError("Unvollständiges Netz im Druckaufbau")
        measurement, pressure = [[nodes[n] + frame.values[n][0] for n in surface] for surface in surfaces]
        gaps.append(min(pressure) - max(measurement))
    if min(gaps) <= 1e-6:
        raise ValueError("Kontaktfreiheit im Druckaufbau nicht nachweisbar: Anfangsspalt erhöhen.")
    return min(gaps)


def merge_accepted(directory: Path, path: list[dict]) -> Path:
    seen = set()
    blocks = []
    previous_end = -1.0
    previous_id = None
    for item in path:
        if item["parent"] != previous_id:
            raise ValueError("Accepted path contains mixed branches")
        log = (directory / item["log"]).resolve()
        if not log.is_relative_to(directory.resolve()):
            raise ValueError("Invalid accepted log path")
        records = read_records(log)
        selected = [r for r in records if r.time > previous_end + 1e-8]
        if not selected or abs(max(r.time for r in selected) - item["solver_time"]) > 1e-7:
            raise ValueError("Missing accepted endpoint")
        for record in selected:
            key = (record.name, record.time)
            if key in seen:
                raise ValueError("Duplicate accepted state")
            seen.add(key)
            blocks.append(record.text)
        previous_end = item["solver_time"]
        previous_id = item["checkpoint_id"]
    destination = directory / "solver.log"
    destination.write_text("\n".join(blocks), encoding="utf-8")
    return destination
