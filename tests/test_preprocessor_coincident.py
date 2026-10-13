"""Coincident relations across model, interactive picking and later edits."""
import os
import unittest

from measurement_shell_prototype import CommandStack, MeasurementShellSketch, Window


def arc_and_line():
    document = MeasurementShellSketch()
    center = document.add_point(3, 4)
    start = document.add_point(5, 4)
    end = document.add_point(3, 6)
    arc = document.add_arc(center, start, end, 2)
    a = document.add_point(0, 3)
    b = document.add_point(10, 3)
    line = document.add_line(a, b)
    document.add_constraint('horizontal', [line])
    height = document.add_dimension('point_vertical', [a], 3)
    document.add_dimension('length', [line], 10)
    radius = document.add_dimension('radius', [arc], 2)
    return document, arc, line, start, center, height, radius


class CoincidentModelTests(unittest.TestCase):
    def test_whole_line_on_either_axis_in_either_selection_order(self):
        for axis in ('x_axis', 'y_axis'):
            for reverse in (False, True):
                with self.subTest(axis=axis, reverse=reverse):
                    document = MeasurementShellSketch()
                    a = document.add_point(2, 2)
                    b = document.add_point(5, 3)
                    line = document.add_line(a, b)
                    length = document.add_dimension('length', [line], 10 ** 0.5)
                    references = [line, axis]
                    document.apply_coincident(references[::-1] if reverse else references)
                    coordinate = 'y' if axis == 'x_axis' else 'x'
                    self.assertAlmostEqual(document.point(a)[coordinate], 0, places=6)
                    self.assertAlmostEqual(document.point(b)[coordinate], 0, places=6)
                    document.set_dimension(length, 5)
                    self.assertAlmostEqual(document.point(a)[coordinate], 0, places=6)
                    self.assertAlmostEqual(document.point(b)[coordinate], 0, places=6)
                    self.assertEqual(document.validate(), [])

    def test_arc_endpoint_on_line_interior_in_either_selection_order(self):
        for reverse in (False, True):
            document, arc, line, start, center, height, radius = arc_and_line()
            references = [arc, line]
            document.apply_coincident(references[::-1] if reverse else references)
            relation = next(c for c in document.constraints if c['type'] == 'coincident')
            self.assertEqual(relation['references'], [start, line])
            self.assertNotIn(center, relation['references'])
            self.assertAlmostEqual(document.point(start)['y'], 3, places=6)
            self.assertEqual(document.validate(), [])
            self.assertGreater(document.point(start)['x'], document.point(document.element(line)['start_point'])['x'])
            document.set_dimension(height, 4)
            document.set_dimension(radius, 3)
            self.assertAlmostEqual(document.point(start)['y'], 4, places=6)
            self.assertEqual(document.validate(), [])
            self.assertEqual(document._driving_dimension_violations(), [])

    def test_explicit_arc_center_is_a_separate_point_on_line_pick(self):
        document, arc, line, start, center, height, radius = arc_and_line()
        document.apply_coincident([center, line])
        self.assertAlmostEqual(document.point(center)['y'], 3, places=6)
        self.assertEqual(document.validate(), [])

    def test_conflicting_axis_relation_rolls_back_and_identifies_the_measure(self):
        document = MeasurementShellSketch()
        a = document.add_point(1, 2)
        b = document.add_point(4, 2)
        line = document.add_line(a, b)
        dimension = document.add_dimension('point_vertical', [a], 2)
        original = document.clone_data()
        with self.assertRaisesRegex(ValueError, dimension):
            document.apply_coincident([line, 'x_axis'])
        self.assertEqual(document.data, original)

    def test_point_on_segment_reaches_endpoint_but_extension_stays_free(self):
        for extension in (False, True):
            document = MeasurementShellSketch()
            a = document.add_point(0, 1)
            b = document.add_point(3, 1)
            point = document.add_point(8, 2)
            line = document.add_line(a, b)
            document.add_constraint('horizontal', [line])
            document.add_constraint('coincident', [a, 'y_axis'])
            document.add_dimension('point_vertical', [a], 1)
            document.add_dimension('length', [line], 3)
            document.apply_coincident([point, line], extension=extension)
            self.assertAlmostEqual(document.point(point)['y'], 1, places=6)
            if extension:
                self.assertAlmostEqual(document.point(point)['x'], 8, places=6)
            else:
                self.assertLessEqual(document.point(point)['x'], 3.000001)
            self.assertEqual(document.validate(), [])

    def test_axis_relation_survives_json_reload_and_can_be_removed(self):
        document = MeasurementShellSketch()
        a = document.add_point(1, 2)
        b = document.add_point(4, 2)
        line = document.add_line(a, b)
        document.apply_coincident([line, 'x_axis'])
        restored = MeasurementShellSketch(document.clone_data())
        self.assertEqual(restored.validate(), [])
        restored.constraints[:] = []
        dimension = restored.add_dimension('point_vertical', [a], 0)
        restored.set_dimension(dimension, 2)
        self.assertAlmostEqual(restored.point(a)['y'], 2, places=6)
        self.assertEqual(restored.validate(), [])


class CoincidentPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def window(self, document):
        window = Window(document, CommandStack(document))
        window.show()
        self.addCleanup(window.close)
        window.fit_view()
        self.app.processEvents()
        return window

    def test_selected_line_then_coincident_then_axis_click(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        document = MeasurementShellSketch()
        a = document.add_point(2, 2)
        b = document.add_point(5, 2)
        line = document.add_line(a, b)
        window = self.window(document)
        QTest.mouseClick(window.view.viewport(), Qt.LeftButton,
                         pos=window.view.mapFromScene(QPointF(3.5, -2)))
        window.coincident_relation()
        self.assertEqual(window.view.coincident_data.get('first'), line)
        QTest.mouseClick(window.view.viewport(), Qt.LeftButton,
                         pos=window.view.mapFromScene(QPointF(8, 0)))
        self.assertIn('erstellt', window.status.text())
        self.assertAlmostEqual(document.point(a)['y'], 0, places=6)
        self.assertAlmostEqual(document.point(b)['y'], 0, places=6)

    def test_picked_arc_and_line_are_not_changed_into_endpoint_pair(self):
        document, arc, line, start, center, height, radius = arc_and_line()
        window = self.window(document)
        window.apply_coincident_references([arc, line])
        self.assertIn('erstellt', window.status.text())
        self.assertAlmostEqual(document.point(start)['y'], 3, places=6)
        self.assertEqual(document.validate(), [])

    def test_arc_then_line_picked_with_actual_mouse_clicks(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        document, arc, line, start, center, height, radius = arc_and_line()
        window = self.window(document)
        window.coincident_relation()
        midpoint = QPointF(3 + 2 ** 0.5, -(4 + 2 ** 0.5))
        QTest.mouseClick(window.view.viewport(), Qt.LeftButton,
                         pos=window.view.mapFromScene(midpoint))
        self.assertEqual(window.view.coincident_data.get('first'), arc)
        QTest.mouseClick(window.view.viewport(), Qt.LeftButton,
                         pos=window.view.mapFromScene(QPointF(5, -3)))
        self.assertIn('erstellt', window.status.text())
        self.assertAlmostEqual(document.point(start)['y'], 3, places=6)
        self.assertEqual(document.validate(), [])

    def test_axis_first_can_pick_a_line_already_on_that_axis(self):
        from PySide6.QtCore import QPointF
        document = MeasurementShellSketch()
        a = document.add_point(2, 0)
        b = document.add_point(5, 0)
        line = document.add_line(a, b)
        window = self.window(document)
        window.view.set_coincident_mode()
        window.view._coincident_press(QPointF(8, 0))
        self.assertEqual(window.view.coincident_data.get('first'), 'x_axis')
        window.view._coincident_press(QPointF(3.5, 0))
        self.assertIn('erstellt', window.status.text())
        self.assertEqual(document.constraints[-1]['references'], [line, 'x_axis'])

    def test_orphan_points_from_trim_do_not_intercept_visible_line_picks(self):
        from PySide6.QtCore import QPointF
        document, arc, line, start, center, height, radius = arc_and_line()
        document.add_point(5, 3)
        window = self.window(document)
        window.view.set_coincident_mode()
        self.assertEqual(window.view._coincident_hover_reference(QPointF(5, 3)), line)

    def test_selected_arc_can_be_refined_to_its_center_before_line_pick(self):
        from PySide6.QtCore import QPointF
        document, arc, line, start, center, height, radius = arc_and_line()
        window = self.window(document)
        window.view.select_element(arc)
        window.coincident_relation()
        window.view._coincident_press(QPointF(3, 4))
        self.assertEqual(window.view.coincident_data.get('first'), center)
        window.view._coincident_press(QPointF(5, 3))
        self.assertIn('erstellt', window.status.text())
        self.assertAlmostEqual(document.point(center)['y'], 3, places=6)
        self.assertEqual(document.validate(), [])


if __name__ == '__main__':
    unittest.main()
