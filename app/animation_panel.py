"""Playback controls for saved solver states; time is quasi-static load time."""

from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                              QPushButton, QSlider, QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

from visualization.mesh_view import MeshView
from model.cavity import read_cavity


class MeshToolbar(NavigationToolbar2QT):
    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems if item[0] in ("Pan", "Zoom"))


class AnimationPanel(QWidget):
    time_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.current_time = 0.0
        self.cavity = None
        self.constant_pressure = False
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.elapsed = QElapsedTimer()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.view = MeshView()
        self.view.setMinimumHeight(330)
        tools = QHBoxLayout()
        self.phase = QLabel("Referenzzustand")
        self.phase.setObjectName("sectionTitle")
        tools.addWidget(self.phase)
        self.pressure_label = QLabel("")
        self.pressure_label.setObjectName("sectionTitle")
        tools.addWidget(self.pressure_label)
        tools.addStretch()
        self.scale = QComboBox()
        for value in (1, 3, 5):
            self.scale.addItem(f"Verformung {value}×", value)
        self.scale.currentIndexChanged.connect(self._scale_changed)
        tools.addWidget(self.scale)
        self.reference = QCheckBox("Referenznetz")
        self.reference.setChecked(True)
        self.reference.toggled.connect(self.view.set_reference_visible)
        tools.addWidget(self.reference)
        layout.addLayout(tools)
        layout.addWidget(self.view, 1)

        navigation = QHBoxLayout()
        navigation.addWidget(MeshToolbar(self.view, self, coordinates=False))
        reset_view = QPushButton("Ansicht zurücksetzen")
        reset_view.clicked.connect(self.view.reset_view)
        navigation.addWidget(reset_view)
        navigation.addStretch()
        self.deformation_label = QLabel("Verformung in Originalgröße · 1×")
        self.deformation_label.setObjectName("muted")
        navigation.addWidget(self.deformation_label)
        self.deformation_label.hide()
        layout.addLayout(navigation)

        playback = QHBoxLayout()
        self.reset_button = QPushButton("Anfang")
        self.reset_button.clicked.connect(self.reset)
        playback.addWidget(self.reset_button)
        self.play_button = QPushButton("Abspielen")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self.toggle_play)
        playback.addWidget(self.play_button)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 2000)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(self._scrub)
        playback.addWidget(self.slider, 1)
        self.speed = QComboBox()
        for speed in (0.5, 1, 2):
            self.speed.addItem(f"Tempo {speed:g}×", speed)
        self.speed.setCurrentIndex(1)
        playback.addWidget(self.speed)
        layout.addLayout(playback)
        self.time_label = QLabel("Keine Animationsdaten geladen")
        self.time_label.setObjectName("muted")
        layout.addWidget(self.time_label)
        self.data_label = QLabel("")
        self.data_label.setObjectName("muted")
        self.data_label.setWordWrap(True)
        layout.addWidget(self.data_label)
        self._enable(False)

    def _enable(self, enabled: bool):
        for widget in (self.play_button, self.reset_button, self.slider, self.speed, self.scale):
            widget.setEnabled(enabled)

    def load_case(self, model_path: Path, *, constant_pressure: bool = False):
        self.pause()
        self.constant_pressure = constant_pressure
        self.view.load_mesh(model_path)
        self.cavity = None
        self.pressure_label.clear()
        if self.view.mesh is not None and model_path.with_name("cavity_history.csv").is_file():
            try:
                self.cavity = read_cavity(model_path.with_name("cavity_history.csv"), self.view.mesh.input_hash)
            except (OSError, ValueError) as exc:
                self.pressure_label.setText("Druckdaten ungültig")
                self.pressure_label.setToolTip(str(exc))
        self._enable(self.view.history is not None)
        self.data_label.setText(self.view.message + (" · Zwischenstände linear interpoliert"
                                                     if self.view.history is not None else ""))
        self.data_label.setVisible(self.view.history is None)
        self.slider.setToolTip(self.data_label.text())
        self.set_time(0)
        if self.view.history is not None:
            QTimer.singleShot(350, self._autoplay)

    def _autoplay(self):
        if self.isVisible() and self.view.history is not None and self.current_time == 0:
            self.play()

    def set_time(self, time: float):
        if self.view.history is None:
            self.current_time = 0.0
            self.slider.setValue(0)
            self.phase.setText("Unverformtes Eingabenetz")
            self.time_label.setText("Kein Verschiebungsexport vorhanden · Animation deaktiviert")
            return
        start, end = self.view.history.times[[0, -1]]
        self.current_time = max(float(start), min(float(end), time))
        maximum = self.view.set_time(self.current_time)
        self.slider.blockSignals(True)
        self.slider.setValue(round((self.current_time - start) / (end - start) * self.slider.maximum()))
        self.slider.blockSignals(False)
        phase = "Druckaufbau" if self.current_time <= 1 else "Indentation"
        self.phase.setText(phase)
        if self.cavity is not None:
            pressure, volume, error = self.cavity.at_time(self.current_time)
            self.pressure_label.setText(f"{pressure:.2f} mmHg")
            quantity = "Volumenänderung" if self.constant_pressure else "Volumenfehler"
            state = "Druckaufbau" if self.current_time <= 1 else f"{quantity} {error:.7%}"
            self.pressure_label.setToolTip(f"Innenvolumen {volume:.2f} mm³ · {state}")
        self.time_label.setText(f"t {self.current_time:.2f} / {end:g}   ·   Verformung max. {maximum:.3f} mm")
        self.time_changed.emit(self.current_time)

    def _scrub(self, value: int):
        if self.view.history is not None:
            start, end = self.view.history.times[[0, -1]]
            self.set_time(float(start + (end - start) * value / self.slider.maximum()))

    def play(self):
        if self.view.history is None:
            return
        if self.current_time >= self.view.history.times[-1]:
            self.set_time(float(self.view.history.times[0]))
        self.elapsed.start()
        self.timer.start()
        self.play_button.setText("Pause")

    def pause(self):
        self.timer.stop()
        self.play_button.setText("Abspielen")

    def toggle_play(self):
        self.pause() if self.timer.isActive() else self.play()

    def reset(self):
        self.pause()
        self.set_time(0)

    def _tick(self):
        if self.view.history is None:
            self.pause()
            return
        duration = float(self.view.history.times[-1] - self.view.history.times[0])
        delta = self.elapsed.restart() / 1000 * duration / 10 * self.speed.currentData()
        self.set_time(self.current_time + delta)
        if self.current_time >= self.view.history.times[-1]:
            self.pause()

    def _scale_changed(self):
        scale = self.scale.currentData()
        self.view.set_scale(scale)
        self.deformation_label.setText("Verformung in Originalgröße · 1×" if scale == 1
                                       else f"Darstellung {scale}× überhöht · Kontaktabstand optisch verfälscht")
        self.deformation_label.setVisible(scale != 1)

    def hideEvent(self, event):
        self.pause()
        super().hideEvent(event)
