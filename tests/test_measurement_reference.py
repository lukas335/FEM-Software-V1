"""Editor reference selection, constant-pixel endpoints and relative solver ux."""
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from measurement_shell_prototype import CommandStack, MeasurementShellSketch, Window
from model.measurement_definition import contour_references, definition_errors, load_definition, reference_x, with_reference
from solver.prototype import baseline, parse_reaction_log

FIXTURE = Path(__file__).parent / 'fixtures' / 'measurement_shell_regions.json'


class ReferenceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.document = MeasurementShellSketch(json.loads(FIXTURE.read_text(encoding='utf-8-sig')))
        self.window = Window(self.document, CommandStack(self.document))
        self.window.show(); self.app.processEvents(); self.window.fit_view()

    def tearDown(self):
        self.window.close(); self.app.processEvents()

    def choose(self, point='p035', region='R002'):
        combo = self.window.measurement_point
        index = next(i for i in range(combo.count()) if combo.itemData(i) == (point, region))
        combo.setCurrentIndex(index)

    def test_selection_persists_undo_redo_and_import_without_changing_geometry(self):
        before = self.document.clone_data()
        self.choose()
        self.assertEqual(self.document.data['metadata']['sensor_tip_point'], 'p035')
        self.assertEqual(self.document.data['metadata']['sensor_tip_region'], 'R002')
        self.assertEqual(self.document.geometry, before['geometry'])
        self.window.undo()
        self.assertNotIn('sensor_tip_point', self.document.data.get('metadata', {}))
        self.window.redo()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'sketch.json'
            path.write_text(json.dumps(self.document.data), encoding='utf-8')
            loaded = load_definition(path)
        self.assertEqual(loaded['metadata']['sensor_tip_point'], 'p035')
        self.assertEqual(loaded['metadata']['sensor_tip_region'], 'R002')
        self.assertFalse(definition_errors(loaded, require_drive=False))
        self.window.measurement_point.setCurrentIndex(0)
        self.assertNotIn('sensor_tip_point', self.document.data['metadata'])

    def test_click_shared_off_axis_point_uses_selected_material_region(self):
        self.window.tabs.setCurrentIndex(1); self.app.processEvents()
        self.window.region_list.setCurrentRow(1)
        self.window.reference_pick_button.setChecked(True)
        point = self.document.point('p035')
        position = self.window.region_view.mapFromScene(QPointF(point['x'], -point['y']))
        QTest.mouseClick(self.window.region_view.viewport(), Qt.LeftButton, pos=position)
        self.assertEqual(self.document.data['metadata']['sensor_tip_point'], 'p035')
        self.assertEqual(self.document.data['metadata']['sensor_tip_region'], 'R002')
        self.assertFalse(self.window.reference_pick_active)
        self.window.reference_pick_button.setChecked(True)
        QTest.keyClick(self.window.region_view, Qt.Key_Escape)
        self.assertFalse(self.window.reference_pick_active)

    def test_deleted_reference_is_not_recycled_or_silently_replaced(self):
        self.choose()
        self.document.delete_ids(['A002', 'A004', 'L010'])
        # Regardless of contour reconciliation, the reference is invalid if
        # its point no longer participates in its selected region.
        self.document.points[:] = [p for p in self.document.points if p['id'] != 'p035']
        self.assertNotEqual(self.document.add_point(50, 1), 'p035')
        data = self.document.clone_data()
        self.assertTrue(definition_errors(data, require_drive=False))

    def test_all_endpoints_visible_in_all_modes_and_constant_screen_size(self):
        expected = {edge[key] for edge in self.document.elements for key in ('start_point', 'end_point')}
        for mode in ('select', 'line', 'arc', 'trim', 'dimension'):
            self.window.view.set_mode(mode); self.window.draw()
            markers = [item for item in self.window.view.scene().items()
                       if item.data(2) and item.data(2)[0] == 'endpoint']
            self.assertEqual({item.data(2)[1] for item in markers}, expected)
            for scale in (20, 80, 320):
                self.window.view.resetTransform(); self.window.view.scale(scale, scale)
                for marker in markers:
                    bounds = marker.deviceTransform(self.window.view.viewportTransform()).mapRect(marker.rect())
                    self.assertAlmostEqual(bounds.width(), 4.0)
                    self.assertAlmostEqual(bounds.height(), 4.0)
                    self.assertTrue(marker.isVisible())
                for ref in expected:
                    point = self.document.point(ref)
                    target = QPointF(point['x'], point['y'])
                    self.assertEqual(self.window.view.snap(target), target)

    def test_real_endpoint_snap_wins_over_near_horizontal_inference(self):
        point_id = self.document.add_point(30, .1)
        self.window.view.resetTransform(); self.window.view.scale(40, 40)
        target = QPointF(30, .1)
        caught = self.window.view.snap(target)
        self.assertEqual(self.window.view.snap_label, point_id)
        end, orientation = self.window.view.infer_line_orientation(caught, QPointF(27, 0))
        self.assertEqual(end, target)
        self.assertIsNone(orientation)

    def test_no_initial_geometry_or_reference(self):
        empty = MeasurementShellSketch()
        window = Window(empty, CommandStack(empty))
        self.assertFalse(empty.elements)
        self.assertFalse(empty.data.get('metadata'))
        self.assertFalse(window.reference_pick_button.isEnabled())
        window.close()

    def test_old_point_only_metadata_uses_importer_owner(self):
        self.document.data['metadata'] = {'sensor_tip_point': 'p032'}
        self.window.refresh()
        self.assertEqual(self.window.measurement_point.currentData(), ('p032', 'R002'))
        self.assertNotIn('Ungültiger', self.window.measurement_point.currentText())

    def test_reference_marker_does_not_block_normal_boundary_selection(self):
        self.choose()
        self.window.tabs.setCurrentIndex(1); self.app.processEvents()
        point = self.document.point('p035')
        before = self.window.selected_boundary
        position = self.window.region_view.mapFromScene(QPointF(point['x'], -point['y']))
        QTest.mouseClick(self.window.region_view.viewport(), Qt.LeftButton, pos=position)
        self.assertNotEqual(self.window.selected_boundary, before)
        region_id, edge_id = self.window.selected_boundary
        edge = self.document.element(edge_id)
        self.assertIn('p035', (edge['start_point'], edge['end_point']))
        self.assertEqual(self.document.data['metadata']['sensor_tip_region'], 'R002')

    def test_main_import_retains_off_axis_reference_and_can_select_other_owner(self):
        from app.main_window import MainWindow
        data = with_reference(load_definition(FIXTURE), 'p035', 'R002', role='sensor')
        with TemporaryDirectory() as directory:
            main = MainWindow(Path(directory))
            main.set_imported_measurement(data)
            self.assertEqual(main.measurement_sensor.currentData(), ('p035', 'R002'))
            self.assertEqual(main.parameters().measurement_definition['metadata']['sensor_tip_point'], 'p035')
            index = next(i for i in range(main.measurement_sensor.count())
                         if main.measurement_sensor.itemData(i) == ('p035', 'R001'))
            main.measurement_sensor.setCurrentIndex(index)
            self.assertEqual(main.parameters().measurement_definition['metadata']['sensor_tip_region'], 'R001')
            main.settings_timer.stop(); main.close()


