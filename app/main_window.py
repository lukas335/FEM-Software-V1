from pathlib import Path
import json
import sys
from math import isfinite
from xml.etree.ElementTree import ParseError

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                              QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                              QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                              QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from app.results_panel import ResultsPanel, card, label
from app.simulation_runner import SimulationRunner, find_febio
from app.study_panel import StudyPanel
from app.drive_control import DriveControlEditor
from app.material_coefficients import MaterialCoefficients
from app.measurement_import import MeasurementImportMixin
from app.mesh_inspection_panel import MeshInspectionPanel
from model.materials import MATERIALS
from model.parameters import CoverShellParameters, ShellParameters, SimulationParameters, MeasurementShellParameters
from visualization.geometry_preview import GeometryPreview
from model.study_storage import atomic_text


class MainWindow(MeasurementImportMixin, QMainWindow):
    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__()
        self.project_root = project_root or Path(__file__).resolve().parents[1]
        self._restoring_settings = False
        self.imported_measurement = None
        self.measurement_import_path = ''
        self.settings_path = self.project_root / "work" / "gui_settings.json"
        self.settings_timer = QTimer(self)
        self.settings_timer.setSingleShot(True)
        self.settings_timer.setInterval(500)
        self.settings_timer.timeout.connect(self._save_settings)
        self.febio_path = find_febio()
        self.runner = SimulationRunner(self.project_root, self)
        self.setWindowTitle("Pressure Sensor FEM")
        self.resize(1680, 1000)
        self.setMinimumSize(1120, 780)
        self.setStyleSheet(Path(__file__).with_name("styles.qss").read_text(encoding="utf-8"))
        workspace = QWidget()
        workspace.setObjectName("workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(12)
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.addWidget(label("Pressure Sensor FEM", "brand"))
        header_layout.addStretch()
        badge = label("Prototyp", "badge")
        badge.setToolTip("Unkalibrierte Materialwerte. Netzkonvergenz und wissenschaftliche Validierung stehen noch aus.")
        header_layout.addWidget(badge)
        layout.addWidget(header)
        self.tabs = QTabWidget()
        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(self._parameter_tab())
        self._restore_settings()
        self.tabs.addTab(parameter_scroll, "Modell")
        self.results = ResultsPanel(self.project_root)
        results_scroll = QScrollArea()
        results_scroll.setWidgetResizable(True)
        results_scroll.setWidget(self.results)
        self.tabs.addTab(results_scroll, "Ergebnisse")
        self.study = StudyPanel(self)
        study_scroll = QScrollArea()
        study_scroll.setWidgetResizable(True)
        study_scroll.setWidget(self.study)
        self.tabs.addTab(study_scroll, "Parameterstudie")
        self.mesh_inspection = MeshInspectionPanel(self)
        self.mesh_tab_index = self.tabs.addTab(self.mesh_inspection, 'Netz & Randbedingungen')
        self.study.runner.busy_changed.connect(self._busy_changed)
        self.study.open_result.connect(self._open_study_result)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(workspace)
        self.runner.busy_changed.connect(self._busy_changed)
        self.runner.progress.connect(self.status.setText)
        self.runner.succeeded.connect(self._simulation_finished)
        self.runner.failed.connect(self._simulation_failed)
        self.runner.mesh_ready.connect(self._mesh_ready)
        self.runner.prepared.connect(self._mesh_prepared)
        self._update_solver_tooltip()
        # Loading saved results is not a solver start. Always begin on Model.
        simulations = self.project_root / "work/simulations"
        saved = []
        for metadata_path in sorted(simulations.glob("run_*/case.json"), reverse=True):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("febio_status") == "success":
                    saved.append(metadata_path.parent)
            except (OSError, ValueError, AttributeError):
                continue
        saved.extend(simulations / name for name in ("baseline_gap1mm", "baseline_febio413"))
        for directory in saved:
            baseline = directory / "force_displacement.csv"
            if baseline.is_file() and self.results.load_case(baseline, show_errors=False):
                break
        self.tabs.setCurrentIndex(0)
        self._load_latest_mesh(show_tab=False)

    @staticmethod
    def _spin(value: float, suffix: str = " mm", maximum: float = 10000.0) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(0, maximum)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setValue(value)
        box.setSuffix(suffix)
        return box

    def _parameter_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 18, 0, 0)
        content = QHBoxLayout()
        content.setSpacing(20)
        self.parameter_form = QWidget()
        grid = QGridLayout(self.parameter_form)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        self.r1, self.tx1, self.ty1 = self._spin(8.0), self._spin(1.25), self._spin(1.25)
        self.r2, self.t2 = self._spin(22.0), self._spin(1.0)
        self.pressure = self._spin(15.0, " mmHg", 1000)
        self.constant_pressure = QCheckBox("Druck konstant halten")
        self.constant_pressure.setToolTip(
            "An: nach dem Druckaufbau bleibt P₀ konstant; Volumenänderung ist erlaubt "
            "(druckgeregeltes Reservoir). Aus: geschlossenes Flüssigkeitsvolumen, Druck reagiert auf Verformung.")
        self.umax, self.initial_gap = self._spin(1.0), self._spin(1.0)
        self.drive_control = DriveControlEditor()
        self.initial_gap.setToolTip("Abstand Messschale–Druckkugel ohne Deckschale. Mit Deckschale muss er tx plus Druckausdehnung übersteigen.")
        self.umax.setToolTip("Gesamter Zustellweg inklusive Leerweg bis zum Erstkontakt.")
        self.wedge = self._spin(3.0, "°", 179)
        self.quality = QComboBox()
        for title, value in (("Grob", "coarse"), ("Normal", "normal"), ("Fein", "fine")):
            self.quality.addItem(title, value)
        self.material1, self.material2 = QComboBox(), QComboBox()
        for box in (self.material1, self.material2):
            for key, material in MATERIALS.items():
                if box is self.material1 and material.fibers is not None:
                    continue
                box.addItem(material.name, key)
            box.setCurrentIndex(box.findData("silicone_A30"))
        self.material2.setCurrentIndex(self.material2.findData("sclera_demo"))
        self.cover_enabled = QCheckBox("Deckschale aktivieren")
        self.cover_tx, self.cover_ty = self._spin(1.0), self._spin(1.0)
        self.cover_front_material, self.cover_rear_material = QComboBox(), QComboBox()
        for box in (self.cover_front_material, self.cover_rear_material):
            for key, material in MATERIALS.items():
                box.addItem(material.name, key)
            box.setCurrentIndex(box.findData("silicone_A30"))
        self.cover_angle = self._spin(70, "°", 89.999)
        self.cover_front_coefficients = MaterialCoefficients(self.cover_front_material)
        self.cover_rear_coefficients = MaterialCoefficients(self.cover_rear_material)
        self.cover_angle.setMinimum(.001)
        self.cover_angle.setToolTip("Vom vorderen Pol gemessen: 70° bedeutet Einspannungsbeginn 20° vor dem Äquator.")
        for widget in (self.cover_tx, self.cover_ty, self.cover_front_material, self.cover_rear_material, self.cover_angle,
                       self.cover_front_coefficients, self.cover_rear_coefficients):
            widget.setEnabled(False)
            self.cover_enabled.toggled.connect(widget.setEnabled)
        self.material2.setToolTip("Sklera: Mooney-Rivlin mit tangentialen, gleichmäßig verteilten Fasern. "
                                  "Illustrative Startwerte, keine kalibrierte menschliche oder tierische Sklera. "
                                  "Silikon bleibt als Vergleich auswählbar.")
        measurement_fields = self.build_measurement_controls()
        groups = [
            ("Messschale", measurement_fields + [("Außenradius R", self.r1), ("Pol-Dicke tx", self.tx1),
                            ("Rand-Dicke ty", self.ty1), ("Material", self.material1)]),
            ("Druckkugel", [("Radius R₂", self.r2), ("Wanddicke t₂", self.t2), ("Material", self.material2)]),
            ("Belastung", [("Anfangsdruck P₀", self.pressure), ("", self.constant_pressure),
                           ("Anfangsspalt", self.initial_gap), ("Steuerung", self.drive_control),
                           ("Max. Zustellweg", self.umax)]),
            ("Rechnung", [("Netz", self.quality), ("Wedge", self.wedge)]),
            ("Deckschale", [("", self.cover_enabled),
                ("Pol-Dicke tx", self.cover_tx), ("Äquator-Dicke ty", self.cover_ty),
                ("Material vorne", self.cover_front_material), ("", self.cover_front_coefficients),
                ("Material hinten", self.cover_rear_material), ("", self.cover_rear_coefficients),
                ("Freier Kappenwinkel", self.cover_angle)]),
        ]
        for index, (title, fields) in enumerate(groups):
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setSpacing(14)
            for text, widget in fields:
                form.addRow(text, widget)
            grid.addWidget(group, index // 2, index % 2)
        content.addWidget(self.parameter_form, 3)
        preview_card, preview_layout = card()
        preview_layout.addWidget(label("Geometrie", "sectionTitle"))
        self.preview = GeometryPreview()
        preview_layout.addWidget(self.preview, 1)
        content.addWidget(preview_card, 2)
        layout.addLayout(content, 1)
        actions = QHBoxLayout()
        self.status = QLabel("Bereit")
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(120)
        self.progress.hide()
        actions.addWidget(self.progress)
        self.solver_button = QPushButton("Solver wählen …")
        self.solver_button.clicked.connect(self._choose_solver)
        actions.addWidget(self.solver_button)
        self.mesh_button = QPushButton('Netz & Randbedingungen prüfen')
        self.mesh_button.setToolTip('Erzeugt das tatsächliche Eingabenetz, startet aber keinen FEM-Solver.')
        self.mesh_button.clicked.connect(self._prepare_mesh)
        actions.addWidget(self.mesh_button)
        self.last_mesh_button = QPushButton('Letztes Netz öffnen')
        self.last_mesh_button.setToolTip('Auch während eines Solverlaufs: vorhandenes Eingabenetz und Bedingungen ansehen.')
        self.last_mesh_button.clicked.connect(lambda: self._load_latest_mesh())
        actions.addWidget(self.last_mesh_button)
        self.start_button = QPushButton("Simulation starten")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._start_simulation)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._update_preview)
        for box in (self.r1, self.tx1, self.ty1, self.r2, self.t2, self.pressure, self.umax,
                    self.initial_gap, self.wedge, self.cover_tx, self.cover_ty, self.cover_angle):
            box.valueChanged.connect(self._parameters_changed)
        for box in (self.material1, self.material2, self.quality, self.cover_front_material, self.cover_rear_material):
            box.currentIndexChanged.connect(self._parameters_changed)
        self.constant_pressure.toggled.connect(self._parameters_changed)
        self.drive_control.changed.connect(self._parameters_changed)
        self.cover_enabled.toggled.connect(self._parameters_changed)
        for editor in (self.cover_front_coefficients, self.cover_rear_coefficients):
            for box in editor.boxes:
                box.valueChanged.connect(self._parameters_changed)
        self._update_preview()
        return root

    def parameters(self) -> SimulationParameters:
        return SimulationParameters(
            MeasurementShellParameters(self.r1.value(), self.tx1.value(), self.ty1.value(), self.material1.currentData()),
            ShellParameters(self.r2.value(), self.t2.value(), self.material2.currentData()),
            pressure_mmhg=self.pressure.value(), max_displacement_mm=self.umax.value(),
            # Legacy snapshot fields; result fitting now determines contact itself.
            fit_start_mm=0.0, fit_end_mm=self.umax.value(),
            wedge_angle_deg=self.wedge.value(), mesh_quality=self.quality.currentData(),
            initial_gap_mm=self.initial_gap.value(),
            constant_pressure=self.constant_pressure.isChecked(),
            measurement_definition=self.active_measurement_definition(),
            cover_shell=CoverShellParameters(enabled=self.cover_enabled.isChecked(),
                thickness_x_mm=self.cover_tx.value(), thickness_y_mm=self.cover_ty.value(),
                front_material_name=self.cover_front_material.currentData(),
                rear_material_name=self.cover_rear_material.currentData(), free_cap_angle_deg=self.cover_angle.value(),
                front_mr_coefficients=self.cover_front_coefficients.values(),
                rear_mr_coefficients=self.cover_rear_coefficients.values()),
            **self.drive_control.values(),
        )

    def _parameters_changed(self):
        if self._restoring_settings:
            return
        self.preview_timer.start()
        self.settings_timer.start()
        self.status.setText("Parameter geändert – bereit für neuen Lauf")

    def _settings_widgets(self):
        numbers = {name: getattr(self, name) for name in (
            "r1", "tx1", "ty1", "r2", "t2", "pressure", "umax", "initial_gap", "wedge",
            "cover_tx", "cover_ty", "cover_angle")}
        numbers.update({f"drive.{name}": box for name, box in self.drive_control.fields.items()})
        for region in ("front", "rear"):
            numbers.update({f"cover.{region}.{i}": box for i, box in enumerate(
                getattr(self, f"cover_{region}_coefficients").boxes)})
        choices = {name: getattr(self, name) for name in (
            "material1", "material2", "quality", "cover_front_material", "cover_rear_material")}
        choices["drive_control"] = self.drive_control.mode
        flags = {name: getattr(self, name) for name in ("constant_pressure", "cover_enabled")}
        return numbers, choices, flags

    def _save_settings(self):
        if self._restoring_settings:
            return
        numbers, choices, flags = self._settings_widgets()
        # Store the draft, including temporarily invalid combinations and disabled fields.
        data = dict(version=1, numbers={k: w.value() for k, w in numbers.items()},
                    choices={k: w.currentData() for k, w in choices.items()},
                    flags={k: w.isChecked() for k, w in flags.items()},
                    febio_path=str(self.febio_path) if self.febio_path else None,
                    measurement_import=self.measurement_import_settings())
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(self.settings_path, json.dumps(data, indent=2, allow_nan=False))
        except (OSError, ValueError) as exc:
            self.status.setText(f"Einstellungen konnten nicht gespeichert werden: {exc}")
            return False
        return True

    def _restore_settings(self):
        if not self.settings_path.is_file():
            return
        numbers, choices, flags = self._settings_widgets()
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if data["version"] != 1:
                raise ValueError("Unbekannte Einstellungs-Version")
            for key, value in data["numbers"].items():
                if key in numbers and (type(value) not in (int, float) or not isfinite(value)
                                       or not numbers[key].minimum() <= value <= numbers[key].maximum()):
                    raise ValueError(f"Ungültiger Wert: {key}")
            for key, value in data["choices"].items():
                if key in choices and choices[key].findData(value) < 0:
                    raise ValueError(f"Auswahl nicht mehr verfügbar: {key}")
            for key, value in data["flags"].items():
                if key in flags and type(value) is not bool:
                    raise ValueError(f"Ungültiger Schalter: {key}")
            solver = data.get("febio_path")
            if solver is not None and not isinstance(solver, str):
                raise ValueError("Ungültiger Solverpfad")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.status.setText(f"Gespeicherte Einstellungen nicht geladen: {exc}")
            return
        self._restoring_settings = True
        try:
            # Material changes reset coefficient editors: restore their numbers afterwards.
            for key, value in data["choices"].items():
                if key in choices:
                    choices[key].setCurrentIndex(choices[key].findData(value))
            for key, value in data["numbers"].items():
                if key in numbers:
                    numbers[key].setValue(int(value) if key == "drive.max_control_trials" else value)
            for key, value in data["flags"].items():
                if key in flags:
                    flags[key].setChecked(value)
            if solver and Path(solver).is_file():
                self.febio_path = Path(solver)
            self.restore_measurement_import(data.get('measurement_import'))
        except (ValueError, TypeError, KeyError) as error:
            self.status.setText(f'Messschalen-Import aus Einstellungen nicht geladen: {error}')
            return
        finally:
            self._restoring_settings = False
        self._update_preview()
        self.status.setText("Letzte Einstellungen geladen")

    def _update_preview(self):
        self.preview.update_parameters(self.parameters())

    def _update_solver_tooltip(self):
        self.solver_button.setToolTip(str(self.febio_path) if self.febio_path else "FEBio 4 auswählen")

    def _choose_solver(self):
        path, _ = QFileDialog.getOpenFileName(self, "FEBio 4 auswählen", str(self.febio_path or ""),
                                             "FEBio (*.exe);;Alle Dateien (*)")
        if path:
            self.febio_path = Path(path)
            self._update_solver_tooltip()
            self.settings_timer.start()

    def _start_simulation(self):
        if self.runner.busy or self.study.runner.busy:
            return
        params = self.parameters()
        if errors := params.validate():
            self.status.setText(" ".join(errors))
            return
        if self.febio_path is None or not self.febio_path.is_file():
            self._choose_solver()
        if self.febio_path is None or not self.febio_path.is_file():
            self.status.setText("Bitte FEBio auswählen.")
            return
        self.results.animation.pause()
        try:
            self.runner.start(params, self.febio_path)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))

    def _prepare_mesh(self):
        if self.runner.busy or self.study.runner.busy:
            return
        try:
            self.runner.start(self.parameters(), None, generate_only=True)
            self.tabs.setCurrentIndex(self.mesh_tab_index)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))

    def _mesh_ready(self, path):
        try:
            self.mesh_inspection.load_mesh(path)
            self.tabs.setCurrentIndex(self.mesh_tab_index)
        except (OSError, ValueError, KeyError, TypeError, ParseError) as error:
            self.status.setText(f'Netzanzeige nicht verfügbar: {error}')

    def _mesh_prepared(self, path):
        self.status.setText('Netzprüfung abgeschlossen – kein Solver gestartet. Hinweise im Netz-Tab beachten.')

    def _load_latest_mesh(self, *, show_tab=True):
        current = self.runner.directory / 'model.feb' if self.runner.directory else None
        candidates = list((self.project_root / 'work/simulations').glob('run_*/model.feb'))
        if not candidates:
            candidates = list((self.project_root / 'work/mesh_previews').glob('run_*/model.feb'))
        path = current if current and current.is_file() else max(candidates, key=lambda item: item.stat().st_mtime, default=None)
        if path is None:
            if show_tab:
                self.status.setText('Noch kein Netz vorhanden. Zuerst Netz & Randbedingungen prüfen.')
            return
        try:
            self.mesh_inspection.load_mesh(path)
            if show_tab:
                self.tabs.setCurrentIndex(self.mesh_tab_index)
        except (OSError, ValueError, KeyError, TypeError, ParseError) as error:
            if show_tab:
                self.status.setText(f'Netz nicht lesbar: {error}')

    def _busy_changed(self, busy: bool):
        busy = busy or self.runner.busy or self.study.runner.busy
        self.start_button.setEnabled(not busy)
        self.start_button.setText("Berechnung läuft …" if busy else "Simulation starten")
        self.solver_button.setEnabled(not busy)
        self.mesh_button.setEnabled(not busy)
        self.parameter_form.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.tabs.setTabText(1, "Ergebnisse · vorheriger Lauf" if busy and self.results.curve else "Ergebnisse")
        self.study.update_controls()

    def _open_study_result(self, path):
        if self.results.load_case(path):
            self.tabs.setCurrentIndex(1)

    def _simulation_finished(self, curve_path: Path):
        if self.results.load_case(curve_path, show_errors=False):
            metadata = json.loads(curve_path.with_name("case.json").read_text(encoding="utf-8"))
            self.status.setText("Berechnung abgeschlossen" if metadata.get("febio_status") == "success"
                               else "Teilresultat – Zielkraft nicht erreicht; siehe Ergebnisstatus")
            if metadata.get('cleanup_warnings'):
                self.status.setText(self.status.text() + ' · Aufräumwarnung; Ergebnisse sind verfügbar')
            self.tabs.setCurrentIndex(1)
        else:
            self._simulation_failed("Die berechneten Ergebnisse konnten nicht geladen werden.")

    def _simulation_failed(self, details: str):
        title = 'Netzprüfung fehlgeschlagen' if self.runner.generate_only else 'Simulation fehlgeschlagen'
        self.status.setText(title + ' – bisherige Ergebnisse bleiben erhalten')
        message = QMessageBox(QMessageBox.Icon.Warning, title,
                              "Der Lauf konnte nicht abgeschlossen werden.", parent=self)
        message.setDetailedText(details)
        message.exec()

    def closeEvent(self, event):
        if self.runner.busy or self.study.runner.busy:
            QMessageBox.information(self, "Simulation läuft", "Bitte den laufenden Solverlauf abwarten, bevor Sie das Fenster schließen.")
            event.ignore()
            return
        self.preview_timer.stop()
        self.settings_timer.stop()
        if self._save_settings() is False:
            QMessageBox.warning(self, "Einstellungen", self.status.text())
        self.results.animation.pause()
        super().closeEvent(event)


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    return app.exec()
