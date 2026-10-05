"""One opt-in GUI-to-FEBio check for the constant-pressure switch."""

from dataclasses import asdict
import json
import os
import time
import unittest
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from model.parameters import SimulationParameters


@unittest.skipUnless(os.environ.get("FEM_RUN_CONSTANT_PRESSURE_TEST") == "1", "Real solver check is opt-in")
class ConstantPressureTest(unittest.TestCase):
    def test_switch_solver_export_and_display(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        failures = []
        window.runner.failed.disconnect(window._simulation_failed)
        window.runner.failed.connect(failures.append)
        try:
            self.assertFalse(window.constant_pressure.isChecked())
            old = asdict(window.parameters())
            old.pop("constant_pressure")
            self.assertFalse(SimulationParameters.from_dict(old).constant_pressure)
            window.constant_pressure.setChecked(True)
            self.assertTrue(window.parameters().constant_pressure)
            self.assertEqual(SimulationParameters.from_dict(asdict(window.parameters())), window.parameters())
            window.start_button.click()
            self.assertTrue(window.runner.busy)
            self.assertFalse(window.constant_pressure.isEnabled())
            deadline = time.monotonic() + 180
            while window.runner.busy and time.monotonic() < deadline:
                QTest.qWait(50)
            self.assertFalse(window.runner.busy, "Solver still running")
            self.assertFalse(failures, "\n".join(failures))
            directory = window.runner.directory
            metadata = json.loads((directory / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["febio_status"], "success")
            self.assertEqual(metadata["cavity_model"], "P0_preload_then_constant_pressure")
            self.assertTrue(metadata["parameters"]["constant_pressure"])
            root = ET.parse(directory / "model.feb").getroot()
            self.assertFalse(root.findall(".//constraint[@type='volume']"))
            self.assertFalse(root.findall(".//rigid_connector_data"))
            self.assertEqual(root.findtext("./LoadData/load_controller[@id='1']/points/point[last()]"), "2,1")
            animation = window.results.animation
            self.assertTrue(animation.constant_pressure)
            cavity = animation.cavity
            np.testing.assert_allclose(cavity.pressure_mmhg, 15 * np.minimum(cavity.time, 1), atol=1e-10)
            self.assertGreater(np.abs(cavity.volume_error).max(), 1e-8)
            self.assertNotIn("max_relative_volume_error", metadata["cavity_results"])
            self.assertEqual(window.tabs.currentIndex(), 1)
            animation.set_time(2)
            self.assertIn("Volumenänderung", animation.pressure_label.toolTip())
            self.assertIn("Druck konstant", window.results.case_label.text())
            self.assertIsNotNone(window.results.pressure_cursor)
            self.assertGreater(window.results.curve.force_n[-1], 0)
            print(f"Constant-pressure run: {directory}")
            print(f"P(end)={cavity.pressure_mmhg[-1]:g} mmHg; dV/V={cavity.volume_error[-1]:.6g}")
        finally:
            if not window.runner.busy:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
