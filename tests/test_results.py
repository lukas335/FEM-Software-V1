from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from model.results import ForceCurve, automatic_fit_range, fit_stiffness, read_force_curve


class ResultsTests(unittest.TestCase):
    def test_automatic_contact_fit_to_maximum(self):
        curve = ForceCurve(tuple(range(7)), (0, .2, .4, .6, .7, .8, 1),
                           (1e-13, -1e-13, 0, .1, .2, .3, .5), Path("auto.csv"))
        interval = automatic_fit_range(curve)
        self.assertAlmostEqual(interval.contact_mm, .5)
        self.assertEqual((interval.start_mm, interval.end_mm), (.6, 1))
        fit = fit_stiffness(curve, interval.start_mm, interval.end_mm)
        self.assertAlmostEqual(fit.stiffness, 1)
        self.assertEqual(fit.count, 2)
        # No contact, isolated spike, too few loaded points, and initial preload.
        for forces in ((0,) * 7, (0, .1, 0, 0, 0, 0, 0),
                       (0, 0, 0, 0, 0, .1, .2), (.1, .2, .3, .4, .5, .6, .7)):
            with self.assertRaises(ValueError):
                automatic_fit_range(ForceCurve(curve.time, curve.displacement_mm, forces, curve.source))

    def test_fit_preserves_preload_and_uses_requested_interval(self):
        curve = ForceCurve((0, 1, 2, 3, 4), (0, 0.25, 0.5, 0.75, 1),
                           (99, 1.5, 2, 2.5, 99), Path("synthetic.csv"))
        result = fit_stiffness(curve, 0.25, 0.75)
        self.assertAlmostEqual(result.stiffness, 2)
        self.assertIsNone(result.intercept)
        self.assertAlmostEqual(result.compliance, 0.5)
        self.assertAlmostEqual(result.delta_force, 1)
        self.assertAlmostEqual(result.delta_displacement, 0.5)
        self.assertIsNone(result.r_squared)
        with self.assertRaises(ValueError):
            fit_stiffness(curve, 0.3, 0.7)
        with self.assertRaises(ValueError):
            fit_stiffness(curve, 0.8, 0.2)

    def test_constant_force_has_no_misleading_compliance_or_r_squared(self):
        curve = ForceCurve((0, 1, 2), (0, 0.5, 1), (0.3, 0.3, 0.3), Path("constant.csv"))
        result = fit_stiffness(curve, 0, 1)
        self.assertEqual(result.stiffness, 0)
        self.assertIsNone(result.compliance)
        self.assertIsNone(result.r_squared)

    def test_csv_validation_and_full_force_is_not_scaled_again(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "force_displacement.csv"
            header = "time,drive_mm,reaction_full_N\n"
            path.write_text(header + "1,0,0.03\n2,1,0.15\n", encoding="utf-8")
            curve = read_force_curve(path)
            self.assertEqual(curve.force_n, (0.03, 0.15))
            for invalid in ("1,0,nan\n2,1,0.15\n", "1,0,0.03\n"):
                path.write_text(header + invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_force_curve(path)


if __name__ == "__main__":
    unittest.main()
