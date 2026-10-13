"""Actual input BCs, read-only rendering and solver-free mesh preparation."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow
from app.mesh_inspection_panel import MeshInspectionPanel
from app.simulation_runner import SimulationRunner
from geometry.mesh_generator import generate_wedge
from model.measurement_definition import load_definition
from model.mesh_inspection import read_inspection, solver_progress
from solver.febio_writer import write_febio
from solver.prototype import baseline

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / 'fixtures/measurement_shell_regions.json'


class MeshInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temporary = TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        definition = load_definition(FIXTURE)
        definition['geometry']['regions'][0]['boundary_motion']['L006'] = 'follow_translation'
        definition['geometry']['interfaces'][0]['condition'] = 'frictionless'
        cls.params = replace(baseline(), measurement_definition=definition)
        cls.path = cls.directory / 'model.feb'
        write_febio(generate_wedge(cls.params), cls.params, cls.path)
        (cls.directory / 'parameters.json').write_text(json.dumps(asdict(cls.params)), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_actual_displacement_dofs_contacts_and_unanchored_body(self):
        before = self.path.read_bytes()
        report = read_inspection(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertGreater(report.min_jacobian, 0)
        self.assertTrue(any('measurement_R002' in warning for warning in report.warnings))
        self.assertFalse(any('measurement_R001' in warning for warning in report.warnings))
        drive = [group for group in report.groups if group.kind == 'Zustellung']
        self.assertEqual(len(drive), 2)
        self.assertIn('ux=0', drive[0].detail)
        self.assertIn('ux=1', drive[1].detail)
        root = ET.parse(self.path).getroot()
        expected = tuple(map(int, root.find("./Mesh/NodeSet[@name='measurement_support']").text.split(',')))
        self.assertEqual(drive[0].nodes, expected)
        self.assertFalse(any(group.name == 'measurement_support' and group.kind == 'Fixierung'
                             for group in report.groups))
        interface = [group for group in report.groups if 'measurement_interface_I001' in group.name and group.kind == 'Kontakt']
        self.assertEqual(len(interface), 2)
        self.assertFalse(set(interface[0].nodes) & set(interface[1].nodes))
        self.assertTrue(all('μ=0' in group.detail for group in interface))
        self.assertTrue(any(group.kind == 'Volumen' and 'Schritt 2' in group.detail for group in report.groups))
        self.assertTrue(any(group.kind == 'Lastkurve' and group.name == '2' for group in report.groups))

    def test_shared_nodes_anchor_bonded_region_without_inventing_a_bc(self):
        params = replace(self.params, measurement_definition=load_definition(FIXTURE))
        params.measurement_definition['geometry']['regions'][0]['boundary_motion']['L006'] = 'follow_translation'
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.feb'
            write_febio(generate_wedge(params), params, path)
            (path.parent / 'parameters.json').write_text(json.dumps(asdict(params)), encoding='utf-8')
            report = read_inspection(path)
            self.assertFalse(report.warnings)
            self.assertTrue(any(group.kind == 'Verklebt' for group in report.groups))

    def test_nonpositive_initial_element_is_reported(self):
        root = ET.parse(self.path)
        elem = root.find('./Mesh/Elements/elem')
        values = elem.text.split(',')
        elem.text = ','.join([values[1], values[0], *values[2:]])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.feb'
            root.write(path)
            report = read_inspection(path)
            self.assertLess(report.min_jacobian, 0)
            self.assertTrue(any('Ausgangs-Jacobian' in warning for warning in report.warnings))

    def test_live_log_reads_trial_tail_without_modifying_files(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertIn('keinen Solver', solver_progress(directory))
            path = directory / 'trials/preload/stdout.txt'
            path.parent.mkdir(parents=True)
            content = 'x' * 100000 + '\nNonlinear solution status: time= 0.120716\nMax nr of iterations reached.\n'
            path.write_text(content)
            result = solver_progress(directory)
            self.assertIn('0.120716', result)
            self.assertIn('Max nr', result)
            self.assertLess(len(result), 300)
            self.assertEqual(path.read_text(), content)

    def test_reference_view_skips_displacement_export_and_highlights_rows(self):
        panel = MeshInspectionPanel()
        try:
            with patch('visualization.mesh_view.read_deformation', side_effect=AssertionError('no histories')):
                panel.load_mesh(self.path)
            self.assertIsNone(panel.view.history)
            self.assertEqual(panel.table.rowCount(), len(panel.inspection.groups))
            self.assertIn('measurement_R002', panel.warning.text())
            panel.table.selectRow(0)
            panel.view.draw()
            self.assertTrue(panel.overlays)
            self.assertTrue(any(artist.get_zorder() == 7 for artist in panel.overlays))
            panel.filters['Kontakt'].setChecked(True)
            panel.filters['Symmetrie'].setChecked(True)
            panel.view.draw()
            panel.load_mesh(self.path)
        finally:
            panel.timer.stop()
            panel.close()
            self.app.processEvents()

    def test_main_window_buttons_preview_mode_and_last_mesh_do_not_run_solver(self):
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = project / 'work/simulations/run_test/model.feb'
            path.parent.mkdir(parents=True)
            path.write_bytes(self.path.read_bytes())
            window = MainWindow(project)
            try:
                self.assertEqual(window.tabs.currentIndex(), 0)
                self.assertEqual(window.mesh_inspection.path, path)
                window.last_mesh_button.click()
                self.assertEqual(window.tabs.currentIndex(), window.mesh_tab_index)
                self.assertFalse(window.runner.busy)
                with patch.object(window.runner, 'start') as start:
                    window.mesh_button.click()
                    self.assertTrue(start.call_args.kwargs['generate_only'])
                    self.assertIsNone(start.call_args.args[1])
                window._busy_changed(True)
                self.assertFalse(window.mesh_button.isEnabled())
                self.assertTrue(window.last_mesh_button.isEnabled())
                window._busy_changed(False)
            finally:
                window.close()
                self.app.processEvents()

    def test_real_background_mesh_preparation_needs_no_febio(self):
        with TemporaryDirectory() as tmp:
            runner = SimulationRunner(PROJECT)
            ready, prepared, failures, solved = [], [], [], []
            runner.mesh_ready.connect(ready.append)
            runner.prepared.connect(prepared.append)
            runner.failed.connect(failures.append)
            runner.succeeded.connect(solved.append)
            path = Path(tmp) / 'preview'
            try:
                runner.start(self.params, None, directory=path, generate_only=True)
                deadline = time.monotonic() + 45
                while runner.busy and time.monotonic() < deadline:
                    QTest.qWait(30)
                self.assertFalse(runner.busy, 'Background mesh generation timed out')
                self.assertFalse(failures, failures)
                self.assertEqual(ready, [path / 'model.feb'])
                self.assertEqual(prepared, ready)
                self.assertFalse(solved)
                metadata = json.loads((path / 'case.json').read_text())
                self.assertEqual(metadata['febio_status'], 'not_run')
                self.assertFalse((path / 'solver.log').exists())
                self.assertFalse((path / 'trials').exists())
                self.assertTrue(read_inspection(path / 'model.feb').warnings)
            finally:
                if runner.busy:
                    runner.process.kill()
                    runner.process.waitForFinished(3000)


if __name__ == '__main__':
    unittest.main()
