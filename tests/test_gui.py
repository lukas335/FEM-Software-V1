"""GUI workflow tests; enable FEM_RUN_SOLVER_TESTS=1 for real FEBio launches."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.main_window import MainWindow
from app.simulation_runner import SimulationRunner, find_febio
from model.parameters import SimulationParameters
from solver.prototype import baseline


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parameter_snapshot_validation(self):
        params = replace(baseline(), initial_gap_mm=1.3, pressure_mmhg=12.0, mesh_quality="fine")
        self.assertEqual(SimulationParameters.from_dict(asdict(params)), params)
        for name in ("pressure_mmhg", "max_displacement_mm", "fit_start_mm", "wedge_angle_deg"):
            with self.assertRaises(ValueError):
                SimulationParameters.from_dict(asdict(replace(params, **{name: float("nan")})))
        with self.assertRaises(ValueError):
            SimulationParameters.from_dict(asdict(replace(params, mesh_quality="unknown")))
        with self.assertRaises(ValueError):
            SimulationParameters.from_dict(asdict(replace(params, measurement_shell=replace(
                params.measurement_shell, material_name="unknown"))))

    def test_start_validation_preview_and_busy_state(self):
        window = MainWindow()
        self.assertFalse(window.runner.busy)
        self.assertEqual(window.tabs.currentIndex(), 0)
        old_curve = window.results.curve
        window.r1.setValue(10)
        window.initial_gap.setValue(1.4)
        QTest.qWait(150)
        self.assertEqual(window.preview.parameters.measurement_shell.radius_mm, 10)
        self.assertEqual(window.preview.parameters.initial_gap_mm, 1.4)
        self.assertAlmostEqual(window.preview.ax.patches[0].get_xy()[:, 0].max(), 0)
        self.assertAlmostEqual(window.preview.ax.patches[1].get_xy()[:, 0].min(), 1.4)
        # Bad thickness must not launch a subprocess.
        window.t1.setValue(12)
        with patch.object(window.runner, "start") as start:
            window.start_button.click()
            start.assert_not_called()
        window.t1.setValue(1.5)
        # Existing path suffices here: start is mocked, no executable is run.
        window.febio_path = Path(__file__)
        with patch.object(window.runner, "start") as start:
            window.start_button.click()
            self.assertEqual(start.call_args.args[0], window.parameters())
        window._busy_changed(True)
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.parameter_form.isEnabled())
        window._busy_changed(False)
        self.assertTrue(window.start_button.isEnabled())
        with patch.object(QMessageBox, "exec", return_value=0):
            window._simulation_failed("Test failure")
        self.assertIs(window.results.curve, old_curve)
        window.close()
        self.app.processEvents()  # Drain queued canvas draws while wrappers are still alive.

    def test_process_failures_unlock_runner(self):
        with TemporaryDirectory() as tmp:
            runner = SimulationRunner(Path(tmp))
            errors = []
            runner.failed.connect(errors.append)
            runner.busy = True
            with self.assertRaises(RuntimeError):
                runner.start(baseline(), Path(__file__))
            runner._process_error(QProcess.ProcessError.FailedToStart)
            self.assertFalse(runner.busy)
            self.assertEqual(len(errors), 1)
            runner.directory = Path(tmp)
            runner.output = "Example solver failure"
            runner.busy = True
            runner._finished(1, QProcess.ExitStatus.NormalExit)
            self.assertFalse(runner.busy)
            self.assertEqual(len(errors), 2)
            self.assertIn("Example solver failure", errors[-1])
            self.assertTrue((Path(tmp) / "launcher.log").is_file())

    def test_pressure_plot_auto_fit_and_legacy_switch_without_solver(self):
        window = MainWindow()
        try:
            path = window.project_root / "work/simulations/run_20261003_001240_eb698693/force_displacement.csv"
            if not path.is_file():
                self.skipTest("Saved cavity run not present")
            self.assertTrue(window.results.load_case(path))
            panel = window.results
            self.assertAlmostEqual(panel.contact.end_mm, 1)
            self.assertGreater(panel.contact.contact_mm, .77)
            self.assertLess(panel.contact.contact_mm, .81)
            panel.animation.set_time(1.9)
            self.assertAlmostEqual(panel.pressure_cursor.get_xdata()[0], 1.9)
            self.assertTrue(panel.pressure_point.get_visible())
            panel.include_preload.setChecked(True)
            panel.animation.set_time(.5)
            self.assertTrue(panel.pressure_cursor.get_visible())
            self.assertFalse(panel.time_cursor.get_visible())
            panel.include_preload.setChecked(False)
            self.assertFalse(panel.pressure_cursor.get_visible())
            with TemporaryDirectory() as tmp:
                csv = Path(tmp) / "force_displacement.csv"
                csv.write_text("time,drive_mm,reaction_full_N\n1,0,0\n1.5,.5,0\n2,1,0\n", encoding="utf-8")
                self.assertTrue(panel.load_case(csv))
                self.assertIsNone(panel.pressure_cursor)
                self.assertFalse(panel.include_preload.isEnabled())
                self.assertEqual(panel.values["k"].text(), "—")
                self.assertIsNone(panel.contact)
            window.umax.setValue(.5)
            self.assertFalse(window.parameters().validate())
            self.assertFalse(window.runner.busy)
        finally:
            window.close()
            self.app.processEvents()

    @unittest.skipUnless(os.environ.get("FEM_RUN_SOLVER_TESTS") == "1", "Real FEBio GUI test is opt-in")
    def test_two_real_gui_runs_use_changed_parameters_and_load_results(self):
        self.assertIsNotNone(find_febio())
        window = MainWindow()
        failures = []
        window.runner.failed.disconnect(window._simulation_failed)
        window.runner.failed.connect(failures.append)
        ticks = []
        heartbeat = QTimer()
        heartbeat.setInterval(20)
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start()
        directories = []
        try:
            for pressure, radius, gap in ((10.0, 8.0, 1.0), (20.0, 9.0, 1.2)):
                window.tabs.setCurrentIndex(0)
                window.pressure.setValue(pressure)
                window.r1.setValue(radius)
                window.initial_gap.setValue(gap)
                expected = asdict(window.parameters())
                count = len(ticks)
                window.start_button.click()
                self.assertTrue(window.runner.busy)
                deadline = time.monotonic() + 180
                while window.runner.busy and time.monotonic() < deadline:
                    QTest.qWait(50)
                self.assertFalse(window.runner.busy, "FEBio GUI run timed out")
                self.assertFalse(failures, "\n".join(failures))
                self.assertGreater(len(ticks), count)
                directory = window.runner.directory
                directories.append(directory)
                metadata = json.loads((directory / "case.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["parameters"], expected)
                self.assertEqual(metadata["febio_status"], "success")
                self.assertEqual(window.results.curve.source.parent, directory)
                self.assertEqual(window.tabs.currentIndex(), 1)
                self.assertIsNotNone(window.results.animation.view.history)
                self.assertEqual(window.results.animation.view.history.times[-1], 2.0)
                self.assertTrue(window.start_button.isEnabled())
                print(f"GUI roundtrip verified: {directory.name}, p={pressure}, R1={radius}, gap={gap}")
            self.assertNotEqual(*directories)
        finally:
            heartbeat.stop()
            if not window.runner.busy:
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
