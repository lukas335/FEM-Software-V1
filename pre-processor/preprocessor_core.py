from __future__ import annotations

from preprocessor_common import *


class SketchCoreMixin:
        def __init__(self, data: dict[str, Any] | None = None):
            self.data = copy.deepcopy(data) if data else {
                "version": FORMAT_VERSION, "parameters": {},
                "geometry": {"points": [], "elements": [], "constraints": [],
                             "dimensions": [], "regions": [], "interfaces": []},
            }
            self.data.setdefault("version", FORMAT_VERSION)
            self.data.setdefault("parameters", {})
            geometry = self.data.setdefault("geometry", {})
            for key in ("points", "elements", "constraints", "dimensions", "regions", "interfaces"):
                geometry.setdefault(key, [])
            self.import_repairs = self.repair_legacy_geometry() if data else []
            if data and (self.regions or self.interfaces):
                try:
                    if self.ensure_regions_current():
                        self.import_repairs.append(
                            f'Veraltete Bereichskonturen und Interfaces aktualisiert: {len(self.regions)} geschlossene Bereiche.')
                except ValueError as error:
                    self.import_repairs.append(f'Bereichskonturen konnten nicht aktualisiert werden: {error}')

        @property
        def geometry(self): return self.data["geometry"]

        @property
        def points(self): return self.geometry["points"]

        @property
        def elements(self): return self.geometry["elements"]

        @property
        def constraints(self): return self.geometry["constraints"]

        @property
        def dimensions(self): return self.geometry["dimensions"]

        @property
        def regions(self): return self.geometry["regions"]

        @property
        def interfaces(self): return self.geometry["interfaces"]

        def clone_data(self): return copy.deepcopy(self.data)

        def replace_data(self, data): self.data = copy.deepcopy(data)

        def _next_id(self, prefix: str) -> str:
            all_ids = {item["id"] for group in self.geometry.values() if isinstance(group, list)
                       for item in group if isinstance(item, dict) and "id" in item}
            # Deleted measurement references must not silently attach to a
            # different point just because its numeric ID gets recycled.
            metadata = self.data.get('metadata', {})
            all_ids.update(metadata[key] for key in ('sensor_tip_point', 'alignment_point',
                           'sensor_tip_region', 'alignment_region') if metadata.get(key))
            for item in self.constraints + self.dimensions:
                all_ids.update(item.get('references', []))
                all_ids.update(item.get('source_references', []))
            for item in self.regions:
                all_ids.update(item.get('boundary', []))
            for item in self.interfaces:
                all_ids.update(item.get('boundary_a', []))
                all_ids.update(item.get('boundary_b', []))
            number = 1
            while f"{prefix}{number:03d}" in all_ids: number += 1
            return f"{prefix}{number:03d}"

        @staticmethod
        def _arc_sweep_for_points(center, start, end, preferred_sweep=None):
            start_angle = math.degrees(math.atan2(start["y"] - center["y"], start["x"] - center["x"]))
            end_angle = math.degrees(math.atan2(end["y"] - center["y"], end["x"] - center["x"]))
            if math.hypot(start["x"] - end["x"], start["y"] - end["y"]) <= GEOMETRY_TOLERANCE:
                return 0.0
            ccw = (end_angle - start_angle) % 360.0
            if preferred_sweep is not None and float(preferred_sweep) < 0.0:
                return ccw - 360.0 if ccw > GEOMETRY_TOLERANCE else -360.0
            return ccw if ccw > GEOMETRY_TOLERANCE else 360.0

        def add_parameter(self, name, value, unit="mm", study_variable=False,
                          values=None, minimum=None, maximum=None):
            if not str(name).strip(): raise ValueError("Parametername darf nicht leer sein.")
            if float(value) <= 0: raise ValueError("Parameterwert muss positiv sein.")
            self.data["parameters"][name] = asdict(Parameter(
                float(value), unit, "float", study_variable, values, minimum, maximum))

        def set_parameter(self, name, value):
            self.set_parameters({name: value})

        def set_parameters(self, values):
            """Apply a complete parameter change before solving the shared graph."""
            before = self.clone_data()
            try:
                for name, raw_value in values.items():
                    if name not in self.data['parameters']:
                        raise ValueError(f'Parameter {name} nicht gefunden.')
                    value = float(raw_value)
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError('Parameterwert muss positiv und endlich sein.')
                    linked = [dimension for dimension in self.dimensions
                              if dimension.get('parameter') == name and dimension.get('driving', True)]
                    if not linked:
                        raise ValueError(f'Parameter {name} ist mit keinem treibenden Maß verknüpft.')
                    self.data['parameters'][name]['value'] = value
                    for dimension in linked:
                        dimension['value'] = value
                self.solve_constraints()
            except Exception:
                self.replace_data(before)
                raise

        def link_dimension(self, dimension_id, parameter_name=None):
            dimension = next((item for item in self.dimensions if item["id"] == dimension_id), None)
            if dimension is None: raise ValueError("Bemaßung nicht gefunden.")
            if parameter_name and parameter_name not in self.data["parameters"]: raise ValueError("Parameter nicht gefunden.")
            dimension.pop("parameter", None) if not parameter_name else dimension.update({"parameter": parameter_name})
            if parameter_name: self.set_parameter(parameter_name, dimension["value"])

        def remove_parameter(self, name):
            if name not in self.data["parameters"]: return
            for dimension in self.dimensions:
                if dimension.get("parameter") == name: dimension.pop("parameter", None)
            del self.data["parameters"][name]

        def add_point(self, x, y, point_id=None):
            point_id = point_id or self._next_id("p")
            self.points.append({"id": point_id, "x": float(x), "y": float(y)})
            return point_id

        def add_line(self, start, end, element_id=None, construction=False):
            a, b = self.point(start), self.point(end)
            if a is None or b is None: raise ValueError("Linie benötigt gültige Punktreferenzen.")
            if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= 1e-9:
                raise ValueError("Linie benötigt zwei unterschiedliche Punkte.")
            horizontal = abs(a["y"] - b["y"]) <= 1e-7; vertical = abs(a["x"] - b["x"]) <= 1e-7
            element_id = element_id or self._next_id("L")
            self.elements.append({"id": element_id, "kind": "line",
                                  "start_point": start, "end_point": end,
                                  "orientation": "horizontal" if horizontal else ("vertical" if vertical else "free"),
                                  "construction": construction})
            return element_id

        def add_arc(self, center, start, end, radius, element_id=None, construction=False, sweep_angle=None):
            if radius <= 0: raise ValueError("Der Bogenradius muss positiv sein.")
            center_point = self.point(center); start_point = self.point(start); end_point = self.point(end)
            if center_point is None or start_point is None or end_point is None:
                raise ValueError("Bogen benötigt gültige Punktreferenzen.")
            if start_point["y"] < -1e-7 or end_point["y"] < -1e-7:
                raise ValueError("Kreisbögen dürfen nur in der oberen Halbschale liegen.")
            start_radius = math.hypot(start_point["x"] - center_point["x"], start_point["y"] - center_point["y"])
            end_radius = math.hypot(end_point["x"] - center_point["x"], end_point["y"] - center_point["y"])
            if abs(start_radius - float(radius)) > GEOMETRY_TOLERANCE or abs(end_radius - float(radius)) > GEOMETRY_TOLERANCE:
                raise ValueError("Bogenpunkte mÃ¼ssen exakt auf dem angegebenen Radius liegen.")
            if math.hypot(start_point["x"] - end_point["x"], start_point["y"] - end_point["y"]) <= GEOMETRY_TOLERANCE:
                raise ValueError("Ein Kreisbogen darf nicht auf demselben Start- und Endpunkt enden.")
            if sweep_angle is None:
                sweep_angle = default_arc_sweep(center_point, start_point, end_point)
            sweep_angle = self._arc_sweep_for_points(center_point, start_point, end_point, sweep_angle)
            start_angle = math.degrees(math.atan2(start_point["y"] - center_point["y"], start_point["x"] - center_point["x"]))
            sample_angles = [start_angle + float(sweep_angle) * index / 24.0 for index in range(25)]
            if min(center_point["y"] + radius * math.sin(math.radians(angle)) for angle in sample_angles) < -1e-7:
                raise ValueError("Der Kreisbogen verlässt die obere Halbschale.")
            element_id = element_id or self._next_id("A")
            self.elements.append({"id": element_id, "kind": "arc",
                                  "center_point": center, "start_point": start,
                                  "end_point": end, "radius": float(radius),
                                  "sweep_angle": float(sweep_angle),
                                  "construction": construction})
            return element_id

        def add_constraint(self, kind, references, value=None, constraint_id=None):
            item = {"id": constraint_id or self._next_id("C"), "type": kind,
                    "references": list(references), "driving": True}
            if value is not None: item["value"] = float(value)
            self.constraints.append(item)
            return item["id"]

        def _coincident_reference_kind(self, reference):
            if reference in ("x_axis", "y_axis"): return "axis"
            if self.point(reference) is not None: return "point"
            if self.element(reference) is not None: return "element"
            return None

        def point(self, point_id):
            return next((p for p in self.points if p["id"] == point_id), None)

        def element(self, element_id):
            return next((e for e in self.elements if e["id"] == element_id), None)

        def dimension(self, dimension_id):
            return next((d for d in self.dimensions if d["id"] == dimension_id), None)
