"""Read-only mesh/BC inspection, available before and during a solver run."""
from pathlib import Path

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView,
                              QLabel, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget)

from model.mesh_inspection import read_inspection, solver_progress
from visualization.mesh_view import MeshView


class MeshInspectionPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.inspection = None
        self.overlays = []
        layout = QVBoxLayout(self)
        self.summary = QLabel('Noch kein Eingabenetz geladen. Im Modell „Netz & Randbedingungen prüfen“ wählen.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet('color: #9a3412; background: #fff7ed; padding: 8px;')
        self.warning.hide()
        layout.addWidget(self.warning)
        controls = QHBoxLayout()
        self.filters = {}
        for kind in ('Zustellung', 'Fixierung', 'Kontakt', 'Druck', 'Symmetrie', 'Verklebt', 'Volumen', 'Messpunkt'):
            box = QCheckBox(kind)
            box.setChecked(kind not in ('Kontakt', 'Symmetrie', 'Volumen'))
            box.toggled.connect(self.draw_boundaries)
            controls.addWidget(box)
            self.filters[kind] = box
        controls.addStretch()
        reset = QPushButton('Ansicht einpassen')
        reset.clicked.connect(self.reset_view)
        controls.addWidget(reset)
        layout.addLayout(controls)
        self.view = MeshView()
        self.view.show_empty('Netzprüfung ohne Solverstart')
        self.toolbar = NavigationToolbar2QT(self.view, self)
        layout.addWidget(self.toolbar)
        splitter = QSplitter()
        splitter.addWidget(self.view)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel('Exportierte Bedingungen · Zeile anklicken zum Hervorheben'))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Art', 'Zuordnung', 'Knoten', 'Details'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.draw_boundaries)
        right_layout.addWidget(self.table, 1)
        right_layout.addWidget(QLabel('Solverprotokoll · alle 2 Sekunden aktualisiert'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(175)
        right_layout.addWidget(self.log)
        splitter.addWidget(right)
        splitter.setSizes([1100, 500])
        layout.addWidget(splitter, 1)
        footer = QLabel('Axialschnitt des 3D-Wedge-Netzes; die untere Hälfte ist nur gespiegelt. '
                        'Rot: axiale Zustellung · Dunkelgrau: Fixierung · Blau: Druck · '
                        'Braun/Violett: Kontaktseiten · Gelb: ausgewählte Tabellenzeile. Keine Modelländerung.')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh_log)
        self.timer.start()

    def load_mesh(self, path: Path):
        # Read everything first; a corrupt/part-written input must not replace
        # the previous valid inspection with a mismatching BC table.
        inspection = read_inspection(path)
        self.view.load_mesh(path, reference_only=True)
        if self.view.mesh is None:
            raise ValueError(self.view.message)
        self.path, self.inspection = Path(path), inspection
        self.overlays.clear()  # old artists were removed with the cleared axes
        self.summary.setText(f'{inspection.node_count} Knoten · {inspection.element_count} Tet4-Elemente · '
                             f'min. Ausgangs-Jacobian {inspection.min_jacobian:.3g} mm³\n{path}')
        self.warning.setText('\n'.join(inspection.warnings))
        self.warning.setVisible(bool(inspection.warnings))
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(inspection.groups))
        for row, group in enumerate(inspection.groups):
            for column, value in enumerate((group.kind, group.name, str(len(group.nodes)), group.detail)):
                item = QTableWidgetItem(value)
                item.setToolTip(group.detail)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self.draw_boundaries()
        self.refresh_log()

    def draw_boundaries(self):
        if self.inspection is None or self.view.mesh is None:
            return
        for artist in self.overlays:
            artist.remove()
        self.overlays.clear()
        mesh = self.view.mesh
        lookup = {node: index for index, node in enumerate(mesh.node_ids)}
        points = self.view._project(mesh.coordinates)
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        for row, group in enumerate(self.inspection.groups):
            highlight = row in selected
            if not highlight and (group.kind not in self.filters or not self.filters[group.kind].isChecked()):
                continue
            color = '#facc15' if highlight else group.color
            ids = [lookup[node] for node in group.nodes]
            # Projection collapses both wedge sides; deduplicate the displayed
            # positions only, never the solver's node sets.
            xy = np.unique(np.round(points[ids], 9), axis=0) if ids else np.empty((0, 2))
            xy = np.concatenate((xy, xy * [1, -1]))
            edges = {tuple(sorted((a, b))) for face in group.faces for a, b in zip(face, face[1:] + face[:1])}
            segments = np.asarray([[points[lookup[a]], points[lookup[b]]] for a, b in edges])
            for ax in (self.view.ax, self.view.detail_ax):
                if len(edges):
                    lines = LineCollection(np.concatenate((segments, segments * [1, -1])),
                                           colors=color, linewidths=2.4 if highlight else 1.3,
                                           alpha=.9, zorder=6 if highlight else 4)
                    ax.add_collection(lines)
                    self.overlays.append(lines)
                # Symmetry/contact surfaces consist of many nodes. Their mesh
                # edges are enough; small point markers identify actual BC sets.
                if len(xy) and (highlight or group.kind in ('Zustellung', 'Fixierung', 'Messpunkt')):
                    artist = ax.scatter(xy[:, 0], xy[:, 1], s=18 if highlight else 8, color=color,
                                        zorder=7 if highlight else 5)
                    self.overlays.append(artist)
        self.view.draw_idle()

    def reset_view(self):
        self.view.reset_view()

    def refresh_log(self):
        if self.path is None:
            return
        try:
            text = solver_progress(self.path.parent)
        except OSError as error:
            text = f'Protokoll derzeit nicht lesbar: {error}'
        if self.log.toPlainText() != text:
            self.log.setPlainText(text)
