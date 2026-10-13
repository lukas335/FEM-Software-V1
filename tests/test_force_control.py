"""Focused controller, compatibility, and optional one-run GUI integration checks."""

from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from model.parameters import SimulationParameters
from model.study import StudyConfig
from solver.force_controller import AcceptedState, ForceController
from solver.prototype import baseline


class ForceControlTests(unittest.TestCase):
    def params(self, **kw):
        defaults = dict(drive_control="target_force", target_force_n=.02,
                        force_tolerance_abs_n=1e-4, force_tolerance_rel=.005)
        defaults.update(kw)
        return replace(baseline(), **defaults)

    def test_default_point_zero_two_newton_band_requires_contact(self):
        p = replace(baseline(), drive_control="target_force", target_force_n=1)
        self.assertEqual(p.force_tolerance_n, .02)
        for force in (.985, 1.015):
            c = ForceController(p, AcceptedState("preload", 0, 0, 1.05))
            request = c.propose_trial()
            self.assertEqual(c.observe_converged(AcceptedState("hit", request.target_drive_mm, force, 1.1)),
                             (True, "target_reached"))
        for force in (.97, 1.03):
            c = ForceController(p, AcceptedState("preload", 0, 0, 1.05))
            request = c.propose_trial()
            c.observe_converged(AcceptedState("outside", request.target_drive_mm, force, 1.1))
            self.assertNotEqual(c.reason, "target_reached")
        p = replace(p, target_force_n=.02)
        self.assertFalse(p.validate())
        c = ForceController(p, AcceptedState("preload", 0, 0, 1.05))
        request = c.propose_trial()
        c.observe_converged(AcceptedState("free", request.target_drive_mm, 0, 1.1))
        self.assertIsNone(c.reason)

    def controller(self, **kw):
        return ForceController(self.params(**kw), AcceptedState("preload", 0, 0, 1.05))

    def test_overshoot_retries_from_last_accepted_not_from_overshoot(self):
        c = self.controller()
        request = c.propose_trial()
        accepted, reason = c.observe_converged(AcceptedState("overshot", request.target_drive_mm, .04, 1.1))
        self.assertFalse(accepted)
        self.assertEqual(reason, "overshoot")
        request = c.propose_trial()
        self.assertEqual(request.source_checkpoint_id, "preload")
        self.assertAlmostEqual(request.target_drive_mm, .025)
        accepted, _ = c.observe_converged(AcceptedState("hit", .025, .02, 1.1))
        self.assertTrue(accepted)
        self.assertEqual(c.reason, "target_reached")
        self.assertIsNone(c.propose_trial())
        self.assertEqual([s.checkpoint_id for s in c.accepted], ["preload", "hit"])

    def test_limits_failure_and_nonmonotonic_response(self):
        for force, reason in ((0., "no_contact"), (.001, "displacement_limit")):
            c = self.controller(max_displacement_mm=.05, fit_start_mm=0, fit_end_mm=.05)
            c.propose_trial()
            c.observe_converged(AcceptedState("one", .05, force, 1.1))
            self.assertIsNone(c.propose_trial())
            self.assertEqual(c.reason, reason)
        c = self.controller()
        first = c.propose_trial()
        c.observe_failed()
        retry = c.propose_trial()
        self.assertEqual(retry.source_checkpoint_id, "preload")
        self.assertLess(retry.target_drive_mm, first.target_drive_mm)
        c.observe_converged(AcceptedState("one", retry.target_drive_mm, .01, 1.1))
        retry = c.propose_trial()
        accepted, reason = c.observe_converged(AcceptedState("drop", retry.target_drive_mm, .005, 1.15))
        self.assertFalse(accepted)
        self.assertEqual(reason, "nonmonotonic_response")

    def test_legacy_parameters_and_study_identity(self):
        p = baseline()
        legacy = asdict(p)
        fields = ("drive_control", "target_force_n", "force_tolerance_abs_n", "force_tolerance_rel",
                  "approach_step_mm", "min_drive_step_mm", "max_drive_step_mm", "max_control_trials")
        for name in fields:
            legacy.pop(name)
        legacy.pop('measurement_definition')  # Old snapshots predate optional editor imports.
        legacy.pop('cover_shell')  # Disabled cover did not exist in the old two-body snapshot.
        legacy['measurement_shell']['thickness_mm'] = legacy['measurement_shell'].pop('thickness_x_mm')
        legacy['measurement_shell'].pop('thickness_y_mm')
        self.assertEqual(SimulationParameters.from_dict(legacy), p)
        cfg = StudyConfig(p, (8.,), (1.25,), (15.,), (), pressure_mode="closed")
        expected = sha256(json.dumps(legacy, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
        self.assertEqual(cfg.cases()[0]["id"], expected)
        self.assertNotEqual(replace(cfg, base_parameters=replace(p, drive_control="target_force")).cases()[0]["id"], expected)
        for kw in ({"target_force_n": float("nan")}, {"force_tolerance_rel": 1},
                   {"min_drive_step_mm": .2}, {"max_control_trials": True}):
            self.assertTrue(replace(p, **kw).validate())

    def test_merge_rejects_mixed_branches(self):
        from solver.accepted_history import merge_accepted
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.log").write_text("Data Record #1\nTime = 1\nData = support_reaction\n1 0 0\n")
            entries = [dict(checkpoint_id="a", parent=None, solver_time=1, log="a.log"),
                       dict(checkpoint_id="b", parent="rejected", solver_time=2, log="a.log")]
            with self.assertRaisesRegex(ValueError, "mixed branches"):
                merge_accepted(root, entries)

    def test_gui_snapshot_and_result_loading_without_solver(self):
        from PySide6.QtWidgets import QApplication
        from app.main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        project = TemporaryDirectory()
        window = MainWindow(Path(project.name))
        try:
            window.drive_control.restore(self.params())
            self.assertEqual(window.parameters().drive_control, "target_force")
            window.study.take_model()
            self.assertEqual(window.study.config().base_parameters.target_force_n, .02)
            self.assertEqual(window.study.config().base_parameters.drive_control, "target_force")
            saved = window.project_root / "work/probes/target_force_closed_02/force_displacement.csv"
            if saved.is_file():
                self.assertTrue(window.results.load_case(saved, show_errors=False))
                self.assertIn("Zielkraft erreicht", window.results.case_label.text())
                self.assertGreater(window.results.animation.view.history.times[-1], 2)
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()
            project.cleanup()

    @unittest.skipUnless(os.environ.get("FEM_RUN_TARGET_FORCE_TESTS") == "1", "opt-in real FEBio run")
    def test_real_constant_pressure_target_with_rollback(self):
        import csv
        import time
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from app.simulation_runner import SimulationRunner, find_febio
        from model.deformation import read_mesh, read_deformation
        from model.cavity import read_cavity
        from model.results import read_force_curve
        from model.study_results import analyze_case
        app = QApplication.instance() or QApplication([])
        project = Path(__file__).resolve().parents[1]
        runner = SimulationRunner(project)
        p = replace(self.params(), target_force_n=.005, constant_pressure=True,
                    pressure_shell=replace(baseline().pressure_shell, material_name="sclera_demo"))
        finished, errors = [], []
        runner.succeeded.connect(finished.append)
        runner.failed.connect(errors.append)
        runner.start(p, find_febio())
        until = time.monotonic() + 180
        while runner.busy and time.monotonic() < until:
            QTest.qWait(50)
        self.assertFalse(runner.busy)
        self.assertFalse(errors, errors)
        self.assertEqual(len(finished), 1)
        directory = finished[0].parent
        metadata = json.loads((directory / "case.json").read_text())
        self.assertTrue(metadata["target_reached"])
        curve = read_force_curve(finished[0])
        self.assertLessEqual(abs(curve.force_n[-1] - p.target_force_n), p.force_tolerance_n)
        mesh = read_mesh(directory / "model.feb")
        read_deformation(directory / "nodal_displacements.csv", mesh)
        cavity = read_cavity(directory / "cavity_history.csv", mesh.input_hash)
        self.assertTrue(all(abs(v - p.pressure_mmhg) < 1e-8 for t, v in zip(cavity.time, cavity.pressure_mmhg) if t >= 1))
        with (directory / "control_history.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rejected = {f"trial_{int(row['trial']):04d}" for row in rows if row["reason"] == "overshoot"}
        self.assertTrue(rejected, "This fixture should exercise an actual overshoot")
        accepted = json.loads((directory / "accepted_path.json").read_text())
        self.assertFalse(rejected & {item["checkpoint_id"] for item in accepted})
        cfg = StudyConfig(p, (8.,), (1.25,), (15.,), (), minimum_fit_points=5)
        # This low-force fixture can have too few original loaded points for a fit;
        # that must remain an explicit invalid fit, never synthetic padding.
        try:
            analyze_case(directory, cfg)
        except ValueError as exc:
            self.assertIn("Fitpunkte", str(exc))
        print(f"Verified target-force run: {directory}")


if __name__ == "__main__":
    unittest.main()
