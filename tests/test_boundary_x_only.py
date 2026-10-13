"""Independent axial drive and transverse guidance, from editor to FEBio DOFs."""
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from measurement_shell_prototype import CommandStack, MeasurementShellSketch, Window
from geometry.mesh_generator import generate_wedge
from model.measurement_definition import load_definition
from model.mesh_inspection import read_inspection
from solver.febio_writer import write_febio
from solver.prototype import baseline

FIXTURE = Path(__file__).parent / 'fixtures/measurement_shell_regions.json'


class GuidanceEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.document = MeasurementShellSketch(load_definition(FIXTURE))
        self.history = CommandStack(self.document)

    def test_four_combinations_and_undo_removal(self):
        for motion in ('free_contact', 'follow_translation'):
            for enabled in (False, True):
                self.history.run('Randbedingungen', lambda: (
                    self.document.set_boundary_motion('R001', 'L006', motion),
                    self.document.set_boundary_x_only('R001', 'L006', enabled)))
                entry = next(item for item in self.document.simulation_boundary_conditions()
                             if item['region_id'] == 'R001' and item['element_id'] == 'L006')
                self.assertEqual(entry['behavior'], motion)
                self.assertEqual(entry['x_only'], enabled)
        self.history.run('Führung entfernen', lambda: self.document.set_boundary_x_only('R001', 'L006', False))
        self.history.undo()
        self.assertTrue(self.document.regions[0]['boundary_x_only']['L006'])
        self.history.redo()
        self.assertNotIn('L006', self.document.regions[0]['boundary_x_only'])
        self.assertEqual(self.document.regions[0]['boundary_motion']['L006'], 'follow_translation')

    def test_split_and_detect_and_json_roundtrip_preserve_both_flags(self):
        # An unconstrained rectangle isolates boundary inheritance from the
        # separate dimension/tangency semantics of splitting measured paths.
        document = MeasurementShellSketch()
        points = [document.add_point(x, y) for x, y in ((0, 0), (4, 0), (4, 3), (0, 3))]
        edges = [document.add_line(points[i], points[(i + 1) % 4]) for i in range(4)]
        document.detect_regions()
        original = edges[1]
        document.set_boundary_motion('R001', original, 'follow_translation')
        document.set_boundary_x_only('R001', original, True)
        edge = document.element(original)
        a, b = [document.point(edge[key]) for key in ('start_point', 'end_point')]
        x, y = (a['x'] + b['x']) / 2, (a['y'] + b['y']) / 2
        before = {item['id'] for item in document.elements}
        point = document.add_point(x, y)
        document._split_element_at(original, x, y, point)
        new_edge = next(item['id'] for item in document.elements if item['id'] not in before)
        document.detect_regions()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'sketch.json'; path.write_text(json.dumps(document.data), encoding='utf-8')
            restored = load_definition(path)
        region = next(r for r in restored['geometry']['regions'] if r['id'] == 'R001')
        for edge_id in (original, new_edge):
            self.assertTrue(region['boundary_x_only'][edge_id])
            self.assertEqual(region['boundary_motion'][edge_id], 'follow_translation')

    def test_invalid_reference_and_non_boolean_rejected(self):
        with self.assertRaises(ValueError):
            self.document.set_boundary_x_only('R002', 'L006', True)
        with self.assertRaises(ValueError):
            self.document.set_boundary_x_only('R001', 'L006', 'true')
        self.document.regions[0]['boundary_x_only'] = {'missing': True}
        self.assertTrue(any('X-Führung' in error for error in self.document.validate()))

    def test_ui_independent_checkbox_selection_apply_and_undo(self):
        window = Window(self.document, self.history)
        try:
            window.select_boundary('R001', 'L006')
            self.assertFalse(window.boundary_x_only.isChecked())
            window.boundary_behavior.setCurrentIndex(window.boundary_behavior.findData('follow_translation'))
            window.boundary_x_only.setChecked(True); window.apply_boundary_behavior()
            self.assertTrue(window.boundary_x_only.isChecked())
            self.assertEqual(window.boundary_behavior.currentData(), 'follow_translation')
            window.select_boundary('R002', 'L004')
            self.assertFalse(window.boundary_x_only.isChecked())
            self.assertEqual(window.boundary_behavior.currentData(), 'free_contact')
            window.boundary_x_only.setChecked(True); window.apply_boundary_behavior()
            self.assertEqual(self.document.regions[1]['boundary_motion']['L004'], 'free_contact')
            self.assertTrue(self.document.regions[1]['boundary_x_only']['L004'])
            window.undo()
            self.assertFalse(window.boundary_x_only.isChecked())
            self.assertTrue(self.document.regions[0]['boundary_x_only']['L006'])
        finally:
            window.close()


