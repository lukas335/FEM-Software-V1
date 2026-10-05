"""Shared compact drive-control editor for single runs and study snapshots."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QToolButton, QVBoxLayout, QWidget


class DriveControlEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItem("Fester Zustellweg", "displacement")
        self.mode.addItem("Zustellen bis Zielkraft", "target_force")
        layout.addWidget(self.mode)
        self.force_panel = QWidget()
        form = QFormLayout(self.force_panel)
        form.setContentsMargins(0, 0, 0, 0)
        self.fields = {}

        def spin(name, value, suffix, decimals=5):
            box = QDoubleSpinBox()
            box.setDecimals(decimals)
            box.setRange(0, 10000)
            box.setValue(value)
            box.setSuffix(suffix)
            box.valueChanged.connect(self._changed)
            self.fields[name] = box
            return box

        form.addRow("Zielkraft (360°)", spin("target_force_n", .2, " N"))
        self.details = QToolButton()
        self.details.setText("Regleroptionen")
        self.details.setCheckable(True)
        form.addRow(self.details)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        for caption, name, value, suffix in (
            ("Absolute Toleranz ±", "force_tolerance_abs_n", .02, " N"),
            ("Relative Toleranz", "force_tolerance_rel", 0, ""),
            ("Annäherungsschritt", "approach_step_mm", .05, " mm"),
            ("Kleinster Schritt", "min_drive_step_mm", .0001, " mm"),
            ("Größter Schritt", "max_drive_step_mm", .1, " mm"),
        ):
            advanced.addRow(caption, spin(name, value, suffix))
        self.fields["force_tolerance_rel"].setToolTip("0 = aus (Standard: ±0,02 N absolut). Anteil: 0,005 = 0,5 %. Es gilt die größere absolute/relative Toleranz.")
        trials = QSpinBox()
        trials.setRange(1, 10000)
        trials.setValue(200)
        trials.valueChanged.connect(self._changed)
        self.fields["max_control_trials"] = trials
        advanced.addRow("Max. Versuche", trials)
        form.addRow(self.advanced)
        layout.addWidget(self.force_panel)
        self.advanced.hide()
        self.details.toggled.connect(self.advanced.setVisible)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()
        self.setToolTip("Der Solver bleibt weggesteuert. Im Zielkraftmodus ist der maximale Zustellweg eine harte Sicherheitsgrenze.")

    def _mode_changed(self, *_):
        self.force_panel.setVisible(self.mode.currentData() == "target_force")
        self.changed.emit()

    def _changed(self, *_):
        self.changed.emit()

    def values(self):
        return dict(drive_control=self.mode.currentData(), **{name: box.value() for name, box in self.fields.items()})

    def restore(self, params):
        for name, box in self.fields.items():
            box.setValue(getattr(params, name))
        self.mode.setCurrentIndex(self.mode.findData(params.drive_control))
