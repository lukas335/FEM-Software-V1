"""Optional acceptance tests against the locally computed 1 mm baseline.

Generate the fixture with solver.prototype --output work/simulations/baseline_gap1mm.
These checks inspect solver results; they do not launch FEBio during unit tests.
"""

import json
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree as ET

import numpy as np

from model.deformation import read_deformation, read_mesh
from model.results import read_force_curve


CASE = Path(__file__).resolve().parents[1] / "work/simulations/baseline_gap1mm"


@unittest.skipUnless((CASE / "case.json").is_file(), "Corrected FEBio baseline not computed")
class BaselinePressureTests(unittest.TestCase):
    def test_free_pressure_inflation_then_contact(self):
        metadata = json.loads((CASE / "case.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["febio_status"], "success")
        self.assertEqual(metadata["parameters"]["initial_gap_mm"], 1.0)
        mesh = read_mesh(CASE / "model.feb")
        history = read_deformation(CASE / "nodal_displacements.csv", mesh)
        root = ET.parse(CASE / "model.feb").getroot()
        lookup = {node: index for index, node in enumerate(mesh.node_ids)}

        def surface_indices(name):
            surface = root.find(f"./Mesh/Surface[@name='{name}']")
            return sorted({lookup[int(node)] for face in surface for node in face.text.split(",")})

        measurement = surface_indices("measurement_contact")
        pressure = surface_indices("pressure_contact")
        pressure_frames = history.times <= 1.0 + 1e-8
        self.assertGreaterEqual(np.count_nonzero(pressure_frames), 11)
        self.assertTrue(np.any(np.isclose(history.times, 1.0)))
        for frame in history.displacements[pressure_frames]:
            xyz = mesh.coordinates + frame
            # Axial bounding planes are a conservative non-contact check for
            # all outer surface triangles, not only the displayed cross-section.
            gap = xyz[pressure, 0].min() - xyz[measurement, 0].max()
            self.assertGreater(gap, 0.5)

        contact_frames = {}
        log = (CASE / "solver.log").read_text(encoding="utf-8", errors="replace")
        expected_faces = len(root.find("./Mesh/Surface[@name='measurement_contact']"))
        for block in re.split(r"(?=Data Record\s*#\d+)", log):
            if not re.search(r'Data\s*=\s*["\']?contact_state\b', block):
                continue
            time = float(re.search(r"(?m)^\s*Time\s*=\s*([\d.eE+-]+)", block).group(1))
            values = {}
            for line in block.splitlines():
                fields = line.split()
                if len(fields) == 3 and fields[0].isdigit():
                    values[int(fields[0])] = float(fields[2])
            self.assertEqual(set(values), set(range(1, expected_faces + 1)))
            self.assertTrue(all(np.isfinite(value) for value in values.values()))
            contact_frames[time] = max(abs(value) for value in values.values())
        self.assertIn(1.0, contact_frames)
        self.assertIn(2.0, contact_frames)
        for time in history.times:
            self.assertIn(float(time), contact_frames)
        self.assertLessEqual(max(value for time, value in contact_frames.items() if time <= 1.0), 1e-10)
        self.assertGreater(contact_frames[2.0], 1e-6)

        curve = read_force_curve(CASE / "force_displacement.csv")
        self.assertAlmostEqual(curve.force_n[0], 0.0, delta=1e-8)
        self.assertGreater(curve.force_n[-1], 0.01)
        first_loaded = next(u for u, force in zip(curve.displacement_mm, curve.force_n) if force > 1e-5)
        self.assertGreater(first_loaded, 0.6)
        self.assertLess(first_loaded, 0.7)
        self.assertGreater(metadata["parameters"]["fit_start_mm"], first_loaded)


if __name__ == "__main__":
    unittest.main()
