"""Focused study checks; no Gmsh or FEBio processes are started."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.study_runner import StudyRunner
from model.results import ForceCurve
from model.study import ReferenceGeometry, StudyConfig
from model.study_results import analyze_curve, check_preload, rank_geometries
from model.study_results import refresh_automatic_fits
from model.study_storage import StudyStorage, atomic_text
from solver.prototype import baseline


def config():
    return StudyConfig(replace(baseline(), max_displacement_mm=3), (8., 10.), (1.25,), (15., 25., 35.),
                       (ReferenceGeometry("Eigene Referenz", 8., 1.25),), .1, 1., pressure_mode="constant", fit_mode="manual")


class StudyTests(unittest.TestCase):
    def test_automatic_common_interval_and_legacy_mode(self):
        cfg = replace(config(), fit_mode="automatic", fit_end_after_contact_mm=100)
        legacy = asdict(cfg)
        legacy.pop("fit_mode")
        self.assertEqual(StudyConfig.from_dict(legacy).fit_mode, "manual")
        self.assertEqual(cfg.cases()[0]["parameters"], config().cases()[0]["parameters"])
        with TemporaryDirectory() as tmp:
            curves = []
            cases = cfg.cases()[:2]
            for i, (offset, end) in enumerate(((.5, 2.), (.8, 1.7))):
                x = np.r_[0, offset, np.linspace(offset + .05, end, 30)]
                y = np.maximum(x - offset, 0) * (i + 1)
                curve = ForceCurve(tuple(x), tuple(x), tuple(y), Path(tmp) / str(i) / "force_displacement.csv")
                curve.source.parent.mkdir()
                curve.source.write_text("time,drive_mm,reaction_full_N\n" + "\n".join(
                    f"{t},{t},{f}" for t, f in zip(x, y)), encoding="utf-8")
                cases[i].update(status="success", result=analyze_curve(curve, cfg), attempts=[str(i)], auto_fit_eligible=True)
                curves.append(curve)
            refresh_automatic_fits(cases, cfg, lambda p: Path(tmp) / p)
            self.assertTrue(all(c["status"] == "success" for c in cases))
            bounds = [(c["result"]["fit_start_delta_mm"], c["result"]["fit_end_delta_mm"]) for c in cases]
            self.assertEqual(bounds[0], bounds[1])
            self.assertAlmostEqual(bounds[0][1], .9)
            self.assertAlmostEqual(cases[0]["result"]["stiffness"], 1)
            self.assertAlmostEqual(cases[1]["result"]["stiffness"], 2)
            before = [c["result"] for c in cases]
            refresh_automatic_fits(list(reversed(cases)), cfg, lambda p: Path(tmp) / p)
            self.assertEqual(before, [c["result"] for c in cases])

    def test_configuration_snapshot_and_deduplication(self):
        cfg = config()
        cfg = replace(cfg, base_parameters=replace(cfg.base_parameters,
            pressure_shell=replace(cfg.base_parameters.pressure_shell, radius_mm=12, thickness_mm=.8,
                                   material_name="sclera_demo"), initial_gap_mm=1.7))
        self.assertEqual(StudyConfig.from_dict(asdict(cfg)), cfg)
        cases = cfg.cases()
        self.assertEqual(len(cases), 6)
        self.assertEqual(sum(bool(c["references"]) for c in cases), 3)
        for case in cases:
            self.assertEqual(case["parameters"]["pressure_shell"]["radius_mm"], 12)
            self.assertEqual(case["parameters"]["initial_gap_mm"], 1.7)
            self.assertTrue(case["parameters"]["constant_pressure"])
        none = replace(cfg, pressure_mode="none").cases()
        self.assertEqual(len(none), 2)
        self.assertTrue(all(c["parameters"]["pressure_mmhg"] == 0 for c in none))
        self.assertTrue(all(c["parameters"]["constant_pressure"] for c in none))
        self.assertFalse(replace(cfg, pressure_mode="closed").cases()[0]["parameters"]["constant_pressure"])
        with self.assertRaises(ValueError):
            replace(cfg, measurement_thicknesses_mm=(20,)).cases()

    def test_weighted_fit_ignores_sampling_density(self):
        cfg = config()
        x = np.r_[0, .25, .5, np.linspace(.55, 1.7, 24)]
        y = np.maximum(0, 2 * (x - .5))
        curve = ForceCurve(tuple(x), tuple(x), tuple(y), Path("synthetic.csv"))
        fit = analyze_curve(curve, cfg)
        self.assertAlmostEqual(fit["stiffness"], 2)
        self.assertAlmostEqual(fit["contact_mm"], .5)
        # Add many samples exactly on a piecewise-linear curve: integral unchanged.
        y += np.maximum(x - 1.1, 0) * .7
        first = analyze_curve(ForceCurve(tuple(x), tuple(x), tuple(y), curve.source), cfg)
        dense_x = np.unique(np.r_[x, np.linspace(1.2, 1.4, 200)])
        second = analyze_curve(ForceCurve(tuple(dense_x), tuple(dense_x), tuple(np.interp(dense_x, x, y)), curve.source), cfg)
        self.assertAlmostEqual(first["stiffness"], second["stiffness"], places=10)
        self.assertAlmostEqual(first["r_squared"], second["r_squared"], places=10)
        for bad in (replace(cfg, fit_end_after_contact_mm=2), replace(cfg, minimum_fit_points=100)):
            with self.assertRaises(ValueError):
                analyze_curve(curve, bad)

    def test_ranking_requires_complete_monotonic_results(self):
        cfg = config()
        cases = cfg.cases()
        for case in cases:
            p = case["parameters"]["pressure_mmhg"]
            case.update(status="success", result={"stiffness": p / 100, "warnings": []})
        rows = rank_geometries(cases, cfg)
        self.assertAlmostEqual(rows[0]["sensitivity"], .01)
        self.assertTrue(rows[0]["preferred"])
        cases[1]["status"] = "invalid"
        row = next(r for r in rank_geometries(cases, cfg) if r["radius_mm"] == 8)
        self.assertIsNone(row["sensitivity"])
        self.assertFalse(row["preferred"])

    def test_atomic_replace_retries_and_journal_survives_locked_mirrors(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "value.json"
            real_replace = os.replace
            calls = []
            def transient(source, target):
                calls.append(target)
                if len(calls) < 3:
                    raise PermissionError(13, "Windows lock")
                return real_replace(source, target)
            with patch("model.study_storage.os.replace", side_effect=transient), patch("model.study_storage.time.sleep"):
                atomic_text(path, "complete")
            self.assertEqual(path.read_text(), "complete")
            self.assertEqual(len(calls), 3)
            store = StudyStorage.create(Path(tmp), config())
            def locked(source, target):
                if Path(target).name in ("cases.json", "results.csv", "ranking.csv"):
                    raise PermissionError(13, "Windows lock")
                return real_replace(source, target)
            store.cases[0].update(status="invalid", error="Gesicherte Meldung")
            with patch("model.study_storage.os.replace", side_effect=locked), patch("model.study_storage.time.sleep"):
                store.save()
            self.assertEqual(len(store.warnings), 3)
            loaded = StudyStorage.open(store.directory)
            self.assertEqual(loaded.cases[0]["status"], "invalid")
            self.assertEqual(loaded.cases[0]["error"], "Gesicherte Meldung")
            loaded.cases[1]["status"] = "running"
            loaded.save()
            self.assertEqual(StudyStorage.open(store.directory).cases[1]["status"], "pending")

    def test_saved_preload_and_storage_failure_recovery(self):
        # Inspect an existing solver run, do not launch a new one.
        fixture = Path("work/simulations/run_20261003_003729_215fc235")
        if not fixture.is_dir():
            self.skipTest("Saved fixture missing")
        check_preload(fixture / "solver.log")
        from model.parameters import SimulationParameters
        metadata = json.loads((fixture / "case.json").read_text(encoding="utf-8"))
        p = SimulationParameters.from_dict(metadata["parameters"])
        cfg = StudyConfig(p, (p.measurement_shell.radius_mm,), (p.measurement_shell.thickness_mm,),
                          (p.pressure_mmhg,), (), .001, .19, pressure_mode="constant", fit_mode="manual")
        import shutil
        with TemporaryDirectory() as tmp:
            store = StudyStorage.create(Path(tmp), cfg)
            case = store.cases[0]
            target = store.new_attempt(case)
            target.mkdir(parents=True)
            for name in ("case.json", "model.feb", "force_displacement.csv", "nodal_displacements.csv", "cavity_history.csv", "solver.log"):
                shutil.copy2(fixture / name, target / name)
            case["status"] = "running"
            store.save()
            loaded = StudyStorage.open(store.directory)
            self.assertEqual(loaded.cases[0]["status"], "success")
            self.assertGreater(loaded.cases[0]["result"]["stiffness"], 0)
            before = (loaded.directory / "study.json").read_bytes()
            derived = loaded.automatic_copy()
            self.assertNotEqual(loaded.directory, derived.directory)
            self.assertEqual(derived.config.fit_mode, "automatic")
            self.assertEqual(derived.cases[0]["status"], "success")
            self.assertAlmostEqual(derived.cases[0]["result"]["fit_end_delta_mm"],
                                   1 - derived.cases[0]["result"]["contact_mm"])
            self.assertEqual((loaded.directory / "study.json").read_bytes(), before)

    def test_runner_stop_resume_and_no_duplicate_success(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as tmp:
            store = StudyStorage.create(Path(tmp), config())
            runner = StudyRunner(Path(tmp))
            errors, launched = [], []
            runner.error.connect(errors.append)
            def fake_start(params, febio, *, directory):
                directory.mkdir(parents=True)
                for name in ("case.json", "model.feb", "force_displacement.csv"):
                    (directory / name).write_text("fixture", encoding="utf-8")
                launched.append(params)
                if len(launched) == 1:
                    runner.stop_after_current()
                QTimer.singleShot(0, lambda: runner.solver.succeeded.emit(directory / "force_displacement.csv"))
            analysis = {"stiffness": .1, "warnings": []}
            with patch.object(runner.solver, "start", side_effect=fake_start), patch("app.study_runner.analyze_case", return_value=analysis):
                runner.start(store, Path(__file__), "references")
                for _ in range(100):
                    if not runner.busy:
                        break
                    QTest.qWait(10)
                self.assertFalse(runner.busy)
                self.assertEqual(len(launched), 1)
                runner.start(store, Path(__file__), "references")
                for _ in range(100):
                    if not runner.busy:
                        break
                    QTest.qWait(10)
            self.assertFalse(runner.busy)
            self.assertEqual(len(launched), 3)
            self.assertFalse(errors)
            self.assertEqual(sum(c["status"] == "success" for c in store.cases), 3)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
