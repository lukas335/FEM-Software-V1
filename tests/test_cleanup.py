"""Locked Windows artifacts must not turn exported results into solver failures."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from solver.cleanup import cleanup_successful_run, finalize_successful_run
from solver.force_controller import AcceptedState
from solver.prototype import baseline


class CleanupTests(unittest.TestCase):
    def test_locked_directory_is_warning_and_other_targets_are_still_cleaned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            kept = ('model.feb', 'parameters.json', 'case.json', 'solver.log', 'force_displacement.csv',
                    'nodal_displacements.csv', 'cavity_history.csv')
            for name in (*kept, 'model.xplt', 'stdout.txt'):
                (root / name).write_text('keep' if name in kept else 'temporary')
            for name in ('trials', 'checkpoints'):
                (root / name).mkdir()
                (root / name / 'state.dmp').write_text('temporary')
            from shutil import rmtree
            def locked(path):
                if path.name == 'trials':
                    raise PermissionError('[WinError 5] Zugriff verweigert')
                return rmtree(path)
            with patch('solver.cleanup.shutil.rmtree', side_effect=locked):
                warnings = cleanup_successful_run(root)
            self.assertEqual(len(warnings), 1)
            self.assertIn('trials', warnings[0])
            self.assertTrue((root / 'trials').exists())
            self.assertFalse((root / 'checkpoints').exists())
            self.assertFalse((root / 'model.xplt').exists())
            for name in kept:
                self.assertEqual((root / name).read_text(), 'keep')

    def test_locked_file_is_nonfatal_and_absent_targets_are_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(cleanup_successful_run(root), [])
            (root / 'model.xplt').write_text('temporary')
            with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
                warnings = cleanup_successful_run(root)
            self.assertEqual(len(warnings), 1)
            self.assertIn('model.xplt', warnings[0])

    def test_success_and_partial_statuses_survive_cleanup_failure(self):
        for status in ('success', 'partial'):
            with self.subTest(status=status), TemporaryDirectory() as tmp:
                root = Path(tmp)
                metadata = dict(febio_status=status, target_reached=status == 'success')
                stdout = io.StringIO()
                with patch('solver.cleanup.cleanup_successful_run', side_effect=PermissionError('locked')), redirect_stdout(stdout):
                    warnings = finalize_successful_run(root, metadata)
                saved = json.loads((root / 'case.json').read_text())
                self.assertEqual(saved['febio_status'], status)
                self.assertFalse(saved['temporary_files_cleaned'])
                self.assertEqual(saved['cleanup_warnings'], warnings)
                self.assertIn('STATUS: Warnung:', stdout.getvalue())

    def test_cleaned_flag_is_only_set_after_cleanup_and_failed_runs_are_not_cleaned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            def cleanup(path):
                saved = json.loads((path / 'case.json').read_text())
                self.assertEqual(saved['febio_status'], 'success')
                self.assertFalse(saved['temporary_files_cleaned'])
                return []
            with patch('solver.cleanup.cleanup_successful_run', side_effect=cleanup):
                finalize_successful_run(root, dict(febio_status='success'))
            self.assertTrue(json.loads((root / 'case.json').read_text())['temporary_files_cleaned'])
            with patch('solver.cleanup.cleanup_successful_run') as clean:
                with self.assertRaises(ValueError):
                    finalize_successful_run(root, dict(febio_status='solver_failed'))
                clean.assert_not_called()

    def test_metadata_export_failure_still_fails_before_cleanup(self):
        with TemporaryDirectory() as tmp:
            with patch('solver.cleanup.atomic_text', side_effect=PermissionError('cannot save results')):
                with patch('solver.cleanup.cleanup_successful_run') as clean:
                    with self.assertRaises(PermissionError):
                        finalize_successful_run(Path(tmp), dict(febio_status='success'))
                    clean.assert_not_called()

    def test_second_metadata_write_failure_is_nonfatal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            from solver.cleanup import atomic_text
            count = 0
            def save(path, value):
                nonlocal count
                count += 1
                if count == 2:
                    raise PermissionError('metadata now locked')
                return atomic_text(path, value)
            with patch('solver.cleanup.atomic_text', side_effect=save), redirect_stdout(io.StringIO()):
                warnings = finalize_successful_run(root, dict(febio_status='success'))
            self.assertIn('Aufräumstatus', warnings[0])
            saved = json.loads((root / 'case.json').read_text())
            self.assertEqual(saved['febio_status'], 'success')
            self.assertFalse(saved['temporary_files_cleaned'])

    def test_cleanup_does_not_follow_redirected_targets_outside_the_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            resolve = Path.resolve
            def redirected(path, *args, **kwargs):
                if path == root / 'trials':
                    return root.parent / 'outside' / 'trials'
                return resolve(path, *args, **kwargs)
            with patch.object(Path, 'resolve', new=redirected), patch('solver.cleanup.shutil.rmtree') as remove:
                warnings = cleanup_successful_run(root)
                remove.assert_not_called()
            self.assertEqual(len(warnings), 1)
            self.assertIn('Verknüpfung', warnings[0])

    def test_displacement_workflow_returns_success_with_cleanup_warning_without_febio(self):
        from solver import prototype
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'fake-febio.exe'
            executable.touch()
            mesh = SimpleNamespace(nodes={1: (0, 0, 0)}, elements={'measurement_shell': [(1, (1, 2, 3, 4))]},
                                   node_sets={'measurement_support': [1], 'measurement_inner_pole': [2]})
            def run(*args, **kwargs):
                (root / 'solver.log').write_text('N O R M A L   T E R M I N A T I O N')
                return SimpleNamespace(returncode=0, stdout='FEBio version = test', stderr='')
            with patch('sys.argv', ['prototype', '--febio', str(executable), '--output', str(root), '--compact']), \
                 patch.object(prototype, 'generate_wedge', return_value=mesh), \
                 patch.object(prototype, 'write_febio'), \
                 patch.object(prototype.subprocess, 'run', side_effect=run), \
                 patch.object(prototype, 'parse_reaction_log', return_value=[(1, 0, 0), (2, .2, .02)]), \
                 patch('solver.cleanup.cleanup_successful_run', side_effect=PermissionError('locked')), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(prototype.main(), 0)
            self.assertTrue((root / 'force_displacement.csv').is_file())
            saved = json.loads((root / 'case.json').read_text())
            self.assertEqual(saved['febio_status'], 'success')
            self.assertTrue(saved['cleanup_warnings'])

    def test_force_target_workflow_returns_success_with_cleanup_warning_without_febio(self):
        from solver.target_force import run_target_force
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            params = replace(baseline(), drive_control='target_force', target_force_n=.02, force_tolerance_abs_n=.0001)
            class FakeSession:
                def __init__(self, *args, **kwargs):
                    self.counter = 0
                    self.support, self.measurement_pole = {1}, 2
                    self.preload_gap_mm = 1.
                    self.entries = {'preload': dict(solver_time=1.05)}
                def initialize(self):
                    return AcceptedState('preload', 0, 0, 1.05)
                def advance(self, parent, drive):
                    self.counter += 1
                    self.entries['hit'] = dict(solver_time=1.1)
                    return AcceptedState('hit', drive, .02, 1.1)
            with patch('solver.target_force.FEBioSession', FakeSession), \
                 patch('solver.target_force.merge_accepted', return_value=root / 'solver.log'), \
                 patch('solver.prototype.parse_reaction_log', return_value=[(1.05, 0, 0), (1.1, .02, .02)]), \
                 patch('solver.cleanup.cleanup_successful_run', side_effect=PermissionError('locked')), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(run_target_force(root, params, Path('unused.exe'), {}, retain_visualization=False), 0)
            saved = json.loads((root / 'case.json').read_text())
            self.assertEqual(saved['febio_status'], 'success')
            self.assertTrue(saved['target_reached'])
            self.assertFalse(saved['temporary_files_cleaned'])


class CleanupGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_runner_emits_success_and_results_panel_shows_warning(self):
        from PySide6.QtCore import QProcess
        from app.simulation_runner import SimulationRunner
        from app.main_window import MainWindow
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            curve = root / 'force_displacement.csv'
            curve.write_text('time,drive_mm,reaction_full_N\n1,0,0\n2,.2,.02\n')
            metadata = dict(febio_status='success', visualization_data=False,
                            cleanup_warnings=['Temporäre Dateien nicht gelöscht: locked'], parameters={})
            (root / 'case.json').write_text(json.dumps(metadata))
            runner = SimulationRunner(root)
            runner.directory, runner.busy = root, True
            succeeded, failed = [], []
            runner.succeeded.connect(succeeded.append)
            runner.failed.connect(failed.append)
            with patch('app.simulation_runner.read_mesh'):
                runner._finished(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(succeeded, [curve])
            self.assertFalse(failed)
            self.assertFalse(runner.busy)
            window = MainWindow(root)
            try:
                window._simulation_finished(curve)
                self.assertEqual(window.tabs.currentIndex(), 1)
                self.assertIn('abgeschlossen', window.status.text())
                self.assertIn('Aufräumwarnung', window.status.text())
                self.assertIn('Aufräumwarnung', window.results.case_label.text())
                self.assertIn('locked', window.results.case_label.toolTip())
                self.assertIsNotNone(window.results.curve)
            finally:
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
