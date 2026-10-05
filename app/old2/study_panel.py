"""Editable study setup, explicit phase starts, and snapshot-based results."""

from dataclasses import replace
from pathlib import Path
import numpy as np
from PySide6.QtCore import QLockFile, QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from app.study_runner import StudyRunner
from app.drive_control import DriveControlEditor
from model.materials import MATERIALS
from model.results import read_force_curve
from model.study import ReferenceGeometry, StudyConfig
from model.study_results import rank_geometries
from model.study_storage import StudyStorage


class StudyCanvas(FigureCanvasQTAgg):
    """Resizable plot with a preferred size independent of its current geometry.

    Matplotlib's default sizeHint tracks the resized figure. Nested inside a
    QTabWidget/QSplitter in a resizable QScrollArea this feeds the allocated
    size back into the next layout request, growing the page indefinitely.
    Only the hint is fixed: the canvas itself still expands and shrinks.
    """

    def __init__(self, figure):
        self._preferred_size = QSize(round(figure.bbox.width), round(figure.bbox.height))
        super().__init__(figure)

    def sizeHint(self):
        return QSize(self._preferred_size)


class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        return self.data(Qt.ItemDataRole.UserRole) < other.data(Qt.ItemDataRole.UserRole)


def numbers(text):
    # Semicolons/spaces separate values; commas are accepted as decimal marks.
    values = tuple(float(v.replace(",", ".")) for v in text.replace(";", " ").split())
    if not values:
        raise ValueError("Werteliste leer. Werte mit Semikolon oder Leerzeichen trennen.")
    return values