class GuidanceMeshTests(unittest.TestCase):
    def test_combined_and_free_axial_guidance_use_only_selected_owner_nodes(self):
        data = load_definition(FIXTURE)
        first, second = data['geometry']['regions']
        first['boundary_motion']['L006'] = 'follow_translation'
        first['boundary_x_only'] = {'L006': True}
        second['boundary_x_only'] = {'A002': True}  # Shared geometry, but independent sliding domain.
        data['geometry']['interfaces'][0]['condition'] = 'frictionless'
        params = replace(baseline(), measurement_definition=data)
        mesh = generate_wedge(params)
        drive = set(mesh.node_sets['measurement_support'])
        guided = set(mesh.node_sets['measurement_x_only'])
        region_b = {node for face in mesh.surfaces['measurement_R002_boundary_A002'] for node in face}
        region_a = {node for face in mesh.surfaces['measurement_R001_boundary_A002'] for node in face}
        self.assertEqual(guided, drive | region_b)
        self.assertFalse(region_a & region_b)
        self.assertTrue(region_b - drive)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'model.feb'; write_febio(mesh, params, path)
            root = ET.parse(path).getroot()
            bc = root.find("./Boundary/bc[@node_set='measurement_x_only']")
            self.assertEqual(bc.get('type'), 'zero displacement')
            self.assertEqual(bc.findtext('y_dof'), '1')
            self.assertEqual(bc.findtext('z_dof'), '1')
            self.assertNotEqual(bc.findtext('x_dof'), '1')
            for step in root.findall('./Step/step'):
                drive_bc = step.find("./Boundary/bc[@node_set='measurement_support']")
                self.assertEqual(drive_bc.findtext('dof'), 'x')
                self.assertIsNone(step.find("./Boundary/bc[@node_set='measurement_x_only']"))
            report = read_inspection(path)
            entry = next(g for g in report.groups if g.name == 'measurement_x_only')
            self.assertEqual(set(entry.nodes), guided)
            self.assertIn('Bewegung nur entlang x', entry.detail)
            # Guidance alone is not an axial anchor of the second body.
            self.assertTrue(any('measurement_R002' in warning for warning in report.warnings))
            axis = root.find("./Mesh/NodeSet[@name='axis_transverse_fixed']")
            if axis is not None:
                self.assertFalse(guided & set(map(int, axis.text.split(','))))

    def test_guided_axis_edge_without_surface_has_owner_correct_nodes(self):
        data = load_definition(FIXTURE)
        first = data['geometry']['regions'][0]
        first['boundary_motion']['L006'] = 'follow_translation'
        first['boundary_x_only'] = {'L011': True}
        data['geometry']['interfaces'][0]['condition'] = 'frictionless'
        mesh = generate_wedge(replace(baseline(), measurement_definition=data))
        self.assertNotIn('measurement_R001_boundary_L011', mesh.surfaces)
        guided = set(mesh.node_sets['measurement_x_only'])
        self.assertTrue(guided)
        self.assertTrue(all(mesh.nodes[node][1:] == (0, 0) for node in guided))
        owner = {node for _, conn in mesh.elements['measurement_R001'] for node in conn}
        other = {node for _, conn in mesh.elements['measurement_R002'] for node in conn}
        self.assertTrue(guided <= owner); self.assertFalse(guided & other)


if __name__ == '__main__':
    unittest.main()
