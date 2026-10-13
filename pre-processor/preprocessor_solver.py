"""Simultaneous sketch equations; coordinates and unmeasured radii stay free.

Only stored driving dimensions/relations and circle incidence are equations.
The previous coordinates select a nearby solution for an underdefined sketch;
they are a soft preference, never an extra geometric constraint.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares

from preprocessor_common import GEOMETRY_TOLERANCE


class SketchEquations:
    def __init__(self, document, build=True):
        self.document = document
        self.points = {point['id']: point for point in document.points}
        self.elements = {item['id']: item for item in document.elements}
        self.arcs = [item for item in document.elements if item['kind'] == 'arc']
        active = set()
        for item in document.elements:
            active.update(item[key] for key in ('start_point', 'end_point'))
            if item['kind'] == 'arc':
                active.add(item['center_point'])
        for item in document.constraints + document.dimensions:
            active.update(ref for ref in item.get('references', []) if ref in self.points)
        self.point_ids = [key for key in self.points if key in active]
        self.indices = {key: 2 * index for index, key in enumerate(self.point_ids)}
        self.radius_indices = {
            arc['id']: 2 * len(self.point_ids) + index for index, arc in enumerate(self.arcs)
        }
        self.line_contacts = {}
        contact_values = []
        for constraint in document.constraints:
            if (constraint.get('type') != 'coincident' or not constraint.get('driving', True)
                    or constraint.get('mode') == 'extension'):
                continue
            refs = constraint.get('references', [])
            point_id = next((ref for ref in refs if ref in self.indices), None)
            line = next((self.elements[ref] for ref in refs
                         if ref in self.elements and self.elements[ref]['kind'] == 'line'), None)
            if point_id is None or line is None:
                continue
            first = np.array([self.points[line['start_point']][axis] for axis in ('x', 'y')])
            second = np.array([self.points[line['end_point']][axis] for axis in ('x', 'y')])
            point = np.array([self.points[point_id][axis] for axis in ('x', 'y')])
            delta = second - first
            fraction = float(np.dot(point - first, delta)) / max(float(np.dot(delta, delta)), 1e-12)
            index = 2 * len(self.point_ids) + len(self.arcs) + len(contact_values)
            self.line_contacts[constraint['id']] = index
            contact_values.append(float(np.clip(fraction, 0.0, 1.0)))
        coordinates = [self.points[key][axis] for key in self.point_ids for axis in ('x', 'y')]
        self.initial = np.array(coordinates + [arc['radius'] for arc in self.arcs] + contact_values, dtype=float)
        self.scale = max(1.0, max((abs(value) for value in self.initial), default=1.0))
        self.equations = []
        self.labels = []
        if build:
            self._build()

    def point(self, values, point_id):
        index = self.indices[point_id]
        return values[index:index + 2]

    def radius(self, values, arc):
        return values[self.radius_indices[arc['id']]]

    def add(self, label, equation):
        self.labels.append(label)
        self.equations.append(equation)

    def _line_points(self, values, line):
        return self.point(values, line['start_point']), self.point(values, line['end_point'])

    def reference_point(self, values, reference):
        if reference in self.indices:
            return self.point(values, reference)
        element = self.elements.get(reference)
        if element is None:
            raise ValueError(f'Maßreferenz fehlt: {reference}')
        return self.point(values, element['start_point'])

    def dimension_value(self, dimension, values=None):
        values = self.initial if values is None else values
        refs = dimension.get('references', [])
        kind = dimension['type']
        if not refs:
            raise ValueError(f"{dimension['id']}: Maßreferenz fehlt.")
        element = self.elements.get(refs[0])
        if kind == 'radius' and element and element['kind'] == 'arc':
            return float(self.radius(values, element))
        if kind in ('point_horizontal', 'point_vertical'):
            point = self.reference_point(values, refs[0])
            return float(abs(point[0]) if kind == 'point_horizontal' else point[1])
        if kind in ('length', 'horizontal_distance', 'vertical_distance'):
            if len(refs) == 1 and element and element['kind'] == 'line':
                first, second = self._line_points(values, element)
            elif len(refs) == 2:
                first, second = [self.reference_point(values, ref) for ref in refs]
            else:
                raise ValueError(f"{dimension['id']}: ungültige Maßreferenzen.")
            if kind == 'length':
                return float(np.linalg.norm(second - first))
            axis = 0 if kind == 'horizontal_distance' else 1
            return float(abs(second[axis] - first[axis]))
        if kind == 'distance' and len(refs) == 2:
            axis = 0 if dimension.get('axis') == 'horizontal' else 1
            first, second = [self.reference_point(values, ref) for ref in refs]
            return float(abs(second[axis] - first[axis]))
        raise ValueError(f"{dimension['id']}: nicht unterstützter Maßtyp {kind}.")

    def _build(self):
        circle_families = {}
        for arc in self.arcs:
            for endpoint in set((arc['start_point'], arc['end_point'])):
                self.add(arc['id'], lambda v, arc=arc, endpoint=endpoint:
                         np.linalg.norm(self.point(v, endpoint) - self.point(v, arc['center_point']))
                         - self.radius(v, arc))
            family = arc.get('circle_id')
            if family in circle_families:
                other = circle_families[family]
                self.add(arc['id'], lambda v, arc=arc, other=other:
                         self.radius(v, arc) - self.radius(v, other))
            elif family:
                circle_families[family] = arc
        for constraint in self.document.constraints:
            if not constraint.get('driving', True):
                continue
            refs = constraint.get('references', [])
            label = constraint['id']
            kind = constraint['type']
            if kind in ('horizontal', 'vertical'):
                axis = 1 if kind == 'horizontal' else 0
                for ref in refs:
                    line = self.elements.get(ref)
                    if not line or line['kind'] != 'line':
                        raise ValueError(f'{label}: ungültige Linienreferenz {ref}.')
                    self.add(label, lambda v, line=line, axis=axis:
                             self._line_points(v, line)[1][axis] - self._line_points(v, line)[0][axis])
            elif kind == 'coincident':
                self._coincident(constraint)
            elif kind == 'tangent':
                self._tangent(constraint)
            else:
                raise ValueError(f'{label}: nicht unterstützte Beziehung {kind}.')
        for dimension in self.document.dimensions:
            if not dimension.get('driving', True):
                continue
            # Check references before invoking the optimizer, so an unsupported
            # measure can never be silently ignored by residual validation.
            self.dimension_value(dimension)
            self.add(dimension['id'], lambda v, d=dimension:
                     self.dimension_value(d, v) - float(d['value']))

    def _coincident(self, constraint):
        refs = constraint['references']
        label = constraint['id']
        if len(refs) != 2:
            raise ValueError(f'{label}: Coincident benötigt zwei Referenzen.')
        if 'x_axis' in refs or 'y_axis' in refs:
            axis_ref = next(ref for ref in refs if ref in ('x_axis', 'y_axis'))
            reference = next(ref for ref in refs if ref != axis_ref)
            entity = self.elements.get(reference)
            if reference in self.indices:
                point_ids = [reference]
            elif entity and entity['kind'] == 'line':
                point_ids = [entity['start_point'], entity['end_point']]
            else:
                raise ValueError(f'{label}: Achsen-Coincident benötigt Punkt oder Linie.')
            axis = 1 if axis_ref == 'x_axis' else 0
            for point_id in point_ids:
                self.add(label, lambda v, point_id=point_id: self.point(v, point_id)[axis])
            return
        if all(ref in self.indices for ref in refs):
            for axis in (0, 1):
                self.add(label, lambda v, axis=axis:
                         self.point(v, refs[0])[axis] - self.point(v, refs[1])[axis])
            return
        point_id = next((ref for ref in refs if ref in self.indices), None)
        target = next((self.elements[ref] for ref in refs if ref in self.elements), None)
        if point_id is None or target is None:
            raise ValueError(f'{label}: ungültige Coincident-Referenzen.')
        if target['kind'] == 'arc':
            self.add(label, lambda v: np.linalg.norm(
                self.point(v, point_id) - self.point(v, target['center_point'])) - self.radius(v, target))
        else:
            if label in self.line_contacts:
                index = self.line_contacts[label]
                for axis in (0, 1):
                    self.add(label, lambda v, axis=axis:
                             self.point(v, point_id)[axis] - (
                                 self._line_points(v, target)[0][axis] + v[index] * (
                                     self._line_points(v, target)[1][axis] -
                                     self._line_points(v, target)[0][axis])))
                return
            def line_distance(values):
                first, second = self._line_points(values, target)
                delta = second - first
                offset = self.point(values, point_id) - first
                return (delta[0] * offset[1] - delta[1] * offset[0]) / max(
                    float(np.linalg.norm(delta)), 1e-12)
            self.add(label, line_distance)

    def _tangent(self, constraint):
        items = [self.elements.get(ref) for ref in constraint['references']]
        line = next((item for item in items if item and item['kind'] == 'line'), None)
        arc = next((item for item in items if item and item['kind'] == 'arc'), None)
        if line is None or arc is None:
            raise ValueError(f"{constraint['id']}: Tangential benötigt Linie und Bogen.")
        shared = set((line['start_point'], line['end_point'])).intersection(
            (arc['start_point'], arc['end_point']))
        if not shared:
            raise ValueError(f"{constraint['id']}: Tangential benötigt einen gemeinsamen Kontaktpunkt.")
        contact = next(iter(shared))
        def dot_product(values):
            first, second = self._line_points(values, line)
            radial = self.point(values, contact) - self.point(values, arc['center_point'])
            return float(np.dot(second - first, radial)) / max(
                float(np.linalg.norm(second - first)), 1e-12)
        self.add(constraint['id'], dot_product)

    def residuals(self, values):
        return np.array([equation(values) for equation in self.equations], dtype=float)

    def violations(self, tolerance=GEOMETRY_TOLERANCE):
        return list(dict.fromkeys(label for label, residual in zip(
            self.labels, self.residuals(self.initial)) if abs(residual) > tolerance))

    def solve(self, preferences=None):
        if not len(self.initial):
            return
        lower = np.full(len(self.initial), -np.inf)
        upper = np.full(len(self.initial), np.inf)
        for point_id, index in self.indices.items():
            lower[index + 1] = 0.0
        for index in self.radius_indices.values():
            lower[index] = 10.0 * GEOMETRY_TOLERANCE
        for index in self.line_contacts.values():
            lower[index], upper[index] = 0.0, 1.0
        initial = np.minimum(np.maximum(self.initial, lower + 1e-12), upper - 1e-12)
        fixed_indices = set()
        for constraint in self.document.constraints:
            if constraint.get('type') != 'coincident' or not constraint.get('driving', True):
                continue
            refs = constraint.get('references', [])
            axis_ref = next((ref for ref in refs if ref in ('x_axis', 'y_axis')), None)
            if axis_ref is None:
                continue
            reference = next(ref for ref in refs if ref != axis_ref)
            entity = self.elements.get(reference)
            point_ids = [reference] if reference in self.indices else [
                entity['start_point'], entity['end_point']]
            axis = 1 if axis_ref == 'x_axis' else 0
            for point_id in point_ids:
                index = self.indices[point_id] + axis
                fixed_indices.add(index)
                initial[index] = 0.0
        free_indices = np.array([index for index in range(len(initial))
                                 if index not in fixed_indices], dtype=int)

        def expand(free_values):
            values = initial.copy()
            values[free_indices] = free_values
            return values

        preference = self.initial.copy()
        weights = np.ones(len(initial))
        for point_id, coordinates in (preferences or {}).items():
            index = self.indices[point_id]
            preference[index:index + 2] = coordinates
            weights[index:index + 2] = 5.0

        # Small regularization chooses continuity while equations dominate.
        # A second pass reduces this preference further if it obstructs accuracy.
        result = initial
        for strength in (1e-4, 1e-7):
            if not len(free_indices):
                break
            def objective(free_values):
                values = expand(free_values)
                return np.concatenate((self.residuals(values) / self.scale,
                                       strength * weights[free_indices] *
                                       (values - preference)[free_indices] / self.scale))
            optimization = least_squares(
                objective, result[free_indices], bounds=(lower[free_indices], upper[free_indices]),
                ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=300,
            )
            result = expand(optimization.x)
            if np.max(np.abs(self.residuals(result)), initial=0.0) <= GEOMETRY_TOLERANCE * 0.1:
                break
        residuals = self.residuals(result)
        failed = list(dict.fromkeys(label for label, value in zip(self.labels, residuals)
                                   if abs(value) > GEOMETRY_TOLERANCE))
        if failed:
            raise ValueError('Maßänderung widerspricht den gespeicherten Bedingungen: ' + ', '.join(failed))
        for point_id, index in self.indices.items():
            self.points[point_id]['x'] = float(result[index])
            self.points[point_id]['y'] = max(0.0, float(result[index + 1]))
        for arc in self.arcs:
            arc['radius'] = float(result[self.radius_indices[arc['id']]])
            arc['sweep_angle'] = self.document._arc_sweep_for_points(
                self.points[arc['center_point']], self.points[arc['start_point']],
                self.points[arc['end_point']], arc.get('sweep_angle', 1.0))
        self._check_segment_relations()

    def _check_segment_relations(self):
        for constraint in self.document.constraints:
            if (constraint.get('type') != 'coincident'
                    or not constraint.get('driving', True)
                    or constraint.get('mode') == 'extension'):
                continue
            refs = constraint['references']
            point = next((self.document.point(ref) for ref in refs if self.document.point(ref)), None)
            target = next((self.document.element(ref) for ref in refs if self.document.element(ref)), None)
            if point and target and self.document.distance_to_element(target, point['x'], point['y']) > GEOMETRY_TOLERANCE:
                raise ValueError(f"{constraint['id']}: Coincident liegt außerhalb des Kontursegments.")


class SolverMixin:
    def solve_constraints(self, preferences=None):
        before = self.clone_data()
        try:
            SketchEquations(self).solve(preferences)
            self._validate_after_dimension()
        except Exception:
            self.replace_data(before)
            raise

    def relation_violations(self):
        equations = SketchEquations(self)
        relation_ids = {constraint['id'] for constraint in self.constraints}
        return [label for label in equations.violations() if label in relation_ids]

    def constrain_line(self, line_id, orientation):
        if orientation not in ('horizontal', 'vertical'):
            raise ValueError('Unbekannte Linienrichtung.')
        line = self.element(line_id)
        if line is None or line.get('kind') != 'line':
            raise ValueError('Bitte eine Linie auswählen.')
        before = self.clone_data()
        try:
            self.constraints[:] = [item for item in self.constraints
                                   if not (line_id in item.get('references', [])
                                           and item['type'] in ('horizontal', 'vertical')
                                           and item['type'] != orientation)]
            if not any(item['type'] == orientation and line_id in item.get('references', [])
                       for item in self.constraints):
                self.add_constraint(orientation, [line_id])
            self.solve_constraints()
        except Exception:
            self.replace_data(before)
            raise

    def repair_legacy_geometry(self):
        """Repair only unambiguous editor artifacts in an imported copy.

        Never pin a point, angle, radius or unmeasured length.  Every correction
        is returned for display in the UI; the source JSON remains untouched.
        """
        repairs = []
        referenced_elements = {ref for item in self.constraints + self.dimensions
                               for ref in item.get('references', [])}
        referenced_elements.update(ref for region in self.regions for ref in region.get('boundary', []))
        for arc in list(self.elements):
            if (arc.get('kind') == 'arc' and arc['start_point'] == arc['end_point']
                    and abs(arc.get('sweep_angle', 0)) <= GEOMETRY_TOLERANCE
                    and arc['id'] not in referenced_elements):
                self.elements.remove(arc)
                repairs.append(f"{arc['id']}: leeren Bogenrest nach Trimmen entfernt.")
        active_points = {ref for element in self.elements for key in (
            'start_point', 'end_point', 'center_point') if (ref := element.get(key))}
        dimension_points = {ref for dimension in self.dimensions for ref in dimension.get('references', [])}
        for constraint in list(self.constraints):
            refs = constraint.get('references', [])
            if (constraint.get('type') == 'coincident' and constraint.get('source_references')
                    and len(refs) == 2 and all(self.point(ref) for ref in refs)
                    and not set(refs).intersection(active_points | dimension_points)):
                self.constraints.remove(constraint)
                repairs.append(f"{constraint['id']}: verwaisten Punktbezug nach Konturänderung entfernt.")
            if constraint.get('type') not in ('horizontal', 'vertical') or len(refs) != 1:
                continue
            line = self.element(refs[0])
            if not line or line['kind'] != 'line':
                continue
            first, second = self.point(line['start_point']), self.point(line['end_point'])
            measured_axis = next((d.get('axis') for d in self.dimensions
                                  if d.get('type') == 'length' and d.get('references') == refs
                                  and d.get('axis') in ('horizontal', 'vertical')), None)
            if measured_axis and measured_axis == line.get('orientation') and measured_axis != constraint['type']:
                axis = 'y' if measured_axis == 'horizontal' else 'x'
                if abs(first[axis] - second[axis]) <= GEOMETRY_TOLERANCE:
                    constraint['type'] = measured_axis
                    repairs.append(f"{constraint['id']}: {line['id']} auf {measured_axis} korrigiert "
                                   '(Punktlage, Linienrichtung und Maßrichtung stimmen überein).')
        return repairs

    def import_data(self, data):
        imported = type(self)(data)
        self.replace_data(imported.data)
        self.import_repairs = imported.import_repairs
        return list(self.import_repairs)
