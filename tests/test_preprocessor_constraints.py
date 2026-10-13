"""Regression coverage for real sketch equations and parameter transactions."""
import copy
import json
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from measurement_shell_prototype import CommandStack, MeasurementShellSketch
from preprocessor_solver import SketchEquations


FIXTURE = Path(__file__).parent / 'fixtures' / 'measurement_shelldynamic.json'


class ParameterConstraintTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def assert_consistent(self, document):
        self.assertEqual(document.validate(), [])
        self.assertEqual(document._driving_dimension_violations(), [])
        equations = SketchEquations(document)
        self.assertEqual(equations.violations(), [])
        for dimension in document.dimensions:
            self.assertAlmostEqual(equations.dimension_value(dimension), dimension['value'], places=6)
        for arc in (item for item in document.elements if item['kind'] == 'arc'):
            center = document.point(arc['center_point'])
            for key in ('start_point', 'end_point'):
                point = document.point(arc[key])
                self.assertAlmostEqual(math.hypot(point['x'] - center['x'], point['y'] - center['y']),
                                       arc['radius'], places=6)

    def test_import_repairs_only_identified_artifacts_without_changing_source(self):
        original = copy.deepcopy(self.raw)
        document = MeasurementShellSketch(self.raw)
        self.assertEqual(self.raw, original)
        self.assertEqual(len(document.import_repairs), 3)
        self.assertIsNone(document.element('A002'))
        self.assertFalse(any(c['id'] == 'C006' for c in document.constraints))
        self.assertEqual(next(c for c in document.constraints if c['id'] == 'C007')['type'], 'vertical')
        self.assertEqual(document.dimensions, self.raw['geometry']['dimensions'])
        self.assert_consistent(document)

    def test_every_parameter_changes_its_real_geometry_both_directions(self):
        for name, parameter in self.raw['parameters'].items():
            for factor in (0.8, 1.2):
                with self.subTest(parameter=name, factor=factor):
                    document = MeasurementShellSketch(self.raw)
                    original = document.clone_data()
                    value = parameter['value'] * factor
                    document.set_parameter(name, value)
                    self.assert_consistent(document)
                    self.assertEqual(document.constraints, original['geometry']['constraints'])
                    self.assertNotEqual(document.points, original['geometry']['points'])
                    for dimension in document.dimensions:
                        expected = value if dimension.get('parameter') == name else next(
                            d['value'] for d in original['geometry']['dimensions'] if d['id'] == dimension['id'])
                        self.assertAlmostEqual(dimension['value'], expected)
                    self.assertAlmostEqual(document.element('A003')['radius'],
                                           document.element('A005')['radius'], places=6)

    def test_every_dimension_changes_without_breaking_other_measures(self):
        for source in self.raw['geometry']['dimensions']:
            for factor in (0.9, 1.1):
                with self.subTest(dimension=source['id'], factor=factor):
                    document = MeasurementShellSketch(self.raw)
                    document.set_dimension(source['id'], source['value'] * factor)
                    self.assert_consistent(document)

    def test_parameter_sequence_and_history_restore_valid_geometry(self):
        document = MeasurementShellSketch(self.raw)
        history = CommandStack(document)
        original = document.clone_data()
        for name, value in (('ty', 4.0), ('tx', 3.0), ('r_sens', 2.5), ('R_outer', 9.0)):
            history.run(name, lambda name=name, value=value: document.set_parameter(name, value))
            self.assert_consistent(document)
        final = document.clone_data()
        for _ in range(4):
            history.undo()
        self.assertEqual(document.data, original)
        for _ in range(4):
            history.redo()
        self.assertEqual(document.data, final)
        self.assert_consistent(document)

    def test_study_variants_solve_the_geometry_for_all_selected_parameters(self):
        document = MeasurementShellSketch(self.raw)
        for name in ('ty', 'r_sens'):
            parameter = document.data['parameters'][name]
            parameter['study_variable'] = True
            parameter['values'] = [parameter['value'] * 0.9, parameter['value'] * 1.1]
        original = document.clone_data()
        variants = list(document.study_variants())
        self.assertEqual(len(variants), 4)
        for combination, variant, errors in variants:
            self.assertEqual(errors, [])
            self.assert_consistent(variant)
            for dimension in variant.dimensions:
                name = dimension.get('parameter')
                if name in combination:
                    self.assertAlmostEqual(dimension['value'], combination[name])
        self.assertEqual(document.data, original)

    def test_actual_conflict_rolls_back_with_dimension_ids(self):
        document = MeasurementShellSketch()
        a = document.add_point(0, 1)
        b = document.add_point(3, 1)
        line = document.add_line(a, b)
        document.add_constraint('horizontal', [line])
        first = document.add_dimension('length', [line], 3)
        second = document.add_dimension('length', [line], 3)
        before = document.clone_data()
        with self.assertRaisesRegex(ValueError, first + '.*' + second):
            document.set_dimension(first, 4)
        self.assertEqual(document.data, before)

    def test_unlinked_parameter_is_not_reported_as_geometry_update(self):
        document = MeasurementShellSketch()
        document.add_parameter('unused', 2)
        before = document.clone_data()
        with self.assertRaisesRegex(ValueError, 'keinem treibenden Maß'):
            document.set_parameter('unused', 3)
        self.assertEqual(document.data, before)

    def test_initial_horizontal_shape_does_not_create_an_implicit_relation(self):
        document = MeasurementShellSketch()
        a = document.add_point(0, 1)
        b = document.add_point(3, 1)
        document.add_line(a, b)
        dimension = document.add_dimension('point_vertical', [b], 1)
        document.set_dimension(dimension, 2)
        self.assertEqual(document.constraints, [])
        self.assertAlmostEqual(document.point(a)['y'], 1)
        self.assertAlmostEqual(document.point(b)['y'], 2, places=6)

    def test_explicit_horizontal_relation_propagates_the_dimension(self):
        document = MeasurementShellSketch()
        a = document.add_point(0, 1)
        b = document.add_point(3, 1)
        line = document.add_line(a, b)
        document.add_constraint('horizontal', [line])
        dimension = document.add_dimension('point_vertical', [b], 1)
        document.set_dimension(dimension, 2)
        self.assertAlmostEqual(document.point(a)['y'], 2, places=6)
        self.assertAlmostEqual(document.point(b)['y'], 2, places=6)

    def test_trim_does_not_keep_a_zero_sweep_arc(self):
        document = MeasurementShellSketch()
        center = document.add_point(0, 0)
        right = document.add_point(4, 0)
        top = document.add_point(0, 4)
        arc = document.add_arc(center, right, top, 4)
        outside = document.add_point(5, 0)
        document.add_line(right, outside)
        document.trim(arc, 0, 4)
        self.assertIsNone(document.element(arc))
        self.assertEqual(document.validate(), [])

    def test_point_axes_and_center_measures_remain_solver_backed(self):
        document = MeasurementShellSketch()
        center = document.add_point(1, 1)
        right = document.add_point(3, 1)
        top = document.add_point(1, 3)
        arc = document.add_arc(center, right, top, 2)
        document.add_dimension('radius', [arc], 2)
        x = document.add_dimension('point_horizontal', [center], 1)
        y = document.add_dimension('point_vertical', [center], 1)
        document.set_dimension(x, 3)
        document.set_dimension(y, 4)
        self.assert_consistent(document)
        self.assertAlmostEqual(document.point(center)['x'], 3, places=6)
        self.assertAlmostEqual(document.point(center)['y'], 4, places=6)

    def test_tangency_does_not_pin_an_unmeasured_line_length(self):
        document = MeasurementShellSketch()
        center = document.add_point(0, 0)
        right = document.add_point(2, 0)
        contact = document.add_point(1, math.sqrt(3))
        other = document.add_point(-2, math.sqrt(3))
        arc = document.add_arc(center, right, contact, 2)
        line = document.add_line(contact, other)
        document.add_constraint('coincident', [center, 'x_axis'])
        document.add_constraint('coincident', [center, 'y_axis'])
        document.add_constraint('horizontal', [line])
        document.add_dimension('radius', [arc], 2)
        document.apply_tangency([line, arc])
        self.assert_consistent(document)
        first, last = document.point(contact), document.point(other)
        self.assertAlmostEqual(first['x'], 0, places=6)
        self.assertAlmostEqual(first['y'], 2, places=6)
        self.assertAlmostEqual(last['x'], -2, places=6)
        self.assertNotAlmostEqual(abs(last['x'] - first['x']), 3)

    def test_coincident_propagates_later_measures_and_can_be_deleted(self):
        document = MeasurementShellSketch()
        a = document.add_point(1, 2)
        b = document.add_point(3, 4)
        c = document.add_point(6, 4)
        line = document.add_line(b, c)
        document.add_constraint('horizontal', [line])
        dimension = document.add_dimension('point_vertical', [a], 2)
        document.apply_coincident([a, b])
        self.assertEqual(document.element(line)['start_point'], b)
        document.set_dimension(dimension, 3)
        self.assert_consistent(document)
        self.assertAlmostEqual(document.point(c)['y'], 3, places=6)
        document.constraints[:] = [item for item in document.constraints if item['type'] != 'coincident']
        document.set_dimension(dimension, 4)
        self.assert_consistent(document)
        self.assertAlmostEqual(document.point(c)['y'], 3, places=6)
        self.assertAlmostEqual(document.point(a)['y'], 4, places=6)

    def test_old_relation_source_ids_cannot_silently_refer_to_new_elements(self):
        document = MeasurementShellSketch()
        a = document.add_point(0, 1)
        b = document.add_point(3, 1)
        document.add_constraint('coincident', [a, b])
        document.constraints[-1]['source_references'] = ['L001', 'L002']
        self.assertEqual(document.add_line(a, b), 'L003')


class ParameterDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_open_and_edit_each_parameter_through_measure_dialog(self):
        from measurement_shell_prototype import Window
        document = MeasurementShellSketch()
        window = Window(document, CommandStack(document))
        self.addCleanup(window.close)
        with patch('preprocessor_window_actions.QFileDialog.getOpenFileName',
                   return_value=(str(FIXTURE), 'JSON')):
            window.load()
        self.assertIn('C007', window.status.text())
        for name, target in [('ty', 4.0), ('tx', 3.0), ('r_sens', 2.5), ('R_outer', 9.0)]:
            dimension = next(d for d in document.dimensions if d.get('parameter') == name)
            window.view.selected_dimension_ids = {dimension['id']}
            with patch('preprocessor_window_actions.QInputDialog.getDouble', return_value=(target, True)), \
                 patch('preprocessor_window_actions.QInputDialog.getItem', return_value=(name, True)):
                window.edit_dimension()
            self.assertIn('aktualisiert', window.status.text())
            self.assertEqual(document.validate(), [])
            self.assertEqual(document._driving_dimension_violations(), [])
            self.assertEqual(document.data['parameters'][name]['value'], target)


if __name__ == '__main__':
    unittest.main()
