"""One opt-in end-to-end check for the uncalibrated sclera material."""

import json
import os
from pathlib import Path
import time
import unittest
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from model.deformation import read_mesh
from model.cavity import read_cavity


@unittest.skipUnless(os.environ.get("FEM_RUN_SCLERA_TEST") == "1", "Real sclera solver check is opt-in")
class ScleraSmokeTest(unittest.TestCase):
    def test_pressure_shell_material_axes_and_solver(self):
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        failures = []
        window.runner.failed.disconnect(window._simulation_failed)
        window.runner.failed.connect(failures.append)
        self.assertEqual(window.material1.currentData(), "silicone_A30")
        self.assertEqual(window.material2.currentData(), "sclera_demo")
        window.start_button.click()
        self.assertTrue(window.runner.busy)
        deadline = time.monotonic() + 180
        while window.runner.busy and time.monotonic() < deadline:
            QTest.qWait(50)
        self.assertFalse(window.runner.busy, "Solver still running")
        try:
            self.assertFalse(failures, "\n".join(failures))
            directory = window.runner.directory
            root = ET.parse(directory / "model.feb").getroot()
            self.assertEqual(root.find("./Material/material[@id='1']").get("type"), "neo-Hookean")
            self.assertEqual(root.find("./Material/material[@id='2']").get("type"), "Mooney-Rivlin von Mises Fibers")
            mesh = read_mesh(directory / "model.feb")
            nodes = dict(zip(mesh.node_ids, mesh.coordinates))
            elements = root.findall("./Mesh/Elements[@name='pressure_shell']/elem")
            axes = root.findall("./MeshData/ElementData[@type='mat_axis']/elem")
            self.assertEqual(len(axes), len(elements))
            center = window.r2.value() + window.initial_gap.value()
            for element, axis in zip(elements, axes):
                n = np.mean([nodes[int(node)] for node in element.text.split(",")], axis=0) - [center, 0, 0]
                n /= np.linalg.norm(n)
                a = np.fromstring(axis.findtext("a"), sep=",")
                d = np.fromstring(axis.findtext("d"), sep=",")
                np.testing.assert_allclose([a @ n, d @ n, a @ d], 0, atol=1e-9)
                np.testing.assert_allclose([a @ a, d @ d], 1, atol=1e-9)
            metadata = json.loads((directory / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["febio_status"], "success")
            self.assertIn("sclera_demo", metadata["materials"])
            self.assertEqual(window.tabs.currentIndex(), 1)
            self.assertEqual(window.results.animation.view.history.times[-1], 2)
            cavity = read_cavity(directory / "cavity_history.csv", mesh.input_hash)
            pressure_end = np.flatnonzero(np.isclose(cavity.time, 1.0))[0]
            self.assertAlmostEqual(cavity.pressure_mmhg[pressure_end], window.pressure.value())
            self.assertGreater(cavity.pressure_mmhg[-1], cavity.pressure_mmhg[pressure_end])
            self.assertLess(np.abs(cavity.volume_error[pressure_end:]).max(), 1e-4)
            support = set(map(int, root.findtext("./Mesh/NodeSet[@name='pressure_support']").split(",")))
            domain_nodes = {int(n) for element in elements for n in element.text.split(",")}
            self.assertEqual(support, {n for n in domain_nodes if nodes[n][0] >= center - 1e-8})
            fixed = np.isin(mesh.node_ids, list(support))
            np.testing.assert_allclose(window.results.animation.view.history.displacements[:, fixed], 0, atol=1e-12)
            curve = window.results.curve
            self.assertLess(abs(curve.force_n[0]), 1e-6)
            self.assertGreater(curve.force_n[-1], 1e-5)
            print(f"Sclera run: {directory}")
            print(f"Pressure-end reaction: {curve.force_n[0]:.6g} N; final: {curve.force_n[-1]:.6g} N")
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