class ReferenceSolverTests(unittest.TestCase):
    def test_relative_delta_x_for_positive_negative_and_equal_reference_motion(self):
        sample = ''
        for index, (drive, movement) in enumerate(((1.0, .3), (2.0, -.2), (3.0, 3.0))):
            time = 1.1 + index * .1
            sample += (f'Data Record #{2*index+1}\nTime = {time}\nData = support_reaction\n'
                       f'1 {drive} -.1\n2 {drive} -.2\n'
                       f'Data Record #{2*index+2}\nTime = {time}\nData = measurement_inner_pole\n'
                       f'9 {movement}\n')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'solver.log'; path.write_text(sample, encoding='utf-8')
            history = parse_reaction_log(path, {1, 2}, 3, displacement_node=9)
        for actual, expected in zip(history, (.7, 2.2, 0)):
            self.assertAlmostEqual(actual[1], expected)
            self.assertAlmostEqual(actual[2], 36)

    def test_invalid_center_and_wrong_owner_rejected_but_off_axis_vertex_allowed(self):
        data = load_definition(FIXTURE)
        for owner in ('R001', 'R002'):
            selected = with_reference(data, 'p035', owner, role='sensor')
            self.assertFalse(definition_errors(selected, require_drive=False))
        with self.assertRaises(ValueError):
            with_reference(data, 'p001', 'R001', role='sensor')  # Arc center, not contour.
        with self.assertRaises(ValueError):
            with_reference(data, 'p002', 'R002', role='sensor')

    def test_off_axis_reference_maps_to_exact_owner_mesh_and_febio_log_node(self):
        from geometry.mesh_generator import generate_wedge
        from solver.febio_writer import write_febio
        from xml.etree import ElementTree as ET
        data = load_definition(FIXTURE)
        data['geometry']['interfaces'][0]['condition'] = 'frictionless'
        data['geometry']['regions'][0]['boundary_motion']['L006'] = 'follow_translation'
        nodes_selected = []
        for owner in ('R001', 'R002'):
            selected = with_reference(data, 'p035', owner, role='sensor')
            params = replace(baseline(), measurement_definition=selected)
            mesh = generate_wedge(params)
            point = next(p for p in selected['geometry']['points'] if p['id'] == 'p035')
            node = mesh.node_sets['measurement_inner_pole'][0]
            actual = mesh.nodes[node]
            self.assertAlmostEqual(actual[0], point['x'] - reference_x(selected), places=6)
            self.assertAlmostEqual(actual[1], point['y'], places=6)
            self.assertAlmostEqual(actual[2], 0, places=6)
            owner_nodes = {n for _, conn in mesh.elements[f'measurement_{owner}'] for n in conn}
            other = 'R002' if owner == 'R001' else 'R001'
            other_nodes = {n for _, conn in mesh.elements[f'measurement_{other}'] for n in conn}
            self.assertIn(node, owner_nodes); self.assertNotIn(node, other_nodes)
            nodes_selected.append(node)
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'model.feb'; write_febio(mesh, params, path)
                logger = ET.parse(path).find("./Output/logfile/node_data[@name='measurement_inner_pole']")
                self.assertIsNotNone(logger)
                self.assertEqual(logger.get('data'), 'ux')
                self.assertEqual(logger.text.strip(), str(node))
        self.assertNotEqual(*nodes_selected)


if __name__ == '__main__':
    unittest.main()
