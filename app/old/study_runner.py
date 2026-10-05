"""Sequential study orchestration; the batch remains busy between solver calls."""

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
        self.solver = SimulationRunner(project_root, self)
        self.solver.succeeded.connect(self._success)
        self.solver.failed.connect(self._failure)
        self.solver.progress.connect(lambda s: self.progress.emit(self.prefix + s))
        self.busy = False
        self.stop_requested = False
        self.prefix = ""
        self.storage = None
        self.current = None
        self.queue = []
        self.lock = None

    def start(self, storage, febio, phase, retry_failed=False):
        if self.busy:
            raise ValueError("Studie läuft bereits.")
        if phase not in ("references", "grid"):
            raise ValueError("Unbekannte Studienphase.")
        if not febio.is_file():
            raise ValueError("FEBio auswählen, bevor die Studie gestartet wird.")
        storage.assert_resumable()
        selected = [c for c in storage.cases if (c["references"] if phase == "references" else c["in_grid"])]
        self.queue = [c for c in selected if c["status"] == "pending" or (retry_failed and c["status"] == "failed")]
        if not self.queue:
            raise ValueError("Keine ausstehenden Fälle in dieser Phase. Fehlgeschlagene Fälle ggf. zum Wiederholen auswählen.")
        lock = QLockFile(str(storage.directory / "study.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError("Diese Studie wird bereits von einem anderen Programmfenster verwendet.")
        self.lock = lock
        self.storage, self.febio = storage, febio
        storage.phase = phase
        try:
            storage.save()
        except (OSError, ValueError):
            self.lock.unlock()
            self.lock = None
            raise
        self.total, self.done = len(self.queue), 0
        self.stop_requested = False
        self.busy = True
        self.busy_changed.emit(True)
        QTimer.singleShot(0, self._next)

    def stop_after_current(self):
        self.stop_requested = True
        self.progress.emit("Stop angefordert – aktueller Lauf wird abgeschlossen.")

    def _finish(self):
        self.busy = False
        self.current = None
        if self.lock is not None:
            self.lock.unlock()
            self.lock = None
        self.busy_changed.emit(False)
        self.changed.emit()

    def _next(self):
        if self.stop_requested or not self.queue:
            self.progress.emit(("Studie angehalten" if self.stop_requested else "Phase abgeschlossen") +
                               (" · " + " ".join(self.storage.warnings) if self.storage.warnings else ""))
            self._finish()
            return
        self.current = self.queue.pop(0)
        case = self.current
        p = SimulationParameters.from_dict(case["parameters"])
        self.prefix = (f"Lauf {self.done + 1}/{self.total} · R {p.measurement_shell.radius_mm:g} · "
                       f"tx {p.measurement_shell.thickness_x_mm:g} / ty {p.measurement_shell.thickness_y_mm:g} · {p.pressure_mmhg:g} mmHg – ")
        try:
            directory = self.storage.new_attempt(case)
            case.update(status="running", result=None, error="", auto_fit_eligible=False)
            self.storage.save()
        except (OSError, ValueError) as exc:
            case.update(status="pending", error="Vor Solverstart am Speichern gescheitert.")
            self._finish()
            self.error.emit(f"Studienstand konnte nicht gespeichert werden; kein Solver gestartet: {exc}")
            return
        self.changed.emit()
        try:
            self.solver.start(p, self.febio, directory=directory)
        except (OSError, ValueError, RuntimeError) as exc:
            self._failure(str(exc))

    def _success(self, curve_path):
        try:
            result = analyze_case(curve_path.parent, self.storage.config)
            self.current.update(status="success", result=result, error="",
                                auto_fit_eligible=self.storage.config.fit_mode == "automatic",
                                fingerprints=self.storage.fingerprints(curve_path.parent))
        except (OSError, ValueError, KeyError) as exc:
            self.current.update(status="invalid", result=None, error=str(exc))
        self._checkpoint()

    def _failure(self, details):
        self.current.update(status="failed", result=None, error=details)
        self._checkpoint()

    def _checkpoint(self):
        try:
            self.storage.save()
        except (OSError, ValueError) as exc:
            self._finish()
            self.error.emit(f"Speichern fehlgeschlagen; Studie angehalten. Vorhandene Solverdateien bleiben erhalten: {exc}")
            return
        self.done += 1
        self.changed.emit()
        QTimer.singleShot(0, self._next)