class StudyPanel(QWidget):
    open_result = Signal(object)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.storage = None
        self.runner = StudyRunner(window.project_root, self)
        self.runner.changed.connect(self.refresh_results)
        self.runner.progress.connect(self._progress)
        self.runner.error.connect(self._error)
        self.runner.busy_changed.connect(self.update_controls)
        self.rows = []
        self.setMinimumHeight(730)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        self.title = QLabel("Parameterstudie · unkalibriertes Modell")
        self.title.setObjectName("sectionTitle")
        heading.addWidget(self.title)
        heading.addStretch()
        self.new_button = QPushButton("Neue Studie / Kopie")
        self.new_button.clicked.connect(self.new_study)
        self.open_button = QPushButton("Studie öffnen …")
        self.open_button.clicked.connect(self.open_study)
        self.auto_reanalyze_button = QPushButton("Automatisch neu auswerten")
        self.auto_reanalyze_button.setToolTip("Neue Auswertungskopie aus vorhandenen Solverdateien erstellen. Kein neuer Solverlauf.")
        self.auto_reanalyze_button.clicked.connect(self.automatic_reanalysis)
        heading.addWidget(self.new_button)
        heading.addWidget(self.auto_reanalyze_button)
        heading.addWidget(self.open_button)
        layout.addLayout(heading)
        self.tabs = QTabWidget()
        self.editor = self._editor()
        self.tabs.addTab(self.editor, "Konfiguration")
        self.tabs.addTab(self._results(), "Auswertung")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("Bereit – Modell übernehmen oder Einstellungen anpassen")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.references_button = QPushButton("Referenzen prüfen")
        self.grid_button = QPushButton("Studie starten")
        self.grid_button.setObjectName("primary")
        self.resume_button = QPushButton("Fortsetzen")
        self.stop_button = QPushButton("Nach aktuellem Lauf stoppen")
        self.retry = QCheckBox("Fehlgeschlagene wiederholen")
        self.retry.setToolTip("Nur Solverfehler wiederholen. Ungültige Fits benötigen ggf. eine neue Konfiguration.")
        self.references_button.clicked.connect(lambda: self.start_phase("references"))
        self.grid_button.clicked.connect(lambda: self.start_phase("grid"))
        self.resume_button.clicked.connect(lambda: self.start_phase(self.storage.phase) if self.storage else None)
        self.stop_button.clicked.connect(self.runner.stop_after_current)
        for button in (self.references_button, self.grid_button, self.resume_button, self.stop_button):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.retry)
        layout.addLayout(actions)
        self.take_model()
        self.refresh_results()
        self.update_controls()

    def _editor(self):
        root = QWidget()
        grid = QGridLayout(root)
        self.take_button = QPushButton("Modellparameter übernehmen")
        self.take_button.clicked.connect(self.take_model)
        grid.addWidget(self.take_button, 0, 0)
        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        grid.addWidget(self.count_label, 0, 1)
        spin = self.window._spin
        self.r2, self.t2 = spin(22), spin(1)
        self.gap, self.drive, self.wedge = spin(1), spin(1), spin(3, "°", 179)
        self.drive_control = DriveControlEditor()
        self.fit_a, self.fit_b = spin(.2), spin(1)
        self.fit_mode = QComboBox()
        self.fit_mode.addItem("Automatisch ab Kontakt", "automatic")
        self.fit_mode.addItem("Manueller Bereich nach Kontakt", "manual")
        self.fit_mode.setToolTip("Automatisch: Kontakt erkennen und den gemeinsam erreichten belasteten Wegbereich aller auswertbaren Fälle verwenden. Während der Studie ist der Bereich vorläufig.")
        self.fit_labels = []
        self.material1, self.material2 = QComboBox(), QComboBox()
        for key, material in MATERIALS.items():
            self.material2.addItem(material.name, key)
            if material.fibers is None:
                self.material1.addItem(material.name, key)
        self.mode = QComboBox()
        for title, value in (("Geschlossenes Volumen", "closed"), ("Konstanter Druck", "constant"),
                             ("Ohne Innendruck", "none")):
            self.mode.addItem(title, value)
        self.mode.setToolTip("Ohne Innendruck: weder Drucklast noch Volumenbindung. Geschlossen mit P₀=0 kann trotzdem Druck aufbauen.")
        self.quality = QComboBox()
        for title, value in (("Grob", "coarse"), ("Normal", "normal"), ("Fein", "fine")):
            self.quality.addItem(title, value)
        self.parallel_jobs = QSpinBox()
        self.parallel_jobs.setRange(1, 32)
        self.parallel_jobs.setValue(4)
        self.parallel_jobs.setToolTip(
            "Anzahl gleichzeitig laufender FEBio-Fälle. "
            "Bei hoher CPU-/RAM-Last einen kleineren Wert wählen."
        )
        self.radii = QLineEdit("6; 8; 10")
        self.thicknesses = QLineEdit("0,75; 1,25; 1,75")
        self.pressures = QLineEdit("15; 25; 35")
        self.cover_summary = QLabel("Deckschale aus Modell-Tab")
        self.cover_summary.setWordWrap(True)
        for box in (self.radii, self.thicknesses, self.pressures):
            box.setToolTip("Werte mit Semikolon oder Leerzeichen trennen; Dezimalpunkt oder Dezimalkomma möglich.")
        groups = (
            ("Gemeinsames Modell", [("Kugelradius R₂", self.r2), ("Kugelwand t₂", self.t2),
                ("Material Messschale", self.material1), ("Material Kugel", self.material2),
                ("Druckmodus", self.mode), ("Anfangsspalt", self.gap),
                ("Deckschale", self.cover_summary),
                ("Steuerung", self.drive_control), ("Max. Zustellweg", self.drive)]),
            ("Variationen && Auswertung", [("Radien R₁ [mm]", self.radii), ("Wanddicken t₁ [mm]", self.thicknesses),
                ("Drücke [mmHg]", self.pressures), ("Regression", self.fit_mode),
                ("Fitbeginn nach Kontakt", self.fit_a), ("Fitende nach Kontakt", self.fit_b),
                ("Netz", self.quality), ("Wedge", self.wedge),
                ("Parallele Läufe", self.parallel_jobs)]))
        for column, (title, fields) in enumerate(groups):
            group = QGroupBox(title)
            form = QFormLayout(group)
            for caption, widget in fields:
                form.addRow(caption, widget)
                if widget in (self.fit_a, self.fit_b):
                    self.fit_labels.append(form.labelForField(widget))
            grid.addWidget(group, 1, column)
        refs = QGroupBox("Referenzgeometrien der Messschale")
        content = QVBoxLayout(refs)
        self.reference_table = QTableWidget(0, 3)
        self.reference_table.setHorizontalHeaderLabels(["Name", "Radius [mm]", "Wanddicke [mm]"])
        self.reference_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.reference_table.setMaximumHeight(155)
        content.addWidget(self.reference_table)
        buttons = QHBoxLayout()
        for text, callback in (("Referenz hinzufügen", lambda: self.add_reference()),
                               ("Aktuelle Messschale übernehmen", self.add_current_reference),
                               ("Auswahl entfernen", self.remove_reference)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        content.addLayout(buttons)
        grid.addWidget(refs, 2, 0, 1, 2)
        grid.setRowStretch(3, 1)
        for box in (self.r2, self.t2, self.gap, self.drive, self.wedge, self.fit_a, self.fit_b, self.parallel_jobs):
            box.valueChanged.connect(self.update_count)
        for box in (self.material1, self.material2, self.mode, self.quality, self.fit_mode):
            box.currentIndexChanged.connect(self.update_count)
        for box in (self.radii, self.thicknesses, self.pressures):
            box.textChanged.connect(self.update_count)
        self.reference_table.itemChanged.connect(self.update_count)
        self.drive_control.changed.connect(self.update_count)
        return root

    def _results(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        self.summary = QLabel("Noch keine Studie geladen")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        side = QVBoxLayout(left)
        self.ranking = QTableWidget(0, 6)
        self.ranking.setHorizontalHeaderLabels(["R", "t", "S", "Δk/k₀", "Referenz", "Status"])
        self.ranking.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ranking.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ranking.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ranking.setSortingEnabled(True)
        self.ranking.itemSelectionChanged.connect(self.draw_detail)
        side.addWidget(self.ranking, 1)
        self.case_table = QTableWidget(0, 5)
        self.case_table.setHorizontalHeaderLabels(["Druck", "k [N/mm]", "R²", "Status", "Hinweis"])
        self.case_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.case_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.case_table.setMaximumHeight(180)
        self.case_table.cellDoubleClicked.connect(self.open_case)
        self.case_table.setToolTip("Doppelklick: gespeicherten Lauf in der Einzelansicht öffnen. Fehlerdetails im Tooltip.")
        side.addWidget(self.case_table)
        split.addWidget(left)
        views = QTabWidget()
        self.map_figure = Figure(figsize=(6, 5), layout="constrained", facecolor="white")
        self.map_canvas = StudyCanvas(self.map_figure)
        self.map_canvas.mpl_connect("button_press_event", self.select_map)
        views.addTab(self.map_canvas, "Empfindlichkeit R / t")
        self.detail_figure = Figure(figsize=(7, 5), layout="constrained", facecolor="white")
        self.detail_canvas = StudyCanvas(self.detail_figure)
        views.addTab(self.detail_canvas, "Kurven der Auswahl")
        split.addWidget(views)
        split.setSizes([480, 850])
        layout.addWidget(split, 1)
        return root

    def take_model(self):
        params = self.window.parameters()
        try:
            params.measurement_shell.concentric_thickness()
        except ValueError as exc:
            self._error(str(exc))
            return
        self.base = params
        self._update_cover_summary()
        p = self.base
        self.drive_control.restore(p)
        for box, value in ((self.r2, p.pressure_shell.radius_mm), (self.t2, p.pressure_shell.thickness_mm),
                           (self.gap, p.initial_gap_mm), (self.drive, p.max_displacement_mm), (self.wedge, p.wedge_angle_deg)):
            box.setValue(value)
        for box, value in ((self.material1, p.measurement_shell.material_name), (self.material2, p.pressure_shell.material_name),
                           (self.mode, "constant" if p.constant_pressure else "closed"), (self.quality, p.mesh_quality)):
            box.setCurrentIndex(box.findData(value))
        self.update_count()

    def add_reference(self, reference=None):
        row = self.reference_table.rowCount()
        p = self.window.parameters().measurement_shell
        try:
            r = reference or ReferenceGeometry(f"Referenz {row + 1}", p.radius_mm, p.concentric_thickness())
        except ValueError as exc:
            self._error(str(exc))
            return
        self.reference_table.insertRow(row)
        for column, value in enumerate((r.name, r.radius_mm, r.thickness_mm)):
            self.reference_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.update_count()

    def add_current_reference(self):
        self.add_reference()

    def remove_reference(self):
        for row in sorted({item.row() for item in self.reference_table.selectedItems()}, reverse=True):
            self.reference_table.removeRow(row)
        self.update_count()

    def config(self):
        refs = []
        for row in range(self.reference_table.rowCount()):
            values = [self.reference_table.item(row, col) for col in range(3)]
            if any(v is None for v in values):
                raise ValueError("Referenzzeile vervollständigen.")
            refs.append(ReferenceGeometry(values[0].text(), float(values[1].text().replace(",", ".")),
                                           float(values[2].text().replace(",", "."))))
        base = replace(self.base,
            measurement_shell=replace(self.base.measurement_shell, material_name=self.material1.currentData()),
            pressure_shell=replace(self.base.pressure_shell, radius_mm=self.r2.value(), thickness_mm=self.t2.value(),
                                   material_name=self.material2.currentData()),
            pressure_mmhg=0, max_displacement_mm=self.drive.value(), fit_start_mm=0, fit_end_mm=self.drive.value(),
            initial_gap_mm=self.gap.value(), wedge_angle_deg=self.wedge.value(), mesh_quality=self.quality.currentData(),
            constant_pressure=self.mode.currentData() != "closed", **self.drive_control.values())
        config = StudyConfig(base, numbers(self.radii.text()), numbers(self.thicknesses.text()),
                            (0.,) if self.mode.currentData() == "none" else numbers(self.pressures.text()), tuple(refs),
                            self.fit_a.value(), self.fit_b.value(), pressure_mode=self.mode.currentData(),
                            fit_mode=self.fit_mode.currentData())
        config.cases()
        return config

    def update_count(self, *_):
        if not hasattr(self, "base"):
            return
        self.pressures.setEnabled(self.mode.currentData() != "none")
        manual = self.fit_mode.currentData() == "manual"
        for widget in (self.fit_a, self.fit_b, *self.fit_labels):
            widget.setVisible(manual)
        try:
            cases = self.config().cases()
            grid = sum(c["in_grid"] for c in cases)
            refs = sum(bool(c["references"]) for c in cases)
            text = f"{len(cases)} eindeutige Fälle · Raster {grid} · Referenzfälle {refs}"
            if manual and self.drive.value() <= self.fit_b.value():
                text += " · Hubreserve für Leerweg fehlt"
            self.count_label.setText(text)
        except (ValueError, TypeError) as exc:
            self.count_label.setText(str(exc))

    def restore_config(self, config):
        self.base = config.base_parameters
        self._update_cover_summary()
        p = self.base
        self.drive_control.restore(p)
        for box, value in ((self.r2, p.pressure_shell.radius_mm), (self.t2, p.pressure_shell.thickness_mm),
                           (self.gap, p.initial_gap_mm), (self.drive, p.max_displacement_mm), (self.wedge, p.wedge_angle_deg),
                           (self.fit_a, config.fit_start_after_contact_mm), (self.fit_b, config.fit_end_after_contact_mm)):
            box.setValue(value)
        for box, value in ((self.material1, p.measurement_shell.material_name), (self.material2, p.pressure_shell.material_name),
                           (self.mode, config.pressure_mode), (self.quality, p.mesh_quality), (self.fit_mode, config.fit_mode)):
            box.setCurrentIndex(box.findData(value))
        for box, values in ((self.radii, config.measurement_radii_mm), (self.thicknesses, config.measurement_thicknesses_mm),
                            (self.pressures, config.pressures_mmhg)):
            box.setText("; ".join(map(str, values)))
        self.reference_table.setRowCount(0)
        for reference in config.reference_geometries:
            self.add_reference(reference)
        self.update_count()

    def _update_cover_summary(self):
        cover = self.base.cover_shell
        self.cover_summary.setText(
            f"tx={cover.thickness_x_mm:g}, ty={cover.thickness_y_mm:g} mm · {cover.free_cap_angle_deg:g}° frei"
            if cover.enabled else "Deaktiviert")
        details = "Im Modell-Tab bearbeiten, dann Modell übernehmen."
        if cover.enabled:
            from model.materials import domain_materials
            for name, material in domain_materials(self.base).items():
                if name.startswith("cover_"):
                    details += f"\n{name}: {material.name}"
                    if material.model == "mooney_rivlin":
                        details += f"; c1={material.c1_mpa:g}, c2={material.c2_mpa:g}, K={material.bulk_modulus_mpa:g} MPa"
        self.cover_summary.setToolTip(details)

    def new_study(self):
        if self.runner.busy:
            return
        self.storage = None
        self.fit_mode.setCurrentIndex(self.fit_mode.findData("automatic"))
        self.status.setText("Neue Konfiguration – gespeicherte Studie bleibt unverändert")
        self.tabs.setCurrentIndex(0)
        self.refresh_results()
        self.update_controls()

    def open_study(self):
        path, _ = QFileDialog.getOpenFileName(self, "Studie öffnen", str(self.window.project_root / "work/studies"), "Studie (study.json)")
        if path:
            try:
                storage = StudyStorage.open(Path(path).parent)
                self.storage = storage
                self.restore_config(storage.config)
                self.refresh_results()
                self.update_controls()
                self.status.setText(f"Geladen: {storage.directory.name} · Konfiguration eingefroren")
                self.tabs.setCurrentIndex(1)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self._error(str(exc))

    def start_phase(self, phase):
        if self.runner.busy or self.window.runner.busy:
            return
        try:
            config = self.storage.config if self.storage else self.config()
            if config.fit_mode == "manual" and config.fit_end_after_contact_mm >= config.base_parameters.max_displacement_mm:
                raise ValueError("Fitende nach Kontakt muss kleiner als der maximale Gesamtzustellweg sein. "
                                 "Für den Leerweg zusätzliche Hubreserve vorsehen. Bei gespeicherter Studie: Neue Studie / Kopie.")
            if phase == "references" and not config.reference_geometries:
                raise ValueError("Bitte zuerst mindestens eine Referenzgeometrie hinzufügen.")
            if self.window.febio_path is None or not self.window.febio_path.is_file():
                self.window._choose_solver()
            if self.window.febio_path is None or not self.window.febio_path.is_file():
                raise ValueError("Bitte FEBio auswählen.")
            if self.storage is None:
                self.storage = StudyStorage.create(self.window.project_root / "work/studies", config)
            self.runner.start(
                self.storage,
                self.window.febio_path,
                phase,
                self.retry.isChecked(),
                max_parallel_jobs=self.parallel_jobs.value(),
            )
            self.tabs.setCurrentIndex(1)
        except (OSError, ValueError, RuntimeError) as exc:
            self._error(str(exc))
        self.update_controls()

    def automatic_reanalysis(self):
        if self.storage is None or self.runner.busy or self.window.runner.busy:
            return
        lock = QLockFile(str(self.storage.directory / "study.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            self._error("Diese Studie läuft in einem anderen Fenster. Zuerst den Lauf abschließen.")
            return
        try:
            self.storage = self.storage.automatic_copy()
            self.restore_config(self.storage.config)
            self.refresh_results()
            self.tabs.setCurrentIndex(1)
            self.status.setText("Automatische Auswertungskopie erstellt – keine neue Solverrechnung")
        except (OSError, ValueError, KeyError) as exc:
            self._error(str(exc))
        finally:
            lock.unlock()
            self.update_controls()

    def update_controls(self, *_):
        busy = self.runner.busy or self.window.runner.busy
        self.editor.setEnabled(not busy and self.storage is None)
        for button in (self.references_button, self.grid_button, self.new_button, self.open_button, self.retry):
            button.setEnabled(not busy)
        self.resume_button.setEnabled(not busy and self.storage is not None)
        self.auto_reanalyze_button.setEnabled(not busy and self.storage is not None)
        self.stop_button.setEnabled(self.runner.busy and not self.runner.stop_requested)

    def _progress(self, text):
        self.status.setText(text)
        self.update_controls()

    def _error(self, text):
        self.status.setText(text)
        QMessageBox.warning(self, "Parameterstudie", text)

    def refresh_results(self):
        selected = self.selected_geometry()
        self.rows = rank_geometries(self.storage.cases, self.storage.config) if self.storage else []
        self.ranking.blockSignals(True)
        self.ranking.setSortingEnabled(False)
        self.ranking.setRowCount(len(self.rows))
        selected_row = 0
        for index, row in enumerate(self.rows):
            key = (row["radius_mm"], row["thickness_mm"])
            if key == selected:
                selected_row = index
            for col, value in enumerate((row["radius_mm"], row["thickness_mm"], row["sensitivity"], row["relative_change"],
                                          ", ".join(row["references"]), row["status"])):
                if col < 4:
                    item = NumericItem("—" if value is None else f"{value:.5g}")
                    item.setData(Qt.ItemDataRole.UserRole, value if value is not None else -float("inf"))
                else:
                    item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole + 1, key)
                item.setToolTip(str(value) if value is not None else "Nicht auswertbar / noch nicht gerechnet")
                self.ranking.setItem(index, col, item)
        self.ranking.resizeColumnsToContents()
        self.ranking.selectRow(selected_row)
        self.ranking.setSortingEnabled(True)
        self.ranking.blockSignals(False)
        if self.storage:
            config = self.storage.config
            modes = {"closed": "Geschlossen · Anfangsdrücke P₀", "constant": "Konstanter Druck", "none": "Ohne Innendruck"}
            finished = sum(c["status"] in ("success", "failed", "invalid") for c in self.storage.cases)
            p = config.base_parameters
            fits = [c["result"] for c in self.storage.cases if c["status"] == "success" and c["result"]]
            if config.fit_mode == "automatic":
                fit_text = "Fit automatisch ab Kontakt"
                if fits:
                    fit_text += f" · gemeinsam δ {fits[0]['fit_start_delta_mm']:.3f}–{fits[0]['fit_end_delta_mm']:.3f} mm"
                if any(c["status"] in ("pending", "running") for c in self.storage.cases):
                    fit_text += " (vorläufig)"
            else:
                fit_text = f"Fit δ {config.fit_start_after_contact_mm:g}–{config.fit_end_after_contact_mm:g} mm"
            self.summary.setText(f"{self.storage.directory.name} · {finished}/{len(self.storage.cases)} bearbeitet · "
                f"{modes[config.pressure_mode]}\nKugel R {p.pressure_shell.radius_mm:g} / t {p.pressure_shell.thickness_mm:g} mm · "
                f"{fit_text} · S in N/(mm·mmHg)")
            self.summary.setToolTip(str(self.storage.directory) + "\nMaterialien: " +
                ", ".join(v["name"] for v in self.storage.definition["materials"].values()))
        else:
            self.summary.setText("Noch keine Studie geladen")
        self.draw_map()
        self.draw_detail()

    def selected_geometry(self):
        row = self.ranking.currentRow()
        item = self.ranking.item(row, 0) if row >= 0 else None
        return tuple(item.data(Qt.ItemDataRole.UserRole + 1)) if item else None

    def draw_map(self):
        self.map_figure.clear()
        self.map_ax = self.map_figure.add_subplot()
        if not self.storage:
            self.map_ax.set_axis_off()
            self.map_canvas.draw_idle()
            return
        cfg = self.storage.config
        self.map_r = sorted(set(cfg.measurement_radii_mm))
        self.map_t = sorted(set(cfg.measurement_thicknesses_mm))
        values = np.full((len(self.map_t), len(self.map_r)), np.nan)
        by_key = {(r["radius_mm"], r["thickness_mm"]): r for r in self.rows}
        for iy, t in enumerate(self.map_t):
            for ix, r in enumerate(self.map_r):
                row = by_key[(r, t)]
                if row["sensitivity"] is not None:
                    values[iy, ix] = row["sensitivity"]
        import matplotlib
        cmap = matplotlib.colormaps["viridis"].copy()
        cmap.set_bad("#dce3eb")
        im = self.map_ax.imshow(np.ma.masked_invalid(values), origin="lower", aspect="auto", cmap=cmap)
        if np.isfinite(values).any():
            self.map_figure.colorbar(im, ax=self.map_ax, label="S [N/(mm·mmHg)]")
        for iy, t in enumerate(self.map_t):
            for ix, r in enumerate(self.map_r):
                row = by_key[(r, t)]
                self.map_ax.text(ix, iy, (f"{values[iy, ix]:.3g}" + ("" if row["preferred"] else " !"))
                                 if np.isfinite(values[iy, ix]) else "—", ha="center", va="center",
                                 color="white" if np.isfinite(values[iy, ix]) else "#64748b")
        self.map_ax.set_xticks(range(len(self.map_r)), [f"{v:g}" for v in self.map_r])
        self.map_ax.set_yticks(range(len(self.map_t)), [f"{v:g}" for v in self.map_t])
        self.map_ax.set(xlabel="Messschalenradius R [mm]", ylabel="Wanddicke t [mm]",
                        title="Druckempfindlichkeit · — fehlt/ungültig · ! prüfen")
        self.map_canvas.draw_idle()

    def select_map(self, event):
        if not self.storage or event.inaxes != self.map_ax or event.xdata is None:
            return
        ix, iy = round(event.xdata), round(event.ydata)
        if not (0 <= ix < len(self.map_r) and 0 <= iy < len(self.map_t)):
            return
        key = (self.map_r[ix], self.map_t[iy])
        for row in range(self.ranking.rowCount()):
            if tuple(self.ranking.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)) == key:
                self.ranking.selectRow(row)
                break

    def draw_detail(self):
        self.detail_figure.clear()
        force_ax, k_ax = self.detail_figure.subplots(2, 1)
        self.case_table.setRowCount(0)
        self.detail_cases = []
        key = self.selected_geometry()
        if self.storage and key:
            cases = [c for c in self.storage.cases if
                     (c["parameters"]["measurement_shell"]["radius_mm"],
                      c["parameters"]["measurement_shell"].get("thickness_x_mm",
                          c["parameters"]["measurement_shell"].get("thickness_mm"))) == key]
            cases.sort(key=lambda c: c["parameters"]["pressure_mmhg"])
            self.detail_cases = cases
            self.case_table.setRowCount(len(cases))
            pressures, stiffness = [], []
            for index, case in enumerate(cases):
                result = case["result"]
                for column, value in enumerate((case["parameters"]["pressure_mmhg"],
                    f"{result['stiffness']:.5g}" if result else "—",
                    f"{result['r_squared']:.5g}" if result and result['r_squared'] is not None else "—", case["status"],
                    case["error"] or "; ".join(result["warnings"] if result else []))):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.case_table.setItem(index, column, item)
                if case["status"] != "success" or not result:
                    continue
                try:
                    curve = read_force_curve(self.storage.run_path(case["attempts"][-1]) / "force_displacement.csv")
                    p = case["parameters"]["pressure_mmhg"]
                    x = np.asarray(curve.displacement_mm) - result["contact_mm"]
                    line, = force_ax.plot(x, curve.force_n, label=f"{p:g} mmHg")
                    a, b = result["fit_start_delta_mm"], result["fit_end_delta_mm"]
                    force_ax.plot([a, b], [result["stiffness"] * u + result["intercept"] for u in (a, b)],
                                  "--", color=line.get_color(), linewidth=2)
                    pressures.append(p)
                    stiffness.append(result["stiffness"])
                except (OSError, ValueError):
                    continue
            fitted = next((c["result"] for c in cases if c["status"] == "success" and c["result"]), None)
            if fitted:
                force_ax.axvspan(fitted["fit_start_delta_mm"], fitted["fit_end_delta_mm"], color="#e6b76c", alpha=.15)
            force_ax.set_title(f"Messschale R {key[0]:g} / t {key[1]:g} mm")
            if pressures:
                force_ax.legend(fontsize=8)
                k_ax.plot(pressures, stiffness, "o-", color="#0b9185")
        force_ax.set(xlabel="Weg ab Kontakt δ [mm]", ylabel="Kraft [N]")
        k_ax.set(xlabel="Anfangsdruck P₀ [mmHg]" if self.storage and self.storage.config.pressure_mode == "closed"
                 else "Druck [mmHg]", ylabel="Steifigkeit k [N/mm]")
        for ax in (force_ax, k_ax):
            ax.grid(alpha=.2)
        self.case_table.resizeColumnsToContents()
        self.detail_canvas.draw_idle()

    def open_case(self, row, _column):
        case = self.detail_cases[row]
        if case["attempts"]:
            path = self.storage.run_path(case["attempts"][-1]) / "force_displacement.csv"
            if path.is_file():
                self.open_result.emit(path)
