"""Mesh topology and actual nodal displacement histories, independent of Qt."""

import csv
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np


@dataclass(frozen=True)
class MeshPart:
    name: str
    triangles: np.ndarray
    edges: np.ndarray


@dataclass(frozen=True)
class MeshTopology:
    node_ids: tuple[int, ...]
    coordinates: np.ndarray
    parts: tuple[MeshPart, ...]
    input_hash: str


@dataclass(frozen=True)
class DeformationHistory:
    times: np.ndarray
    displacements: np.ndarray  # [saved state, node, xyz], in mm

    def at_time(self, time: float) -> np.ndarray:
        """Linear display interpolation; never extrapolate solver states."""
        if time <= self.times[0]:
            return self.displacements[0]
        if time >= self.times[-1]:
            return self.displacements[-1]
        upper = int(np.searchsorted(self.times, time, side="right"))
        lower = upper - 1
        weight = (time - self.times[lower]) / (self.times[upper] - self.times[lower])
        return (1 - weight) * self.displacements[lower] + weight * self.displacements[upper]


def read_mesh(path: Path) -> MeshTopology:
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    nodes = {int(n.attrib["id"]): tuple(map(float, n.text.split(",")))
             for n in root.findall("./Mesh/Nodes/node")}
    if not nodes:
        raise ValueError("Keine Knoten im Eingabenetz.")
    ids = tuple(sorted(nodes))
    lookup = {node: index for index, node in enumerate(ids)}
    coordinates = np.asarray([nodes[node] for node in ids], dtype=float)
    if coordinates.shape != (len(ids), 3) or not np.isfinite(coordinates).all():
        raise ValueError("Ungültige Knotenkoordinaten.")
    parts = []
    names = ["measurement", "pressure"]
    imported = [group.get('name') for group in root.findall('./Mesh/Elements')
                if group.get('name', '').startswith('measurement_') and group.get('name') != 'measurement_shell']
    if imported:
        names = imported + ['pressure']
    names += [name for name in ("cover_front", "cover_rear")
              if root.find(f"./Mesh/Surface[@name='{name}_symmetry_0']") is not None]
    for name in names:
        face = root.find(f"./Mesh/Surface[@name='{name}_symmetry_0']")
        if face is None or not len(face):
            raise ValueError("Axiale Schnittfläche fehlt im Eingabenetz.")
        triangles = []
        for entry in face:
            if entry.tag != "tri3":
                raise ValueError("Die Animation unterstützt aktuell Tri3-Schnittflächen.")
            triangles.append([lookup[int(node)] for node in entry.text.split(",")])
        edges = {tuple(sorted((a, b))) for tri in triangles for a, b in zip(tri, tri[1:] + tri[:1])}
        parts.append(MeshPart(name, np.asarray(triangles, dtype=int), np.asarray(sorted(edges), dtype=int)))
    return MeshTopology(ids, coordinates, tuple(parts), sha256(raw).hexdigest())


def read_deformation(path: Path, mesh: MeshTopology) -> DeformationHistory:
    """Read a complete history bound to the exact input mesh via SHA-256."""
    with path.open(encoding="utf-8", newline="") as stream:
        if stream.readline().strip() != f"# model_sha256={mesh.input_hash}":
            raise ValueError("Verschiebungsdaten gehören nicht zu diesem Eingabenetz.")
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["time", "node_id", "ux_mm", "uy_mm", "uz_mm"]:
            raise ValueError("Ungültiges Format der Verschiebungsdatei.")
        frames: dict[float, dict[int, tuple[float, ...]]] = {}
        previous_time = -float("inf")
        for row in reader:
            time, node = float(row["time"]), int(row["node_id"])
            displacement = tuple(float(row[axis]) for axis in ("ux_mm", "uy_mm", "uz_mm"))
            if not np.isfinite((time, *displacement)).all() or time < previous_time:
                raise ValueError("Ungültige oder unsortierte Verschiebungsdaten.")
            previous_time = time
            frame = frames.setdefault(time, {})
            if node in frame:
                raise ValueError("Doppelter Knoten in einem Zeitstand.")
            frame[node] = displacement
    if len(frames) < 2:
        raise ValueError("Zu wenige Solverstände für eine Animation.")
    expected = set(mesh.node_ids)
    if any(set(frame) != expected for frame in frames.values()):
        raise ValueError("Ein Zeitstand enthält nicht alle Netzknoten.")
    return DeformationHistory(np.asarray(list(frames)),
                              np.asarray([[frame[node] for node in mesh.node_ids] for frame in frames.values()]))
