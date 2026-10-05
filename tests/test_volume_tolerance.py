"""Bounded volume acceptance; no solver runs or changes to physical constraints."""

import unittest

from model.cavity import assess_volume_error


class VolumeToleranceTests(unittest.TestCase):
    def test_closed_cavity_warning_and_hard_limit(self):
        self.assertEqual(assess_volume_error(1e-4, constant_pressure=False), [])
        self.assertTrue(assess_volume_error(.000120536, constant_pressure=False))
        self.assertTrue(assess_volume_error(.001, constant_pressure=False))
        with self.assertRaisesRegex(ValueError, "0.1%"):
            assess_volume_error(.00101, constant_pressure=False)

    def test_constant_pressure_is_allowed_to_change_volume(self):
        self.assertEqual(assess_volume_error(.02, constant_pressure=True), [])
        with self.assertRaises(ValueError):
            assess_volume_error(float("nan"), constant_pressure=True)


if __name__ == "__main__":
    unittest.main()
