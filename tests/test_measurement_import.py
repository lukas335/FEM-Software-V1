"""Real OCC meshes, interface mechanics, FEBio snapshots and both GUI modes."""
from dataclasses import asdict, replace
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from geometry.mesh_generator import generate_wedge
from model.measurement_definition import definition_errors, load_definition, reference_x, with_reference
from model.materials import domain_materials, material_snapshot
from model.parameters import SimulationParameters
from solver.febio_writer import write_febio
from solver.prototype import baseline


FIXTURE = Path(__file__).parent / 'fixtures' / 'measurement_shell_regions.json'


def parameters(condition='bonded'):
    data = load_definition(FIXTURE)
    data['geometry']['regions'][0]['boundary_motion']['L006'] = 'follow_translation'
    data['geometry']['regions'][1]['boundary_motion']['L010'] = 'follow_translation'
    data['geometry']['regions'][1]['material'] = 'silicone_A30'
    data['geometry']['interfaces'][0]['condition'] = condition
    return replace(baseline(), measurement_definition=data)


class ImportDefinitionTests(unittest.TestCase):
    def test_import_does_not_overwrite_file_and_requires_explicit_drive(self):
        before = FIXTURE.read_bytes()
        data = load_definition(FIXTURE)
        self.assertEqual(FIXTURE.read_bytes(), before)
        self.assertEqual(len(data['geometry']['regions']), 2)
        self.assertAlmostEqual(reference_x(data), 11.779532286685722)
        self.assertTrue(any('Keine Zustellkante' in error for error in definition_errors(data)))
        self.assertFalse(definition_errors(data, require_drive=False))

    def test_snapshots_embed_two_domain_materials_and_roundtrip(self):
        params = parameters()
        self.assertFalse(params.validate())
        self.assertEqual(SimulationParameters.from_dict(asdict(params)), params)
        self.assertEqual(set(domain_materials(params)), {'measurement_R001', 'measurement_R002', 'pressure_shell'})
        self.assertNotEqual(domain_materials(params)['measurement_R001'].name, domain_materials(params)['measurement_R002'].name)
        self.assertIn('measurement_R001', material_snapshot(params))
        old = asdict(baseline()); old.pop('measurement_definition')
        self.assertEqual(SimulationParameters.from_dict(old), baseline())

    def test_bad_geometry_never_falls_back_and_axis_drive_is_explicit(self):
        params = parameters()
        data = copy.deepcopy(params.measurement_definition)
        data['geometry']['regions'][0]['boundary'][0] = 'deleted'
        self.assertTrue(replace(params, measurement_definition=data).validate())
        self.assertTrue(replace(params, measurement_definition={}).validate())
        data = load_definition(FIXTURE)
        data['geometry']['regions'][0]['boundary_motion']['L011'] = 'follow_translation'
        self.assertFalse(definition_errors(data))

    def test_shared_reference_point_keeps_explicit_region_for_sliding_bodies(self):
        data = parameters('frictionless').measurement_definition
        sensor = with_reference(data, 'p032', 'R002', role='sensor')
        shell = with_reference(data, 'p032', 'R001', role='sensor')
        self.assertEqual(sensor['metadata']['sensor_tip_region'], 'R002')
        self.assertEqual(shell['metadata']['sensor_tip_region'], 'R001')
        self.assertFalse(definition_errors(sensor))
        with self.assertRaises(ValueError):
            with_reference(data, 'p002', 'R002')

    def test_legacy_study_identity_stays_the_historical_value(self):
        from model.study import StudyConfig
        config = StudyConfig(baseline(), (8.,), (1.25,), (15.,), (), pressure_mode='closed')
        self.assertEqual(config.cases()[0]['id'], '021cf85f137f1939')
        with self.assertRaisesRegex(ValueError, 'R/tx/ty'):
            replace(config, base_parameters=parameters()).cases()


class ImportMeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {}
        for condition in ('bonded', 'frictionless'):
            params = parameters(condition)
            cls.models[condition] = (params, generate_wedge(params))

    def test_two_volumes_and_only_selected_drive_faces(self):
        for params, mesh in self.models.values():
            self.assertEqual(set(mesh.elements), {'measurement_R001', 'measurement_R002', 'pressure_shell'})
            expected = {node for name in ('measurement_R001_boundary_L006', 'measurement_R002_boundary_L010')
                        for face in mesh.surfaces[name] for node in face}
            self.assertEqual(set(mesh.node_sets['measurement_support']), expected)
            metadata = params.measurement_definition['metadata']
            point = next(point for point in params.measurement_definition['geometry']['points']
                         if point['id'] == metadata['sensor_tip_point'])
            actual = mesh.nodes[mesh.node_sets['measurement_inner_pole'][0]]
            self.assertAlmostEqual(actual[0], point['x'] - reference_x(params.measurement_definition), places=6)
            self.assertEqual(actual[1:], (0, 0))
            self.assertTrue(all(records for records in mesh.elements.values()))

    def test_frictionless_does_not_share_nodes_bonded_does(self):
        for condition, (_, mesh) in self.models.items():
            first = {node for _, conn in mesh.elements['measurement_R001'] for node in conn}
            second = {node for _, conn in mesh.elements['measurement_R002'] for node in conn}
            interface_a = {node for face in mesh.surfaces['measurement_interface_I001_a'] for node in face}
            interface_b = {node for face in mesh.surfaces['measurement_interface_I001_b'] for node in face}
            if condition == 'bonded':
                self.assertEqual(interface_a, interface_b)
                self.assertEqual(first & second, interface_a)
            else:
                self.assertFalse(first & second)
                self.assertFalse(interface_a & interface_b)
                self.assertEqual({mesh.nodes[node] for node in interface_a}, {mesh.nodes[node] for node in interface_b})

    def test_writer_material_contact_drive_and_animation_parts(self):
        from model.deformation import read_mesh
        for condition, (params, mesh) in self.models.items():
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'model.feb'
                write_febio(mesh, params, path)
                root = ET.parse(path).getroot()
                self.assertEqual(len(root.findall('./Material/material')), 3)
                self.assertEqual({entry.get('name') for entry in root.findall('./MeshDomains/SolidDomain')}, set(mesh.elements))
                pairs = root.findall('./Contact/contact')
                self.assertEqual(len(pairs), 2 if condition == 'frictionless' else 1)
                self.assertTrue(all(entry.findtext('fric_coeff') == '0' for entry in pairs))
                self.assertIsNone(root.find("./Boundary/bc[@node_set='measurement_support']"))
                for step in root.findall('./Step/step'):
                    drive = step.find("./Boundary/bc[@node_set='measurement_support']")
                    self.assertEqual(drive.findtext('dof'), 'x')
                self.assertEqual([part.name for part in read_mesh(path).parts], ['measurement_R001', 'measurement_R002', 'pressure'])

    def test_all_tetrahedra_positive_and_imported_surfaces_point_outward(self):
        import numpy as np
        from solver.imported_boundaries import imported_surface_orienter
        for params, mesh in self.models.values():
            for records in mesh.elements.values():
                for _, conn in records:
                    xyz = np.asarray([mesh.nodes[node] for node in conn])
                    self.assertGreater(np.linalg.det(xyz[1:] - xyz[0]), 0)
            orient = imported_surface_orienter(mesh, params.measurement_definition)
            for name in ('measurement_contact', 'measurement_interface_I001_a', 'measurement_interface_I001_b'):
                for face in mesh.surfaces[name]:
                    self.assertEqual(set(orient(face, name)), set(face))

    def test_legacy_mesh_and_support_are_unchanged(self):
        mesh = generate_wedge(baseline())
        self.assertEqual(set(mesh.elements), {'measurement_shell', 'pressure_shell'})
        self.assertTrue(all(abs(mesh.nodes[node][0] + 8) < 1e-8 for node in mesh.node_sets['measurement_support']))

    def test_imported_shell_can_use_existing_cover_and_closed_cavity(self):
        params = parameters('frictionless')
        params = replace(params, initial_gap_mm=2, cover_shell=replace(params.cover_shell, enabled=True))
        mesh = generate_wedge(params)
        self.assertEqual(set(mesh.elements), {'measurement_R001', 'measurement_R002', 'pressure_shell', 'cover_front', 'cover_rear'})
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'model.feb'
            write_febio(mesh, params, path)
            root = ET.parse(path).getroot()
            self.assertEqual(len(root.findall('./Contact/contact')), 3)
            self.assertEqual(root.find("./Step/step[@id='2']/Constraints/constraint").get('surface'), 'fluid_cavity')
            from model.deformation import read_mesh
            self.assertEqual(len(read_mesh(path).parts), 5)

    def test_axis_edge_drive_tags_only_its_nodes_without_inventing_a_surface(self):
        data = load_definition(FIXTURE)
        data['geometry']['regions'][0]['boundary_motion']['L011'] = 'follow_translation'
        params = replace(baseline(), measurement_definition=data)
        mesh = generate_wedge(params)
        driven = mesh.node_sets['measurement_support']
        self.assertTrue(driven)
        self.assertNotIn('measurement_support', mesh.surfaces)
        self.assertTrue(all(mesh.nodes[node][1:] == (0, 0) for node in driven))
        self.assertTrue(all(-1.64 <= mesh.nodes[node][0] <= 0 for node in driven))
        with TemporaryDirectory() as directory:
            write_febio(mesh, params, Path(directory) / 'model.feb')


class ImportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_import_switch_back_persistence_and_preview(self):
        from app.main_window import MainWindow
        with TemporaryDirectory() as directory:
            window = MainWindow(Path(directory))
            self.assertIsNone(window.parameters().measurement_definition)
            legacy = window.parameters().measurement_shell
            path = Path(directory) / 'sketch.json'
            path.write_text(json.dumps(parameters().measurement_definition), encoding='utf-8')
            with patch('app.measurement_import.QFileDialog.getOpenFileName', return_value=(str(path), 'JSON')):
                window.import_measurement()
            self.assertIsNotNone(window.parameters().measurement_definition)
            self.assertFalse(window.r1.isEnabled())
            window._busy_changed(True); window._busy_changed(False)
            self.assertFalse(window.r1.isEnabled())
            self.assertFalse(window.parameters().validate())
            self.assertEqual(window.measurement_reference.currentData(), ('p002', 'R001'))
            self.assertEqual(window.measurement_sensor.currentData(), ('p032', 'R002'))
            self.assertEqual(len(window.preview.ax.patches), 6)  # Two mirrored regions + pressure/rear support.
            saved = window.parameters().measurement_definition
            self.assertTrue(window._save_settings())
            path.unlink()  # Restore uses the snapshot, not the deleted source file.
            restored = MainWindow(Path(directory))
            self.assertEqual(restored.parameters().measurement_definition, saved)
            restored.measurement_source.setCurrentIndex(0)
            self.assertIsNone(restored.parameters().measurement_definition)
            self.assertTrue(restored.r1.isEnabled())
            self.assertEqual(restored.parameters().measurement_shell, legacy)
            restored._update_preview()
            self.assertEqual(len(restored.preview.ax.patches), 3)
            restored.settings_timer.stop(); window.settings_timer.stop()
            window.close(); restored.close(); self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
