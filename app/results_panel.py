"""Desktop dashboard for a completed solver case."""

import json
from pathlib import Path
import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                              QLabel, QMessageBox, QPushButton, QSplitter,
                              QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from model.results import ForceCurve, automatic_fit_range, fit_stiffness, read_force_curve
from model.materials import MATERIALS
from app.animation_panel import AnimationPanel


def label(text: str, name: str = "muted") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


class PlotToolbar(NavigationToolbar2QT):
    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems
                      if item[0] in ("Home", "Back", "Forward", "Pan", "Zoom"))


class ResultsPanel(QWidget):
    def __init__(self, project_root: Path):
        super().__init__()
        self.setObjectName("workspace")
        self.setMinimumHeight(730)
        self.project_root = project_root
        self.curve: ForceCurve | None = None
        self.annotation = None
        self.time_cursor = None
        self.time_point = None
        self.pressure_cursor = None
        self.pressure_point = None
        self.contact = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(16)

        heading = QHBoxLayout()
        heading.addWidget(label("Ergebnisse", "sectionTitle"))
        self.case_label = label("Noch kein Lauf geladen")
        heading.addWidget(self.case_label)
        heading.addStretch()
        self.reload_button = QPushButton("Aktualisieren")
        self.reload_button.clicked.connect(self.reload)
        self.open_button = QPushButton("Ergebnis öffnen …")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.open_file)
        self.export_button = QPushButton("Plot exportieren …")
        self.export_button.clicked.connect(self.export_plot)
        for button in (self.reload_button, self.open_button, self.export_button):
            heading.addWidget(button)
        layout.addLayout(heading)

        metrics = QGridLayout()
        metrics.setSpacing(8)
        self.values = {}
        for index, (key, title, unit) in enumerate((("k", "Steifigkeit · N/mm", "Linearer Fit nach Erstkontakt"),
                                 ("c", "Compliance · mm/N", "1/k"),
                                 ("r2", "R²", "1,0 = linearer Verlauf"),
                                 ("fmax", "Max. Kraft · N", "Gesamtumfang 360°"))):
            frame, content = card()
            content.setContentsMargins(12, 10, 12, 10)
            content.setSpacing(4)
            content.addWidget(label(title, "metricTitle"))
            self.values[key] = label("—", "metricValue")
            content.addWidget(self.values[key])
            frame.setToolTip(unit)
            metrics.addWidget(frame, index // 2, index % 2)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        curve_card, curve_layout = card()
        curve_card.setMinimumWidth(370)
        curve_layout.addLayout(metrics)
        curve_layout.addWidget(label("Kraft & Druck", "sectionTitle"))
        self.curve_description = label("Reaktionskraft an der Messhalterung · Zustellweg inklusive Leerweg")
        self.curve_description.setWordWrap(True)
        curve_layout.addWidget(self.curve_description)
        self.curve_description.hide()
        curve_card.setToolTip(self.curve_description.text())
        controls = QHBoxLayout()
        self.fit_range_label = label("Fit automatisch ab Kontakt")
        self.fit_range_label.setWordWrap(True)
        controls.addWidget(self.fit_range_label)
        curve_layout.addLayout(controls)
        self.figure = Figure(figsize=(3.5, 5), facecolor="white", layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(170)
        self.ax, self.pressure_ax = self.figure.subplots(2, 1, gridspec_kw={"height_ratios": [1.15, 1]})
        curve_layout.addWidget(self.canvas, 1)
        self.include_preload = QCheckBox("Druckaufbau mit anzeigen")
        self.include_preload.setToolTip("Standard: Druck während der Indentation. Optional den gesamten Druckaufbau einblenden.")
        self.include_preload.toggled.connect(self.draw_curve)
        curve_layout.addWidget(self.include_preload)
        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(PlotToolbar(self.canvas, self, coordinates=False))
        self.point_label = label("Maus über einen Kurvenpunkt bewegen")
        self.point_label.setWordWrap(True)
        curve_layout.addLayout(toolbar_row)
        curve_layout.addWidget(self.point_label)
        self.point_label.hide()
        self.fit_message = label("", "fitMessage")
        self.fit_message.setWordWrap(True)
        curve_layout.addWidget(self.fit_message)
        split.addWidget(curve_card)

        mesh_card, mesh_layout = card()
        mesh_card.setMinimumWidth(640)
        self.animation = AnimationPanel()
        self.mesh_view = self.animation.view
        self.animation.time_changed.connect(self._set_animation_time)
        mesh_layout.addWidget(self.animation, 1)
        self.mesh_info = label("Netz und Parameter aus dem Laufverzeichnis")
        self.mesh_info.setWordWrap(True)
        mesh_layout.addWidget(self.mesh_info)
        self.mesh_info.hide()
        split.addWidget(mesh_card)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([400, 1160])
        layout.addWidget(split, 1)

        self.source_label = label("Lade eine force_displacement.csv aus einem Simulationslauf.")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.source_label)
        self.source_label.hide()
        self.animation.setToolTip("Axialschnitt aus realen Knotenverschiebungen, radial gespiegelt. "
                                  "Zwischenstände linear interpoliert; quasistatische Lastzeit, keine physikalische Dauer.")
        self.canvas.mpl_connect("motion_notify_event", self._hover)
        self.draw_curve()

    def heightForWidth(self, width: int) -> int:
        # Wrapped captions otherwise make QScrollArea reserve the plots' full
        # preferred height even when the canvases could comfortably shrink.
        return 730

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "FEM-Ergebnis laden",
                                            str(self.project_root / "work/simulations"), "Ergebnis-CSV (*.csv)")
        if path:
            self.load_case(Path(path))

    def reload(self):
        if self.curve:
            self.load_case(self.curve.source)

    def load_case(self, path: Path, *, show_errors: bool = True) -> bool:
        try:
            curve = read_force_curve(path)
        except (OSError, ValueError) as exc:
            if show_errors:
                QMessageBox.warning(self, "Ergebnis konnte nicht geladen werden", str(exc))
            else:
                self.source_label.setText(f"Ergebnis nicht geladen: {exc}")
            return False
        metadata = {}
        try:
            raw = json.loads(path.with_name("case.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                metadata = raw
        except (OSError, ValueError):
            pass
        self.curve = curve
        self.target_force_n = None
        params = metadata.get("parameters", {})
        if not isinstance(params, dict):
            params = {}
        pressure = params.get("pressure_mmhg", "?")
        status = metadata.get("febio_status")
        state = "Konvergiert" if status == "success" else (f"Laufstatus: {status}" if status else "CSV importiert")
        mode = "P₀ " if metadata.get("cavity_model") else ""
        self.case_label.setText(f"{mode}{pressure} mmHg  ·  {state}")
        if params.get("drive_control") == "target_force":
            self.target_force_n = params.get("target_force_n")
            reasons = {"target_reached": "Zielkraft erreicht", "displacement_limit": "Weglimit erreicht",
                       "no_contact": "Kein Kontakt bis Weglimit", "minimum_step_reached": "Minimaler Wegschritt",
                       "trial_limit": "Versuchslimit", "nonmonotonic_response": "Nichtmonotone Kraftantwort",
                       "solver_failed": "Solverfehler", "timeout": "Zeitlimit"}
            reason = metadata.get("termination_reason", "unbekannt")
            title = reasons.get(reason, reason)
            if reason != "target_reached":
                title = "Zielkraft NICHT erreicht: " + title
            self.case_label.setText(f"{mode}{pressure} mmHg · {title} · "
                                    f"F = {curve.force_n[-1]:.5f} / {self.target_force_n:g} N")
        constant_pressure = metadata.get("cavity_model") == "P0_preload_then_constant_pressure"
        if metadata.get("cavity_model"):
            self.case_label.setText(self.case_label.text() + (" · Druck konstant" if constant_pressure else " · Volumen konstant"))
        pressure_material = params.get("pressure_shell", {}).get("material_name")
        if pressure_material in MATERIALS:
            self.case_label.setText(self.case_label.text() + "  ·  " + MATERIALS[pressure_material].name)
        if params.get("cover_shell", {}).get("enabled"):
            self.case_label.setText(self.case_label.text() + " · Deckschale: zwei Materialgebiete")
        if curve.force_n[0] > 1e-6:
            self.case_label.setText(self.case_label.text() + "  ·  Kontaktvorlast: Spalt prüfen")
        cavity_warnings = metadata.get("cavity_results", {}).get("warnings", [])
        if cavity_warnings:
            self.case_label.setText(self.case_label.text() + "  ·  " + " · ".join(cavity_warnings))
        self.source_label.setText(f"Lauf: {curve.source.parent.name}  ·  {len(curve.force_n)} Punkte  ·  "
                                  f"uₘₐₓ = {curve.displacement_mm[-1]:.3f} mm  ·  F(u₀) = {curve.force_n[0]:.4f} N")
        self.source_label.setToolTip(str(curve.source))
        self.case_label.setToolTip(str(curve.source.parent))
        self.mesh_info.setText(f"FEBio {metadata.get('febio_version', '—')}  ·  "
                               f"{metadata.get('nodes', '—')} Knoten  ·  Wedge {params.get('wedge_angle_deg', '—')}°\n"
                               f"Anfangsspalt: {params.get('initial_gap_mm', 'nicht dokumentiert')} mm  ·  "
                               "Quelle: Eingabenetz und Knotenverschiebungen des geladenen Laufs")
        self.mesh_view.setToolTip(self.mesh_info.text())
        # Loading animation emits time_changed before the new plots exist.
        self.time_cursor = self.time_point = None
        self.pressure_cursor = self.pressure_point = None
        self.animation.load_case(path.with_name("model.feb"), constant_pressure=constant_pressure)
        self.draw_curve()
        return True

    def draw_curve(self):
        self.ax.clear()
        self.annotation = None
        self.time_cursor = None
        self.time_point = None
        self.contact = None
        self._draw_pressure()
        enabled = self.curve is not None
        self.reload_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled)
        for spine in ("top", "right", "left"):
            self.ax.spines[spine].set_visible(False)
        self.ax.spines["bottom"].set_color("#cbd5e1")
        self.ax.tick_params(colors="#64748b", labelsize=9, length=0, pad=8)
        self.ax.grid(axis="y", color="#e5edf3", linewidth=0.8)
        self.ax.set_axisbelow(True)
        self.ax.set_xlabel("Zustellweg u [mm]", color="#64748b", fontsize=10, labelpad=12)
        self.ax.set_ylabel("Reaktionskraft F [N]", color="#64748b", fontsize=10, labelpad=12)
        if self.curve is None:
            self.ax.text(0.5, 0.5, "Noch kein Ergebnis geladen", transform=self.ax.transAxes,
                         ha="center", va="center", color="#64748b")
            self.canvas.draw_idle()
            return
        curve = self.curve
        self.ax.plot(curve.displacement_mm, curve.force_n, color="#0b9185", linewidth=2.5,
                     marker="o", markersize=4, markeredgecolor="white", markeredgewidth=0.7,
                     label="FEBio · 360°")
        self.ax.fill_between(curve.displacement_mm, curve.force_n, color="#0b9185", alpha=0.07)
        if getattr(self, "target_force_n", None) is not None:
            self.ax.axhline(self.target_force_n, color="#8b5cb0", linestyle=":", linewidth=1, label="Zielkraft")
        self.ax.margins(x=0.04, y=0.15)
        self.ax.set_ylim(bottom=min(0, min(curve.force_n) * 1.15))
        self.fit_message.hide()
        self.fit_range_label.setText("Fit automatisch ab Kontakt")
        self.fit_range_label.setToolTip("")
        for key in ("k", "c", "r2"):
            self.values[key].setText("—")
        self.values["fmax"].setText(f"{max(curve.force_n):.4f}")
        try:
            self.contact = automatic_fit_range(curve)
            start, end = self.contact.start_mm, self.contact.end_mm
            self.fit_range_label.setText(f"Kontakt ≈ {self.contact.contact_mm:.3f} mm → {end:.3f} mm")
            self.fit_range_label.setToolTip(
                f"Kontakt aus Kraftanstieg geschätzt: [{self.contact.lower_mm:.4f}, {self.contact.upper_mm:.4f}] mm.\n"
                f"Schwelle {self.contact.threshold_n:.3g} N, mindestens 3 aufeinanderfolgende belastete Zustände.\n"
                f"Weg ab Kontakt bis Maximum: {end - self.contact.contact_mm:.4f} mm.\n"
                f"Regression aller belasteten Punkte von {start:.4f} bis {end:.4f} mm, freier Achsenabschnitt.")
            self.ax.axvline(self.contact.contact_mm, color="#64748b", linestyle=":", linewidth=1)
            fit = fit_stiffness(curve, start, end)
            self.values["k"].setText(f"{fit.stiffness:.4f}")
            self.values["c"].setText(f"{fit.compliance:.3f}" if fit.compliance is not None else "—")
            self.values["r2"].setText(f"{fit.r_squared:.5f}" if fit.r_squared is not None else "—")
            self.ax.axvspan(start, end, color="#e6b76c", alpha=0.13, linewidth=0)
            self.ax.plot([start, end], [fit.stiffness * v + fit.intercept for v in (start, end)],
                         color="#cc8640", linestyle="--", linewidth=2, label="Linearer Fit")
            self.fit_message.setText(f"{fit.count} Punkte im Fit  ·  F = {fit.stiffness:.4f} · u + {fit.intercept:.4f} N"
                                     + ("  ·  Keine positive Steifigkeit" if fit.stiffness <= 0 else ""))
            self.values["k"].setToolTip(self.fit_message.text())
            if fit.stiffness <= 0:
                self.fit_message.setText("Keine positive Steifigkeit im Fit-Bereich")
                self.fit_message.show()
        except ValueError as exc:
            self.fit_message.setText(str(exc))
            self.fit_message.show()
        self.ax.legend(loc="upper left", frameon=False, fontsize=8)
        self.annotation = self.ax.annotate("", xy=(0, 0), xytext=(10, 12), textcoords="offset points",
                                            fontsize=9, color="white", bbox=dict(boxstyle="round,pad=0.6", fc="#102a3b", ec="none"))
        self.annotation.set_visible(False)
        self.time_cursor = self.ax.axvline(curve.displacement_mm[0], color="#d18c42", linewidth=1, alpha=0.8)
        self.time_point, = self.ax.plot([], [], "o", color="#d18c42", markersize=7, zorder=5)
        self._set_animation_time(self.animation.current_time)
        self.point_label.setText("Maus über einen Kurvenpunkt bewegen")
        self.canvas.draw_idle()

    def _draw_pressure(self):
        ax = self.pressure_ax
        ax.clear()
        self.pressure_cursor = None
        self.pressure_point = None
        cavity = self.animation.cavity
        self.include_preload.setEnabled(cavity is not None)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#cbd5e1")
        ax.tick_params(colors="#64748b", labelsize=8, length=0, pad=6)
        ax.grid(axis="y", color="#e5edf3", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylabel("Innendruck [mmHg]", color="#64748b", fontsize=10)
        ax.set_xlabel("Lastzeit t", color="#64748b", fontsize=10)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.locator_params(axis="y", nbins=4)
        if cavity is None:
            ax.text(0.5, 0.5, "Keine Druckdaten in diesem Lauf", transform=ax.transAxes,
                    ha="center", va="center", color="#64748b", fontsize=9, wrap=True)
            return
        first_time = cavity.time[0] if self.include_preload.isChecked() else 1.0
        mask = cavity.time >= first_time
        ax.plot(cavity.time[mask], cavity.pressure_mmhg[mask], color="#4f70cf", linewidth=2)
        ax.set_xlim(first_time, cavity.time[-1])
        ax.margins(y=0.15)
        ax.set_title("Innendruck · " + ("gesamter Verlauf" if self.include_preload.isChecked() else "Indentation"),
                     loc="left", fontsize=10, color="#263c52")
        if self.include_preload.isChecked():
            ax.axvline(1.0, color="#94a3b8", linestyle=":", linewidth=1)
        if self.curve is not None:
            try:
                contact = automatic_fit_range(self.curve)
                contact_time = np.interp(contact.contact_mm, self.curve.displacement_mm, self.curve.time)
                ax.axvline(contact_time, color="#64748b", linestyle=":", linewidth=1)
            except ValueError:
                pass
        self.pressure_cursor = ax.axvline(first_time, color="#d18c42", linewidth=1, alpha=0.8)
        self.pressure_point, = ax.plot([], [], "o", color="#d18c42", markersize=6, zorder=5)

    def _set_animation_time(self, time: float):
        if self.curve is not None and self.time_cursor is not None:
            visible = self.curve.time[0] <= time <= self.curve.time[-1]
            self.time_cursor.set_visible(visible)
            self.time_point.set_visible(visible)
            if visible:
                u = float(np.interp(time, self.curve.time, self.curve.displacement_mm))
                force = float(np.interp(time, self.curve.time, self.curve.force_n))
                self.time_cursor.set_xdata([u, u])
                self.time_point.set_data([u], [force])
        if self.pressure_cursor is not None and self.animation.cavity is not None:
            lo, hi = self.pressure_ax.get_xlim()
            visible = lo <= time <= hi
            self.pressure_cursor.set_visible(visible)
            self.pressure_point.set_visible(visible)
            if visible:
                pressure, _, _ = self.animation.cavity.at_time(time)
                self.pressure_cursor.set_xdata([time, time])
                self.pressure_point.set_data([time], [pressure])
        self.canvas.draw_idle()

    def _hover(self, event):
        if self.curve is None or self.annotation is None:
            return
        if event.inaxes == self.ax:
            positions = self.ax.transData.transform(list(zip(self.curve.displacement_mm, self.curve.force_n)))
            distances = [(xy[0] - event.x) ** 2 + (xy[1] - event.y) ** 2 for xy in positions]
            index = min(range(len(distances)), key=distances.__getitem__)
            if distances[index] < 400:
                u, force = self.curve.displacement_mm[index], self.curve.force_n[index]
                self.annotation.xy = u, force
                self.annotation.set_position((-12 if u > sum(self.ax.get_xlim()) / 2 else 12, 14))
                self.annotation.set_ha("right" if u > sum(self.ax.get_xlim()) / 2 else "left")
                relative = f"\nAb Kontakt: {u - self.contact.contact_mm:.3f} mm" if self.contact else ""
                self.annotation.set_text(f"{u:.3f} mm\n{force:.4f} N" + relative)
                self.annotation.set_visible(True)
                self.point_label.setText(f"u {u:.3f} mm   ·   F {force:.4f} N")
                self.canvas.draw_idle()
                return
        if self.annotation.get_visible():
            self.annotation.set_visible(False)
            self.canvas.draw_idle()

    def export_plot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Kraft- und Druckverlauf exportieren",
                                            str(self.curve.source.with_suffix(".png")), "PNG (*.png);;SVG (*.svg)")
        if path:
            try:
                self.figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
