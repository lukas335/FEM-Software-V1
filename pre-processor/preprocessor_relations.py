from __future__ import annotations

from preprocessor_common import *


class RelationMixin:
        def _coincident_element_points(self, element):
            ids = [element.get("start_point"), element.get("end_point")]
            if element.get("kind") == "arc": ids.append(element.get("center_point"))
            return [point_id for point_id in ids if self.point(point_id) is not None]

        def _coincident_projection(self, point, element, extension=False):
            if element.get("kind") == "line":
                start = self.point(element["start_point"]); end = self.point(element["end_point"])
                dx, dy = end["x"] - start["x"], end["y"] - start["y"]
                length2 = dx * dx + dy * dy
                if length2 <= 1e-12: raise ValueError("Coincident kann keine kurze Linie verwenden.")
                t = ((point["x"] - start["x"]) * dx + (point["y"] - start["y"]) * dy) / length2
                if not extension: t = max(0.0, min(1.0, t))
                return {"x": start["x"] + t * dx, "y": start["y"] + t * dy}
            center = self.point(element["center_point"]); radius = float(element["radius"])
            dx, dy = point["x"] - center["x"], point["y"] - center["y"]
            distance = math.hypot(dx, dy)
            if distance <= 1e-12: raise ValueError("Coincident kann einen Punkt im Bogenmittelpunkt nicht projizieren.")
            candidate = {"x": center["x"] + radius * dx / distance,
                         "y": center["y"] + radius * dy / distance}
            if extension or self._arc_contains(element, math.degrees(math.atan2(candidate["y"] - center["y"], candidate["x"] - center["x"]))):
                return candidate
            endpoints = [self.point(element["start_point"]), self.point(element["end_point"])]
            return min(endpoints, key=lambda item: math.hypot(item["x"] - point["x"], item["y"] - point["y"]))

        def _driving_dimension_violations(self, tolerance=1e-5):
            """Return driving dimensions whose current geometry no longer matches."""
            from preprocessor_solver import SketchEquations
            equations = SketchEquations(self, build=False)
            violations = []
            for dimension in self.dimensions:
                if not dimension.get('driving', True):
                    continue
                try:
                    actual = equations.dimension_value(dimension)
                except (KeyError, TypeError, ValueError):
                    violations.append(dimension.get('id', '?'))
                    continue
                if abs(float(actual) - float(dimension.get("value", 0.0))) > tolerance:
                    violations.append(dimension.get("id", "?"))
            return violations


        def _merge_contact_points(self, source_id, target_id):
            """Use one canonical point ID for a connected tangent junction."""
            if source_id == target_id:
                return
            for element in self.elements:
                for key in ('start_point', 'end_point', 'center_point'):
                    if element.get(key) == source_id:
                        element[key] = target_id
            for item in self.constraints + self.dimensions:
                item['references'] = [target_id if ref == source_id else ref
                                      for ref in item.get('references', [])]

        def apply_tangency(self, references, add_constraint=True):
            before = self.clone_data()
            try:
                if len(references) != 2:
                    raise ValueError('Tangential benötigt zwei Elemente.')
                items = [self.element(ref) for ref in references]
                line = next((item for item in items if item and item['kind'] == 'line'), None)
                arc = next((item for item in items if item and item['kind'] == 'arc'), None)
                if line is None or arc is None:
                    raise ValueError('Tangential benötigt eine Linie und einen Kreisbogen.')
                shared = set((line['start_point'], line['end_point'])).intersection(
                    (arc['start_point'], arc['end_point']))
                if not shared:
                    pairs = [(line_id, arc_id) for line_id in (line['start_point'], line['end_point'])
                             for arc_id in (arc['start_point'], arc['end_point'])]
                    source_id, target_id = min(pairs, key=lambda pair: math.hypot(
                        self.point(pair[0])['x'] - self.point(pair[1])['x'],
                        self.point(pair[0])['y'] - self.point(pair[1])['y']))
                    self._merge_contact_points(source_id, target_id)
                if not any(item.get('type') == 'tangent' and
                           set(item.get('references', [])) == set(references)
                           for item in self.constraints):
                    self.add_constraint('tangent', list(references))
                self.solve_constraints()
            except Exception:
                self.replace_data(before)
                raise

        def apply_coincident(self, references, extension=False, add_constraint=True,
                             source_references=None, validate=True):
            before = self.clone_data()
            try:
                if len(references) != 2 or references[0] == references[1]:
                    raise ValueError('Coincident benötigt zwei unterschiedliche Referenzen.')
                kinds = [self._coincident_reference_kind(ref) for ref in references]
                if None in kinds:
                    raise ValueError('Coincident-Referenz fehlt.')
                selected_references = list(references)
                references = self._resolve_coincident_references(references, extension)
                if references != selected_references and source_references is None:
                    source_references = selected_references
                constraint = next((item for item in self.constraints
                                   if item.get('type') == 'coincident'
                                   and item.get('references') == list(references)), None)
                if constraint is None:
                    constraint_id = self.add_constraint('coincident', list(references))
                    constraint = next(item for item in self.constraints if item['id'] == constraint_id)
                constraint['mode'] = 'extension' if extension else 'segment'
                if source_references:
                    constraint['source_references'] = list(source_references)
                self.solve_constraints()
                return next((ref for ref in references if self.point(ref)), references[0])
            except Exception:
                self.replace_data(before)
                raise

        def solve_tangencies(self):
            self.solve_constraints()

        def _resolve_coincident_references(self, references, extension=False):
            """Resolve object picks without confusing a curve with its center."""
            if any(self.point(ref) for ref in references):
                return list(references)
            axis = next((ref for ref in references if ref in ('x_axis', 'y_axis')), None)
            entities = [self.element(ref) for ref in references if self.element(ref)]
            if axis:
                if len(entities) != 1:
                    raise ValueError('Coincident benötigt ein Element oder einen Punkt und eine Achse.')
                entity = entities[0]
                if entity['kind'] == 'line':
                    return [entity['id'], axis]
                coordinate = 'y' if axis == 'x_axis' else 'x'
                endpoint = min((entity['start_point'], entity['end_point']),
                               key=lambda ref: abs(self.point(ref)[coordinate]))
                return [endpoint, axis]
            if len(entities) != 2:
                raise ValueError('Ungültige Coincident-Kombination.')
            arc = next((entity for entity in entities if entity['kind'] == 'arc'), None)
            line = next((entity for entity in entities if entity['kind'] == 'line'), None)
            if arc and line:
                def distance(endpoint):
                    point = self.point(endpoint)
                    target = self._coincident_projection(point, line, extension)
                    return math.hypot(point['x'] - target['x'], point['y'] - target['y'])
                endpoint = min((arc['start_point'], arc['end_point']), key=distance)
                return [endpoint, line['id']]
            pairs = [(first, second)
                     for first in (entities[0]['start_point'], entities[0]['end_point'])
                     for second in (entities[1]['start_point'], entities[1]['end_point'])]
            return list(min(pairs, key=lambda pair: math.hypot(
                self.point(pair[0])['x'] - self.point(pair[1])['x'],
                self.point(pair[0])['y'] - self.point(pair[1])['y'])))

        def solve_coincident_constraints(self):
            self.solve_constraints()
