from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from model.deformation import DeformationHistory, MeshTopology, read_deformation


class DeformationTests(unittest.TestCase):
    def test_interpolation_uses_actual_nonuniform_solver_times(self):
        values = np.asarray([[[0, 0, 0]], [[1, 2, 0]], [[5, 6, 0]]], dtype=float)
        history = DeformationHistory(np.asarray([0, 0.2, 1.0]), values)
        np.testing.assert_allclose(history.at_time(0.6), [[3, 4, 0]])
        np.testing.assert_allclose(history.at_time(-1), values[0])
        np.testing.assert_allclose(history.at_time(2), values[-1])

    def test_history_requires_exact_mesh_and_complete_frames(self):
        mesh = MeshTopology((7, 12), np.zeros((2, 3)), (), "test-hash")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nodal_displacements.csv"
            header = "# model_sha256=test-hash\ntime,node_id,ux_mm,uy_mm,uz_mm\n"
            data = "0,12,0,0,0\n0,7,0,0,0\n1,7,1,2,3\n1,12,4,5,6\n"
            path.write_text(header + data, encoding="utf-8")
            history = read_deformation(path, mesh)
            np.testing.assert_allclose(history.displacements[-1], [[1, 2, 3], [4, 5, 6]])
            for invalid in (header.replace("test-hash", "wrong-hash") + data,
                            header + data.replace("1,12,4,5,6\n", ""),
                            header + data + "1,12,4,5,6\n",
                            header + data.replace("1,7,1,2,3", "1,7,nan,2,3")):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_deformation(path, mesh)


if __name__ == "__main__":
    unittest.main()
