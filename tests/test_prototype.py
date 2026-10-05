import importlib.util
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

from geometry.mesh_generator import generate_wedge
from solver.febio_writer import write_febio
from solver.prototype import baseline, parse_reaction_log


class PrototypeTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("gmsh"), "Gmsh is not installed")
    def test_mesh_and_febio_input(self):
        params = replace(baseline(), initial_gap_mm=1.3)
        mesh = generate_wedge(params)
        self.assertTrue(mesh.elements["measurement_shell"])
        self.assertTrue(mesh.elements["pressure_shell"])
        required = ("measurement_contact", "pressure_contact", "pressure_inner_surface",
                    "measurement_support", "pressure_support")
        self.assertTrue(all(mesh.surfaces[name] for name in required))
        self.assertTrue(all(abs(mesh.nodes[n][0] + 8) < 1e-8 for n in mesh.node_sets["measurement_support"]))
        pressure_center = params.pressure_shell.radius_mm + params.initial_gap_mm
        pressure_nodes = {n for _, conn in mesh.elements["pressure_shell"] for n in conn}
        rear_nodes = {n for n in pressure_nodes if mesh.nodes[n][0] >= pressure_center - 1e-8}
        self.assertEqual(set(mesh.node_sets["pressure_support"]), rear_nodes)
        self.assertAlmostEqual(max(mesh.nodes[n][0] for n in rear_nodes),
                               pressure_center + params.pressure_shell.radius_mm)
        measurement_tip = max(mesh.nodes[n][0] for face in mesh.surfaces["measurement_contact"] for n in face)
        pressure_tip = min(mesh.nodes[n][0] for face in mesh.surfaces["pressure_contact"] for n in face)
        self.assertAlmostEqual(pressure_tip - measurement_tip, params.initial_gap_mm)
        for elements in mesh.elements.values():
            for _, connectivity in elements:
                a, b, c, d = (mesh.nodes[n] for n in connectivity)
                ab = tuple(b[i] - a[i] for i in range(3))
                ac = tuple(c[i] - a[i] for i in range(3))
                ad = tuple(d[i] - a[i] for i in range(3))
                determinant = (ab[0] * (ac[1]*ad[2] - ac[2]*ad[1])
                               - ab[1] * (ac[0]*ad[2] - ac[2]*ad[0])
                               + ab[2] * (ac[0]*ad[1] - ac[1]*ad[0]))
                self.assertGreater(determinant, 0)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.feb"
            write_febio(mesh, params, path)
            root = ET.parse(path).getroot()
            self.assertEqual(len(root.findall("./Material/material")), 2)
            self.assertEqual(len(root.findall("./Step/step")), 2)
            steps = root.findall("./Step/step")
            self.assertIsNone(steps[0].find("./Constraints/constraint[@type='volume']"))
            self.assertEqual(steps[1].find("./Constraints/constraint[@type='volume']").get("surface"), "fluid_cavity")
            self.assertEqual(root.find("./Contact/contact").attrib["type"], "sliding-elastic")
            self.assertEqual(len(root.findall("./LoadData/load_controller")), 2)
            self.assertEqual(root.find("./Loads/surface_load/pressure").attrib["lc"], "1")
            nodeset = root.find("./Mesh/NodeSet[@name='measurement_support']")
            self.assertEqual(set(map(int, nodeset.text.split(","))), set(mesh.node_sets["measurement_support"]))
            self.assertEqual(len(nodeset), 0)
            self.assertIsNotNone(root.find("./Boundary/bc/x_dof"))
            for step in root.findall("./Step/step"):
                self.assertIsNotNone(step.find("./Control/solver"))
                self.assertIsNotNone(step.find("./Boundary/bc/value"))
            for name, sign in (("pressure_inner_surface", -1), ("pressure_contact", 1), ("fluid_cavity", 1)):
                surface = root.find(f"./Mesh/Surface[@name='{name}']")
                self.assertIsNotNone(surface)
                for facet in surface:
                    a, b, c = (mesh.nodes[int(n)] for n in facet.text.split(","))
                    ab = tuple(b[i]-a[i] for i in range(3))
                    ac = tuple(c[i]-a[i] for i in range(3))
                    normal = (ab[1]*ac[2]-ab[2]*ac[1],
                              ab[2]*ac[0]-ab[0]*ac[2],
                              ab[0]*ac[1]-ab[1]*ac[0])
                    midpoint = tuple((a[i]+b[i]+c[i])/3 for i in range(3))
                    radial = (midpoint[0]-pressure_center, midpoint[1], midpoint[2])
                    self.assertGreater(sign * sum(normal[i]*radial[i] for i in range(3)), 0)

    def test_initial_gap_validation(self):
        for gap in (-0.1, float("nan"), float("inf")):
            self.assertTrue(replace(baseline(), initial_gap_mm=gap).validate())
        for gap in (0.0, 0.1, 1.0):
            self.assertFalse(replace(baseline(), initial_gap_mm=gap).validate())

    def test_reaction_log_sums_wedge_forces(self):
        sample = '''Data Record #1
Step = 10
Time = 1.0
Data = "support_reaction"
1 0.0 0.0
2 0.0 0.0
Data Record #2
Step = 11
Time = 1.1
Data = "support_reaction"
1 0.1 -0.2
2 0.1 -0.3
'''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "solver.log"
            path.write_text(sample, encoding="utf-8")
            history = parse_reaction_log(path, {1, 2}, 3.0)
        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(history[-1][1], 0.1)
        self.assertAlmostEqual(history[-1][2], 60.0)


if __name__ == "__main__":
    unittest.main()
