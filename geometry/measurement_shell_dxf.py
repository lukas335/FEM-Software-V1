"""Import a constrained SolidWorks 2-D measurement-shell sketch from DXF.

The importer intentionally supports only the entities needed by the project:
LINE, ARC, POINT and CIRCLE.  It does not depend on a CAD installation or on
``ezdxf``; this keeps the simulation-side import reproducible.  SolidWorks
can export the same geometry as ASCII DXF.

Layer convention
----------------
``MS_CONSTRUCTION`` and ``MS_AXIS`` are ignored for material regions.
``MS_SENSOR_TIP`` must contain exactly one POINT or CIRCLE.  All other
non-construction line/arc components are candidate closed regions.  A JSON
sidecar may override units, materials and the sensor marker.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_TOLERANCE_MM = 1e-3
SENSOR_LAYER = "MS_SENSOR_TIP"
AXIS_LAYER = "MS_AXIS"
CONSTRUCTION_LAYER = "MS_CONSTRUCTION"


class DxfImportError(ValueError):
    """A human-readable validation/import error."""


@dataclass(frozen=True)
class DxfLine:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class DxfArc:
    layer: str
    cx: float
    cy: float
    radius: float
    start_deg: float
    end_deg: float


@dataclass(frozen=True)
class DxfPoint:
    layer: str
    x: float
    y: float


def _pairs(text: str) -> list[tuple[int, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if len(lines) % 2: raise DxfImportError("DXF enthält eine unvollständige Gruppe.")
    result = []
    for i in range(0, len(lines), 2):
        try: code = int(lines[i].strip())
        except ValueError as exc: raise DxfImportError(f"Ungültiger DXF-Gruppencode: {lines[i]!r}") from exc
        result.append((code, lines[i + 1].strip()))
    return result


def _float(entity: dict[int, str], code: int, name: str) -> float:
    try: return float(entity[code])
    except (KeyError, ValueError) as exc: raise DxfImportError(f"DXF-Entity benötigt {name} ({code}).") from exc


def parse_ascii_dxf(text: str) -> tuple[list[DxfLine], list[DxfArc], list[DxfPoint], dict[str, str]]:
    """Parse the supported ASCII DXF subset and return entities plus header."""
    pairs = _pairs(text); lines: list[DxfLine] = []; arcs: list[DxfArc] = []; points: list[DxfPoint] = []; header: dict[str, str] = {}
    section = None; entity: dict[int, str] | None = None; entity_type = None

    def consume(kind, values):
        if not values: return
        layer = values.get(8, "0")
        if kind == "LINE": lines.append(DxfLine(layer, _float(values, 10, "X1"), _float(values, 20, "Y1"), _float(values, 11, "X2"), _float(values, 21, "Y2")))
        elif kind == "ARC":
            radius = _float(values, 40, "Radius")
            if radius <= 0: raise DxfImportError("Ein DXF-Bogen hat einen ungültigen Radius.")
            arcs.append(DxfArc(layer, _float(values, 10, "Center X"), _float(values, 20, "Center Y"), radius, _float(values, 50, "Startwinkel"), _float(values, 51, "Endwinkel")))
        elif kind == "POINT": points.append(DxfPoint(layer, _float(values, 10, "X"), _float(values, 20, "Y")))
        elif kind == "CIRCLE": points.append(DxfPoint(layer, _float(values, 10, "Center X"), _float(values, 20, "Center Y")))

    for code, value in pairs:
        if code == 0:
            if section == "ENTITIES" and entity_type is not None:
                consume(entity_type, entity or {})
                entity_type, entity = None, None
            if value == "SECTION": section = None
            elif value in ("ENTITIES", "HEADER"): section = value
            elif value == "ENDSEC": section = None
            elif value == "EOF": break
            elif section == "ENTITIES": entity_type, entity = value, {}
        elif section == "ENTITIES" and entity is not None:
            entity[code] = value
        elif section == "HEADER":
            if code == 9: header["pending"] = value
            elif "pending" in header:
                header[header.pop("pending")] = value
        elif section is None and code == 2:
            # The section name follows ``0 SECTION`` in a DXF stream.
            if value in ("ENTITIES", "HEADER"): section = value
    if section == "ENTITIES": consume(entity_type, entity or {})
    return lines, arcs, points, header


def _unit_scale(header: dict[str, str], units: str | None) -> float:
    if units:
        name = units.lower()
        return {"mm": 1.0, "millimeter": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4, "in": 25.4}.get(name, 1.0)
    # DXF $INSUNITS: 4 = millimeters, 5 = centimeters, 6 = meters, 1 = inches.
    code = header.get("$INSUNITS", "4")
    return {"1": 25.4, "4": 1.0, "5": 10.0, "6": 1000.0}.get(code, 1.0)


def _key(x: float, y: float, tolerance: float) -> tuple[int, int]:
    return (round(x / tolerance), round(y / tolerance))


def _angle_point(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _sidecar(path: str | Path | None) -> dict[str, Any]:
    if not path: return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def import_dxf(text_or_path: str | Path, definition: dict[str, Any] | None = None,
              tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> dict[str, Any]:
    """Convert a DXF string/path into the project's versioned sketch JSON."""
    if tolerance_mm <= 0: raise ValueError("tolerance_mm muss positiv sein.")
    text = Path(text_or_path).read_text(encoding="utf-8") if isinstance(text_or_path, Path) or "\n" not in str(text_or_path) else str(text_or_path)
    definition = definition or {}
    lines, arcs, marker_points, header = parse_ascii_dxf(text)
    scale = _unit_scale(header, definition.get("units"))
    tolerance = tolerance_mm
    points: list[dict[str, Any]] = []; point_by_key: dict[tuple[int, int], str] = {}; next_point = 1

    def point_id(x, y):
        nonlocal next_point
        x, y = x * scale, y * scale; key = _key(x, y, tolerance)
        if key in point_by_key: return point_by_key[key]
        ident = f"p{next_point:03d}"; next_point += 1; point_by_key[key] = ident
        points.append({"id": ident, "x": x, "y": y}); return ident

    elements: list[dict[str, Any]] = []; edges: list[tuple[str, str, str, str]] = []; next_line = 1; next_arc = 1
    for item in lines:
        start, end = point_id(item.x1, item.y1), point_id(item.x2, item.y2)
        ident = f"L{next_line:03d}"; next_line += 1
        elements.append({"id": ident, "kind": "line", "start_point": start, "end_point": end, "layer": item.layer,
                         "construction": item.layer in (CONSTRUCTION_LAYER, AXIS_LAYER)})
        if item.layer not in (CONSTRUCTION_LAYER, AXIS_LAYER, SENSOR_LAYER): edges.append((ident, start, end, item.layer))
    for item in arcs:
        center = point_id(item.cx, item.cy); sx, sy = _angle_point(item.cx, item.cy, item.radius, item.start_deg); ex, ey = _angle_point(item.cx, item.cy, item.radius, item.end_deg)
        start, end = point_id(sx, sy), point_id(ex, ey); ident = f"A{next_arc:03d}"; next_arc += 1
        elements.append({"id": ident, "kind": "arc", "center_point": center, "start_point": start, "end_point": end,
                         "radius": item.radius * scale, "start_angle_deg": item.start_deg, "end_angle_deg": item.end_deg, "layer": item.layer,
                         "construction": item.layer in (CONSTRUCTION_LAYER, AXIS_LAYER)})
        if item.layer not in (CONSTRUCTION_LAYER, AXIS_LAYER, SENSOR_LAYER): edges.append((ident, start, end, item.layer))

    sensor_candidates = [p for p in marker_points if p.layer == SENSOR_LAYER]
    explicit_sensor = definition.get("sensor_tip", {}).get("x") is not None if isinstance(definition.get("sensor_tip"), dict) else False
    if explicit_sensor:
        sensor = point_id(float(definition["sensor_tip"]["x"]), float(definition["sensor_tip"]["y"]))
    elif len(sensor_candidates) == 1:
        sensor = point_id(sensor_candidates[0].x, sensor_candidates[0].y)
    elif not sensor_candidates:
        raise DxfImportError("Sensorspitze fehlt: genau ein Punkt/Kreis auf Layer MS_SENSOR_TIP erwartet.")
    else: raise DxfImportError(f"Sensorspitze ist nicht eindeutig: {len(sensor_candidates)} Marker gefunden.")

    adjacency: dict[str, list[tuple[str, str, str]]] = {}
    for ident, start, end, layer in edges:
        adjacency.setdefault(start, []).append((ident, end, layer)); adjacency.setdefault(end, []).append((ident, start, layer))
    unvisited = {edge[0] for edge in edges}; regions: list[dict[str, Any]] = []; region_number = 1

    def ordered_boundary(component_edges):
        by_id = {edge[0]: edge for edge in component_edges}
        first_id, first_start, first_end, _ = component_edges[0]
        ordered = [first_id]; used = {first_id}; start_node = first_start; current = first_end
        while current != start_node:
            candidates = [(edge_id, other, layer) for edge_id, other, layer in adjacency[current]
                          if edge_id in by_id and edge_id not in used]
            if len(candidates) != 1:
                raise DxfImportError("Kontur lässt sich nicht eindeutig umlaufen.")
            edge_id, other, _ = candidates[0]; ordered.append(edge_id); used.add(edge_id); current = other
        if len(used) != len(component_edges): raise DxfImportError("Kontur enthält eine nicht verbundene Teilkante.")
        return ordered

    while unvisited:
        seed = next(iter(unvisited)); component = []; stack = [seed]
        while stack:
            current = stack.pop()
            if current not in unvisited: continue
            unvisited.remove(current); component.append(current)
            edge = next(edge for edge in edges if edge[0] == current)
            for node in edge[1:3]:
                for neighbour, _, _ in adjacency.get(node, []):
                    if neighbour in unvisited: stack.append(neighbour)
        component_edges = [edge for edge in edges if edge[0] in component]; nodes = {node for edge in component_edges for node in edge[1:3]}
        degree = {node: sum(node in edge[1:3] for edge in component_edges) for node in nodes}
        if len(component_edges) < 3 or any(value != 2 for value in degree.values()):
            raise DxfImportError("Nicht jede Profilkontur ist geschlossen; Endpunkte und Layer prüfen.")
        layer = component_edges[0][3]; material = definition.get("default_material", "silicone_A30")
        for item in definition.get("regions", []):
            if item.get("layer") == layer: material = item.get("material", material)
        if material not in MATERIALS: raise DxfImportError(f"Unbekanntes Material für Layer {layer}: {material}")
        regions.append({"id": f"region_{region_number:03d}", "name": layer, "material": material,
                        "boundary": ordered_boundary(component_edges), "connected_to": []}); region_number += 1

    if not regions:
        raise DxfImportError("Keine geschlossene Messschalenkontur gefunden.")

    axis_entities = [e["id"] for e in elements if e.get("layer") == AXIS_LAYER]
    return {"version": 2, "source": {"format": "dxf", "units": "mm", "scale": scale},
            "parameters": definition.get("parameters", {}),
            "geometry": {"points": points, "elements": elements, "constraints": [], "dimensions": [], "regions": regions},
            "metadata": {"sensor_tip_point": sensor, "axis_entities": axis_entities, "tolerance_mm": tolerance}}


def import_dxf_file(path: str | Path, definition_path: str | Path | None = None,
                    tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> dict[str, Any]:
    return import_dxf(Path(path), _sidecar(definition_path), tolerance_mm)
