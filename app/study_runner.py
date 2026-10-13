"""Parallel study orchestration with isolated SimulationRunner workers."""

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal

from app.simulation_runner import SimulationRunner
from model.parameters import SimulationParameters
from model.study_results import analyze_case


class StudyRunner(QObject):
    busy_changed = Signal(bool)
    changed = Signal()
    progress = Signal(str)
    error = Signal(str)

    def __init__(self, project_root, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.busy = False
        self.stop_requested = False
        self.storage = None
        self.queue = []
        self.lock = None
        self.febio = None
        self.total = 0
        self.done = 0
        self.max_parallel_jobs = 1

        # runner -> {"case": dict, "directory": Path, "prefix": str}
        self.active = {}
        self.workers = []

    def start(
        self,
        storage,
        febio,
        phase,
        retry_failed=False,
        max_parallel_jobs=1,
    ):
        if self.busy:
            raise ValueError("Studie läuft bereits.")
        if phase not in ("references", "grid"):
            raise ValueError("Unbekannte Studienphase.")
        if not febio.is_file():
            raise ValueError("FEBio auswählen, bevor die Studie gestartet wird.")

        try:
            max_parallel_jobs = int(max_parallel_jobs)
        except (TypeError, ValueError) as exc:
            raise ValueError("Parallele Läufe müssen eine ganze Zahl sein.") from exc
        if not 1 <= max_parallel_jobs <= 32:
            raise ValueError("Parallele Läufe müssen zwischen 1 und 32 liegen.")

        storage.assert_resumable()

        selected = [
            case
            for case in storage.cases
            if (
                case["references"]
                if phase == "references"
                else case["in_grid"]
            )
        ]
        self.queue = [
            case
            for case in selected
            if case["status"] == "pending"
            or (retry_failed and case["status"] == "failed")
        ]

        if not self.queue:
            raise ValueError(
                "Keine ausstehenden Fälle in dieser Phase. "
                "Fehlgeschlagene Fälle ggf. zum Wiederholen auswählen."
            )

        lock = QLockFile(str(storage.directory / "study.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError(
                "Diese Studie wird bereits von einem anderen Programmfenster verwendet."
            )

        self.lock = lock
        self.storage = storage
        self.febio = febio
        self.max_parallel_jobs = min(max_parallel_jobs, len(self.queue))
        storage.phase = phase

        try:
            storage.save()
        except (OSError, ValueError):
            self.lock.unlock()
            self.lock = None
            raise

        self.total = len(self.queue)
        self.done = 0
        self.stop_requested = False
        self.active.clear()
        self._ensure_workers(self.max_parallel_jobs)

        self.busy = True
        self.busy_changed.emit(True)

        self.progress.emit(
            f"Studie gestartet · {self.total} Fälle · "
            f"bis zu {self.max_parallel_jobs} parallel"
        )
        QTimer.singleShot(0, self._fill_slots)

    def _ensure_workers(self, count):
        while len(self.workers) < count:
            runner = SimulationRunner(self.project_root, self)
            runner.succeeded.connect(
                lambda curve_path, worker=runner: self._success(worker, curve_path)
            )
            runner.failed.connect(
                lambda details, worker=runner: self._failure(worker, details)
            )
            runner.progress.connect(
                lambda text, worker=runner: self._worker_progress(worker, text)
            )
            self.workers.append(runner)

    def stop_after_current(self):
        self.stop_requested = True
        self.progress.emit(
            "Stop angefordert – laufende Fälle werden abgeschlossen; "
            "es werden keine neuen gestartet."
        )
        if not self.active:
            self._finish()

    def _finish(self):
        if not self.busy:
            return

        self.busy = False
        self.active.clear()

        if self.lock is not None:
            self.lock.unlock()
            self.lock = None

        self.busy_changed.emit(False)
        self.changed.emit()

    def _case_prefix(self, case):
        p = SimulationParameters.from_dict(case["parameters"])
        return (
            f"R {p.measurement_shell.radius_mm:g} · "
            f"tx {p.measurement_shell.thickness_x_mm:g} / "
            f"ty {p.measurement_shell.thickness_y_mm:g} · "
            f"{p.pressure_mmhg:g} mmHg"
        )

    def _fill_slots(self):
        if not self.busy:
            return

        if self.stop_requested:
            if not self.active:
                self.progress.emit(
                    "Studie angehalten"
                    + (
                        " · " + " ".join(self.storage.warnings)
                        if self.storage.warnings
                        else ""
                    )
                )
                self._finish()
            return

        free_workers = [
            worker
            for worker in self.workers[: self.max_parallel_jobs]
            if worker not in self.active
        ]

        while self.queue and free_workers and not self.stop_requested:
            worker = free_workers.pop(0)
            case = self.queue.pop(0)
            self._start_case(worker, case)

        if not self.queue and not self.active:
            self.progress.emit(
                "Phase abgeschlossen"
                + (
                    " · " + " ".join(self.storage.warnings)
                    if self.storage.warnings
                    else ""
                )
            )
            self._finish()
            return

        self._emit_overall_progress()

    def _start_case(self, worker, case):
        p = SimulationParameters.from_dict(case["parameters"])
        prefix = self._case_prefix(case)

        try:
            directory = self.storage.new_attempt(case)
            case.update(
                status="running",
                result=None,
                error="",
                auto_fit_eligible=False,
            )
            self.storage.save()
        except (OSError, ValueError) as exc:
            case.update(
                status="pending",
                error="Vor Solverstart am Speichern gescheitert.",
            )
            self.stop_requested = True
            self.error.emit(
                "Studienstand konnte nicht gespeichert werden; "
                f"kein Solver für {prefix} gestartet: {exc}"
            )
            QTimer.singleShot(0, self._fill_slots)
            return

        self.active[worker] = {
            "case": case,
            "directory": directory,
            "prefix": prefix,
        }
        self.changed.emit()

        try:
            worker.start(p, self.febio, directory=directory,
                         compact=False, final_only_visualization=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self._failure(worker, str(exc))

    def _worker_progress(self, worker, text):
        job = self.active.get(worker)
        if job is None:
            return
        self.progress.emit(
            f"{self.done}/{self.total} fertig · "
            f"{len(self.active)} aktiv · {job['prefix']} – {text}"
        )

    def _success(self, worker, curve_path):
        job = self.active.get(worker)
        if job is None:
            return

        case = job["case"]
        try:
            result = analyze_case(
                curve_path.parent,
                self.storage.config,
            )
            case.update(
                status="success",
                result=result,
                error="",
                auto_fit_eligible=self.storage.config.fit_mode == "automatic",
                fingerprints=self.storage.fingerprints(curve_path.parent),
            )
        except (OSError, ValueError, KeyError) as exc:
            case.update(
                status="invalid",
                result=None,
                error=str(exc),
            )

        self._complete_worker(worker)

    def _failure(self, worker, details):
        job = self.active.get(worker)
        if job is None:
            return

        job["case"].update(
            status="failed",
            result=None,
            error=details,
        )
        self._complete_worker(worker)

    def _complete_worker(self, worker):
        if worker not in self.active:
            return

        self.active.pop(worker)
        self.done += 1

        try:
            self.storage.save()
        except (OSError, ValueError) as exc:
            self.stop_requested = True
            self.error.emit(
                "Speichern fehlgeschlagen; es werden keine neuen Fälle gestartet. "
                "Bereits laufende Solver werden noch abgeschlossen. "
                f"Vorhandene Solverdateien bleiben erhalten: {exc}"
            )

        self.changed.emit()
        self._emit_overall_progress()
        QTimer.singleShot(0, self._fill_slots)

    def _emit_overall_progress(self):
        if not self.busy:
            return
        queued = len(self.queue)
        active = len(self.active)
        self.progress.emit(
            f"{self.done}/{self.total} fertig · "
            f"{active} aktiv · {queued} ausstehend"
        )
