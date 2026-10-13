from __future__ import annotations

from preprocessor_common import *


class GeometryMixin:
        def _arc_contains(self, element, angle):
            center = self.point(element["center_point"])
            start = self.point(element["start_point"]); end = self.point(element["end_point"])
            a0 = math.degrees(math.atan2(start["y"]-center["y"], start["x"]-center["x"])) % 360
            a1 = math.degrees(math.atan2(end["y"]-center["y"], end["x"]-center["x"])) % 360
            value = angle % 360
            sweep = float(element.get("sweep_angle", (a1 - a0) % 360))
            if sweep >= 0:
                travelled = (value - a0) % 360
                return travelled <= abs(sweep) + 1e-7
            travelled = (a0 - value) % 360
            return travelled <= abs(sweep) + 1e-7

        def _intersections(self, first, second):
            """Return valid line/arc intersections as (x, y)."""
            def endpoints(entity):
                return self.point(entity["start_point"]), self.point(entity["end_point"])
            def segment_intersection(a, b, c, d):
                den = (a["x"]-b["x"]) * (c["y"]-d["y"]) - (a["y"]-b["y"]) * (c["x"]-d["x"])
                if abs(den) < 1e-12: return []
                t = ((a["x"]-c["x"]) * (c["y"]-d["y"]) - (a["y"]-c["y"]) * (c["x"]-d["x"])) / den
                u = -((a["x"]-b["x"]) * (a["y"]-c["y"]) - (a["y"]-b["y"]) * (a["x"]-c["x"])) / den
                if -1e-8 <= t <= 1+1e-8 and -1e-8 <= u <= 1+1e-8:
                    return [(a["x"] + t*(b["x"]-a["x"]), a["y"] + t*(b["y"]-a["y"]))]
                return []
            def circle_line(line, arc):
                p, q = endpoints(line); center = self.point(arc["center_point"]); dx, dy = q["x"]-p["x"], q["y"]-p["y"]
                fx, fy = p["x"]-center["x"], p["y"]-center["y"]; aa = dx*dx + dy*dy
                bb = 2*(fx*dx + fy*dy); cc = fx*fx + fy*fy - arc["radius"]**2; disc = bb*bb - 4*aa*cc
                if disc < -1e-9: return []
                values = [(-bb + sign*math.sqrt(max(0, disc)))/(2*aa) for sign in (1, -1)] if aa else []
                result = []
                for t in values:
                    if -1e-8 <= t <= 1+1e-8:
                        x, y = p["x"] + t*dx, p["y"] + t*dy; angle = math.degrees(math.atan2(y-center["y"], x-center["x"]))
                        if self._arc_contains(arc, angle) and not any(math.hypot(x-r[0], y-r[1]) < 1e-7 for r in result): result.append((x, y))
                return result
            if first["kind"] == "line" and second["kind"] == "line":
                return segment_intersection(*endpoints(first), *endpoints(second))
            if first["kind"] == "line" and second["kind"] == "arc": return circle_line(first, second)
            if first["kind"] == "arc" and second["kind"] == "line": return circle_line(second, first)
            c1, c2 = self.point(first["center_point"]), self.point(second["center_point"]); dx, dy = c2["x"]-c1["x"], c2["y"]-c1["y"]; distance = math.hypot(dx, dy)
            if distance <= 1e-12 or distance > first["radius"]+second["radius"]+1e-8 or distance < abs(first["radius"]-second["radius"])-1e-8: return []
            base = (first["radius"]**2 - second["radius"]**2 + distance**2)/(2*distance); height = math.sqrt(max(0, first["radius"]**2-base**2)); bx, by = c1["x"]+base*dx/distance, c1["y"]+base*dy/distance
            candidates = [(bx-height*dy/distance, by+height*dx/distance), (bx+height*dy/distance, by-height*dx/distance)]
            return [(x, y) for x, y in candidates if self._arc_contains(first, math.degrees(math.atan2(y-c1["y"], x-c1["x"]))) and self._arc_contains(second, math.degrees(math.atan2(y-c2["y"], x-c2["x"]))) ]

        def _element_parameter(self, element, x, y):
            start = self.point(element["start_point"]); end = self.point(element["end_point"])
            if element["kind"] == "line":
                dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                length2 = dx * dx + dy * dy
                return ((x - start["x"]) * dx + (y - start["y"]) * dy) / length2 if length2 else 0.0
            center = self.point(element["center_point"])
            a0 = math.degrees(math.atan2(start["y"] - center["y"], start["x"] - center["x"]))
            angle = math.degrees(math.atan2(y - center["y"], x - center["x"]))
            sweep = float(element.get("sweep_angle", 0.0))
            travelled = ((angle - a0) % 360.0) if sweep >= 0 else ((a0 - angle) % 360.0)
            return travelled / max(abs(sweep), 1e-12)

        def _point_at(self, x, y, tolerance=1e-7):
            existing = next((point for point in self.points if math.hypot(point["x"] - x, point["y"] - y) <= tolerance), None)
            return existing["id"] if existing else self.add_point(x, y)

        def _split_element_at(self, element_id, x, y, point_id):
            element = self.element(element_id)
            if element is None: return False
            parameter = self._element_parameter(element, x, y)
            if parameter <= 1e-7 or parameter >= 1.0 - 1e-7: return False
            if element["kind"] == "line":
                old_end = element["end_point"]
                new_element = dict(element)
                new_element["id"] = self._next_id("L")
                new_element["start_point"] = point_id
                new_element["end_point"] = old_end
                element["end_point"] = point_id
                element["orientation"] = "horizontal" if abs(self.point(element["start_point"])["y"] - self.point(point_id)["y"]) <= 1e-7 else ("vertical" if abs(self.point(element["start_point"])["x"] - self.point(point_id)["x"]) <= 1e-7 else "free")
                new_element["orientation"] = "horizontal" if abs(self.point(point_id)["y"] - self.point(old_end)["y"]) <= 1e-7 else ("vertical" if abs(self.point(point_id)["x"] - self.point(old_end)["x"]) <= 1e-7 else "free")
                self.elements.append(new_element)
                self.remap_split_boundary(element_id, new_element['id'])
                return True
            old_end = element["end_point"]
            sweep = float(element.get("sweep_angle", 0.0))
            new_element = dict(element)
            new_element["id"] = self._next_id("A")
            family = element.get('circle_id')
            if not family:
                existing_families = {item.get('circle_id') for item in self.elements}
                number = 1
                while f'circle_{number:03d}' in existing_families:
                    number += 1
                family = f'circle_{number:03d}'
                element['circle_id'] = family
            new_element['circle_id'] = family
            new_element["start_point"] = point_id
            new_element["end_point"] = old_end
            new_element["sweep_angle"] = sweep * (1.0 - parameter)
            element["end_point"] = point_id
            element["sweep_angle"] = sweep * parameter
            self.elements.append(new_element)
            self.remap_split_boundary(element_id, new_element['id'])
            return True

        def trim(self, element_id, x, y):
            """Trim the clicked side up to the nearest real line/arc intersection."""
            target = self.element(element_id)
            if target is None: raise ValueError("Trim-Element nicht gefunden.")
            intersections = []
            for other in self.elements:
                if other["id"] != element_id: intersections.extend(self._intersections(target, other))
            if not intersections: raise ValueError("Keine Kreuzung zum Trimmen gefunden.")
            start, end = self.point(target["start_point"]), self.point(target["end_point"])

            def parameter(px, py):
                if target["kind"] == "line":
                    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                    length2 = dx * dx + dy * dy
                    return ((px - start["x"]) * dx + (py - start["y"]) * dy) / length2 if length2 else 0.0
                center = self.point(target["center_point"])
                a0 = math.degrees(math.atan2(start["y"] - center["y"], start["x"] - center["x"]))
                angle = math.degrees(math.atan2(py - center["y"], px - center["x"]))
                sweep = float(target.get("sweep_angle", 0.0))
                travelled = ((angle - a0) % 360.0) if sweep >= 0 else ((a0 - angle) % 360.0)
                return travelled / max(abs(sweep), 1e-12)

            click_parameter = parameter(x, y)
            ix, iy = min(intersections, key=lambda point: abs(parameter(point[0], point[1]) - click_parameter))
            intersection_parameter = parameter(ix, iy)
            if abs(intersection_parameter - click_parameter) < 1e-6:
                raise ValueError("Bitte auf den zu trimmenden Abschnitt klicken.")
            key = "start_point" if click_parameter < intersection_parameter else "end_point"
            original_sweep = float(target.get("sweep_angle", 0.0)) if target["kind"] == "arc" else None
            if target["kind"] == "arc":
                center = self.point(target["center_point"])
                radial = math.hypot(ix - center["x"], iy - center["y"])
                ix = center["x"] + target["radius"] * (ix - center["x"]) / max(radial, 1e-12)
                iy = max(0.0, center["y"] + target["radius"] * (iy - center["y"]) / max(radial, 1e-12))
            existing = next((point for point in self.points if math.hypot(point["x"] - ix, point["y"] - iy) < 1e-7), None)
            new_id = existing["id"] if existing else self.add_point(ix, iy)
            crossed_ids = [other["id"] for other in list(self.elements)
                           if other["id"] != element_id and self.distance_to_element(other, ix, iy) <= 1e-6]
            for crossed_id in crossed_ids:
                self._split_element_at(crossed_id, ix, iy, new_id)
            target[key] = new_id
            if target["kind"] == "line":
                new_start = self.point(target["start_point"]); new_end = self.point(target["end_point"])
                target["orientation"] = "horizontal" if abs(new_start["y"] - new_end["y"]) <= 1e-7 else ("vertical" if abs(new_start["x"] - new_end["x"]) <= 1e-7 else "free")
            if target["kind"] == "arc":
                center = self.point(target["center_point"])
                start = self.point(target["start_point"]); end = self.point(target["end_point"])
                target["sweep_angle"] = self._arc_sweep_for_points(center, start, end, original_sweep)
                if (target['start_point'] == target['end_point']
                        or abs(target['sweep_angle']) <= GEOMETRY_TOLERANCE):
                    self.delete_ids([target['id']])
            return new_id

        def distance_to_element(self, element, x, y):
            """Geometric hit-test distance in model units, independent of paint order."""
            start, end = self.point(element["start_point"]), self.point(element["end_point"])
            if element["kind"] == "line":
                dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                length2 = dx * dx + dy * dy
                t = max(0.0, min(1.0, ((x - start["x"]) * dx + (y - start["y"]) * dy) / length2)) if length2 else 0.0
                return math.hypot(x - (start["x"] + t * dx), y - (start["y"] + t * dy))
            center = self.point(element["center_point"])
            radius = float(element["radius"]); radial = math.hypot(x - center["x"], y - center["y"])
            angle = math.degrees(math.atan2(y - center["y"], x - center["x"]))
            if self._arc_contains(element, angle): return abs(radial - radius)
            return min(math.hypot(x - start["x"], y - start["y"]), math.hypot(x - end["x"], y - end["y"]))

        def delete_ids(self, ids):
            ids = set(ids)
            self.points[:] = [p for p in self.points if p["id"] not in ids]
            self.elements[:] = [e for e in self.elements if e["id"] not in ids]
            self.constraints[:] = [c for c in self.constraints
                                   if not ids.intersection(c.get("references", [])) and c["id"] not in ids]
            self.dimensions[:] = [d for d in self.dimensions
                                  if not ids.intersection(d.get("references", [])) and d["id"] not in ids]

        def validate(self, include_regions=True) -> list[str]:
            errors: list[str] = []
            all_ids = []
            for group in self.geometry.values():
                if isinstance(group, list): all_ids.extend(item.get("id") for item in group)
            if len(all_ids) != len(set(all_ids)): errors.append("IDs müssen eindeutig sein.")
            point_ids = {p["id"] for p in self.points}
            for p in self.points:
                if not all(math.isfinite(float(p.get(k, math.nan))) for k in ("x", "y")):
                    errors.append(f"{p.get('id')}: ungültige Punktkoordinaten.")
            for p in self.points:
                if float(p.get("y", 0)) < -1e-7: errors.append(f"{p.get('id')}: Geometrie unterhalb der x-Achse.")
            for e in self.elements:
                refs = [e.get("start_point"), e.get("end_point")]
                if e["kind"] == "arc": refs.append(e.get("center_point"))
                if any(ref not in point_ids for ref in refs): errors.append(f"{e['id']}: fehlender Punktbezug.")
                if e["kind"] == "line":
                    a, b = self.point(e["start_point"]), self.point(e["end_point"])
                    if a and b and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= 1e-9: errors.append(f"{e['id']}: Linie ist zu kurz.")
                if e["kind"] == "arc":
                    if float(e.get("radius", 0)) <= 0: errors.append(f"{e['id']}: Radius muss positiv sein.")
                    center = self.point(e.get("center_point")); start = self.point(e.get("start_point")); end = self.point(e.get("end_point"))
                    if start and end and (start["y"] < -1e-7 or end["y"] < -1e-7): errors.append(f"{e['id']}: Bogen liegt unterhalb der x-Achse.")
                    if center and start and end and float(e.get("radius", 0)) > 0:
                        radius = float(e["radius"])
                        start_radius = math.hypot(start["x"] - center["x"], start["y"] - center["y"])
                        end_radius = math.hypot(end["x"] - center["x"], end["y"] - center["y"])
                        if abs(start_radius - radius) > GEOMETRY_TOLERANCE or abs(end_radius - radius) > GEOMETRY_TOLERANCE:
                            errors.append(f"{e['id']}: Bogenendpunkte liegen nicht auf dem gespeicherten Radius.")
                        if math.hypot(start["x"] - end["x"], start["y"] - end["y"]) <= GEOMETRY_TOLERANCE or abs(float(e.get("sweep_angle", 0.0))) <= GEOMETRY_TOLERANCE:
                            errors.append(f"{e['id']}: degenerierter Kreisbogen.")
                        else:
                            expected = self._arc_sweep_for_points(center, start, end, e.get("sweep_angle", 1.0))
                            if abs(float(e.get("sweep_angle", 0.0)) - expected) > GEOMETRY_TOLERANCE:
                                errors.append(f"{e['id']}: Sweep-Winkel passt nicht zu den Bogenendpunkten.")
                            if (self._arc_contains(e, 270.0)
                                    and center['y'] - radius < -GEOMETRY_TOLERANCE):
                                errors.append(f"{e['id']}: Bogen verlässt die obere Halbschale.")
                if e.get("kind") == "line":
                    start = self.point(e["start_point"]); end = self.point(e["end_point"])
                    e["orientation"] = "horizontal" if abs(start["y"] - end["y"]) <= 1e-7 else ("vertical" if abs(start["x"] - end["x"]) <= 1e-7 else "free")
            for constraint in self.constraints:
                if not constraint.get('driving', True):
                    continue
                if constraint.get("type") == "coincident":
                    refs = constraint.get("references", [])
                    if len(refs) != 2:
                        errors.append(f"{constraint.get('id', '?')}: ungültige Coincident-Referenz.")
                        continue
                    first_kind = self._coincident_reference_kind(refs[0])
                    second_kind = self._coincident_reference_kind(refs[1])
                    try:
                        if first_kind == "axis" or second_kind == "axis":
                            axis = refs[0] if first_kind == 'axis' else refs[1]
                            reference = refs[1] if first_kind == 'axis' else refs[0]
                            point = self.point(reference)
                            entity = self.element(reference)
                            if point:
                                points = [point]
                            elif entity and entity['kind'] == 'line':
                                points = [self.point(entity[key]) for key in ('start_point', 'end_point')]
                            else:
                                raise ValueError('Ungültige Achsen-Coincident-Referenz.')
                            coordinate = 'y' if axis == 'x_axis' else 'x'
                            if any(abs(point[coordinate]) > GEOMETRY_TOLERANCE for point in points):
                                errors.append(f"{constraint.get('id', '?')}: Achsen-Coincident verletzt.")
                        elif first_kind == "point" and second_kind == "point":
                            first = self.point(refs[0]); second = self.point(refs[1])
                            if math.hypot(first["x"] - second["x"], first["y"] - second["y"]) > GEOMETRY_TOLERANCE:
                                errors.append(f"{constraint.get('id', '?')}: Punkt-Coincident verletzt.")
                        elif first_kind == "point" and second_kind == "element":
                            point = self.point(refs[0]); target = self._coincident_projection(point, self.element(refs[1]), constraint.get("mode") == "extension")
                            if math.hypot(point["x"] - target["x"], point["y"] - target["y"]) > GEOMETRY_TOLERANCE:
                                errors.append(f"{constraint.get('id', '?')}: Geometrie-Coincident verletzt.")
                        elif first_kind == "element" and second_kind == "point":
                            point = self.point(refs[1]); target = self._coincident_projection(point, self.element(refs[0]), constraint.get("mode") == "extension")
                            if math.hypot(point["x"] - target["x"], point["y"] - target["y"]) > GEOMETRY_TOLERANCE:
                                errors.append(f"{constraint.get('id', '?')}: Geometrie-Coincident verletzt.")
                        else:
                            errors.append(f"{constraint.get('id', '?')}: ungültige Coincident-Kombination.")
                    except (KeyError, TypeError, ValueError):
                        errors.append(f"{constraint.get('id', '?')}: ungültige Coincident-Referenz.")
                    continue
                if constraint.get("type") not in ("horizontal", "vertical"): continue
                for element_id in constraint.get("references", []):
                    element = self.element(element_id)
                    if element is None or element.get("kind") != "line":
                        errors.append(f"{constraint.get('id', '?')}: ungültige Linienreferenz."); continue
                    start = self.point(element["start_point"]); end = self.point(element["end_point"])
                    difference = abs(start["y"] - end["y"]) if constraint["type"] == "horizontal" else abs(start["x"] - end["x"])
                    if difference > GEOMETRY_TOLERANCE:
                        errors.append(f"{element['id']}: {constraint['type']}e Bedingung verletzt.")
            if not errors:
                try:
                    errors.extend(f'{item}: geometrische Beziehung verletzt.'
                                  for item in self.relation_violations())
                except (KeyError, TypeError, ValueError) as error:
                    errors.append(str(error))
            for name, p in self.data["parameters"].items():
                try: value = float(p["value"])
                except (KeyError, TypeError, ValueError): errors.append(f"Parameter {name}: ungültiger Wert."); continue
                if not math.isfinite(value): errors.append(f"Parameter {name}: Wert muss endlich sein.")
                if p.get("study_variable") and not p.get("values") and p.get("minimum") is None:
                    errors.append(f"Parameter {name}: values oder minimum/maximum fehlen.")
            for region in self.regions if include_regions else []:
                if region.get("material") not in MATERIALS: errors.append(f"Region {region.get('id')}: unbekanntes Material.")
                for element_id, behavior in region.get("boundary_motion", {}).items():
                    if element_id not in region.get("boundary", []) or behavior not in ("follow_translation", "free_contact"):
                        errors.append(f"Region {region.get('id')}: ungültiges Randverhalten.")
                for element_id, enabled in region.get('boundary_x_only', {}).items():
                    if element_id not in region.get('boundary', []) or not isinstance(enabled, bool):
                        errors.append(f"Region {region.get('id')}: ungültige X-Führung.")
            if include_regions:
                errors.extend(self.region_validation_errors())
            return errors
