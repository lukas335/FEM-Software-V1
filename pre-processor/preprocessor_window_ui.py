from __future__ import annotations

from pathlib import Path

from preprocessor_common import *
from preprocessor_view import SketchView
from preprocessor_region_view import RegionView
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QGraphicsScene, QListWidget, QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
    QInputDialog, QTabWidget, QScrollArea, QCheckBox)


class WindowUiMixin:
    def __init__(self, document, history):
        super().__init__(); self.document = document; self.history = history; self.document_path = None; self.update_file_title(); self.resize(1400, 850); self.selected_boundary = None
        self.setStyleSheet("""
            QMainWindow { background:#d2d3d5; }
            QWidget { color:#202020; font-family:'Segoe UI'; font-size:10pt; }
            #ribbon { background:#ededed; border-bottom:1px solid #aeb1b5; }
            #ribbonGroup { background:#e5e6e8; border:1px solid #c4c7ca; border-radius:2px; }
            #ribbonTitle { color:#555; font-size:8pt; padding:2px 6px; }
            QToolButton { background:transparent; border:1px solid transparent; padding:3px 6px; min-width:62px; }
            QToolButton:hover { background:#dcecff; border:1px solid #6d9dcc; }
            QToolButton:pressed { background:#bdd7f2; border:1px solid #4e83b7; }
            #featurePanel { background:#f1f1f1; border-right:1px solid #aeb1b5; }
            #featureHeader { background:#e5e5e5; font-weight:bold; padding:7px; border-bottom:1px solid #c5c5c5; }
            QListWidget { background:#ffffff; border:1px solid #c6c6c6; }
            QGraphicsView { background:#e7e8ea; border:0; }
            QPushButton { background:#ededed; border:1px solid #a9a9a9; padding:5px 8px; }
            QPushButton:hover { background:#dcecff; }
        """)
        self.view = SketchView(self, self.document, self.history); self.list = QListWidget(); self.status = QLabel(); self.status.setWordWrap(True); self._build_ui(); self.refresh(); self.view.set_mode("select")

    def _button(self, text, callback):
        button = QPushButton(text); button.clicked.connect(callback); return button

    def _build_ui(self):
        ribbon = QWidget(); ribbon.setObjectName("ribbon"); ribbon_row = QHBoxLayout(ribbon); ribbon_row.setContentsMargins(7, 5, 7, 5); ribbon_row.setSpacing(6)
        self._ribbon_group(ribbon_row, "Skizze", [("Beenden", lambda: self.close()), ("Auswahl", lambda: self.view.set_mode("select"))])
        self._ribbon_group(ribbon_row, "Geometrie", [("Linie", lambda: self.view.set_mode("line")), ("Bogen", lambda: self.view.set_mode("arc")), ("Trimmen", lambda: self.view.set_mode("trim"))])
        self._ribbon_group(ribbon_row, "Bemaßung", [("Smart Maß", lambda: self.view.set_mode("dimension")), ("Maß ändern", self.edit_dimension), ("Radius", self.radius_dimension), ("Parameter", self.parameter_manager)])
        self._ribbon_group(ribbon_row, "Beziehungen", [("Horizontal", lambda: self.apply_relation("horizontal")), ("Vertikal", lambda: self.apply_relation("vertical")), ("Tangential", self.tangent_relation), ("Coincident", self.coincident_relation), ("Coincident ext.", lambda: self.coincident_relation(True))])
        self._ribbon_group(ribbon_row, "Bearbeiten", [("Löschen", self.delete_selected), ("Undo", self.undo), ("Redo", self.redo)])
        self._ribbon_group(ribbon_row, "Datei / Ansicht", [("Öffnen", self.load), ("Speichern", self.save), ("Speichern unter …", self.save_as), ("Einpassen", self.fit_view)])
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save)
        self.save_as_shortcut = QShortcut(QKeySequence.StandardKey.SaveAs, self)
        self.save_as_shortcut.activated.connect(self.save_as)
        ribbon_row.addStretch()
        self.parameter_table = QTableWidget(0, 2); self.parameter_table.setHorizontalHeaderLabels(["Parameter", "Wert [mm]"]); self.parameter_table.setMinimumHeight(90); self.parameter_table.cellDoubleClicked.connect(self._edit_parameter_cell)
        class RelationList(QListWidget):
            def __init__(self, owner):
                super().__init__(); self.owner = owner
            def keyPressEvent(self, event):
                if event.key() == Qt.Key_Delete:
                    self.owner.delete_selected_relations(); event.accept(); return
                super().keyPressEvent(event)

        self.relation_list = RelationList(self); self.relation_list.setMaximumHeight(130)
        self.relation_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        relation_box = QGroupBox("Geometriebeziehungen"); relation_layout = QVBoxLayout(relation_box); relation_layout.addWidget(self.relation_list); relation_layout.addWidget(self._button("Beziehung löschen", self.delete_selected_relations))
        left = QVBoxLayout(); header = QLabel("FeatureManager\nSketch1"); header.setObjectName("featureHeader"); left.addWidget(header); left.addWidget(QLabel("Geometrie / Beziehungen")); left.addWidget(self.list); left.addWidget(relation_box)
        parameter_box = QGroupBox("Geometrieparameter"); parameter_layout = QVBoxLayout(parameter_box); parameter_layout.addWidget(self.parameter_table); parameter_layout.addWidget(self._button("Parameter verwalten", self.parameter_manager)); parameter_layout.addWidget(self._button("Parameter löschen", self.delete_parameter)); left.addWidget(parameter_box)
        left.addWidget(self._button("Skizze validieren", self.validate_view)); left.addWidget(self.status)
        panel = QWidget(); panel.setObjectName("featurePanel"); panel.setFixedWidth(250); panel.setLayout(left)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.addWidget(panel, 0); body.addWidget(self.view, 1)
        geometry_root = QVBoxLayout(); geometry_root.setContentsMargins(0, 0, 0, 0); geometry_root.setSpacing(0); geometry_root.addWidget(ribbon); geometry_root.addLayout(body, 1)
        geometry_tab = QWidget(); geometry_tab.setLayout(geometry_root)
        self.tabs = QTabWidget(); self.tabs.addTab(geometry_tab, "Geometrie"); self.tabs.addTab(self._build_regions_tab(), "Bereiche & Materialien")
        self.tabs.currentChanged.connect(self.regions_tab_changed)
        self.setCentralWidget(self.tabs); self.list.itemSelectionChanged.connect(self.feature_list_selection_changed)

    def _build_regions_tab(self):
        self.region_list = QListWidget(); self.region_list.currentItemChanged.connect(self._region_selection_changed)
        self.region_material = QComboBox()
        for key, material in MATERIALS.items(): self.region_material.addItem(getattr(material, "name", key), key)
        self.interface_list = QListWidget()
        self.interface_condition = QComboBox(); self.interface_condition.addItem("Verklebt", "bonded"); self.interface_condition.addItem("Reibungsfrei", "frictionless")
        self.boundary_list = QListWidget(); self.boundary_list.currentItemChanged.connect(self._boundary_selection_changed)
        self.boundary_behavior = QComboBox(); self.boundary_behavior.addItem("Keine Verschiebungsvorgabe", "free_contact"); self.boundary_behavior.addItem("Vorgegebene Verschiebung", "follow_translation")
        self.boundary_x_only = QCheckBox('Bewegung nur entlang x')
        self.boundary_x_only.setToolTip('uy = uz = 0. Unabhängig von der axialen Vorgabe: ux bleibt frei oder folgt der vorgegebenen Zustellung.')
        self.region_view = RegionView(self); self.region_scene = QGraphicsScene(self); self.region_view.setScene(self.region_scene); self.region_view.setObjectName("regionView")
        left = QVBoxLayout(); left.addWidget(QLabel("Geschlossene Bereiche")); left.addWidget(self._button("Geschlossene Bereiche erkennen", self.detect_regions)); left.addWidget(self.region_list)
        material_box = QGroupBox("Material"); material_layout = QVBoxLayout(material_box); material_layout.addWidget(self.region_material); material_layout.addWidget(self._button("Material zuweisen", self.apply_region_material)); left.addWidget(material_box)
        left.addWidget(self.build_reference_group())
        boundary_box = QGroupBox("Randkanten / Verschiebung"); boundary_layout = QVBoxLayout(boundary_box); boundary_layout.addWidget(QLabel("Kante auswählen:")); boundary_layout.addWidget(self.boundary_list); boundary_layout.addWidget(self.boundary_behavior); boundary_layout.addWidget(self.boundary_x_only); boundary_layout.addWidget(self._button("Randverhalten setzen", self.apply_boundary_behavior)); left.addWidget(boundary_box)
        interface_box = QGroupBox("Interfaces"); interface_layout = QVBoxLayout(interface_box); interface_layout.addWidget(self.interface_list); interface_layout.addWidget(self.interface_condition); interface_layout.addWidget(self._button("Interface-Bedingung setzen", self.apply_interface_condition)); left.addWidget(interface_box)
        left_panel = QWidget(); left_panel.setObjectName("featurePanel"); left_panel.setFixedWidth(300); left_panel.setLayout(left)
        # Keep controls reachable on smaller windows without shrinking the canvas.
        for widget in (self.region_list, self.boundary_list, self.interface_list):
            widget.setMinimumHeight(70); widget.setMaximumHeight(110)
        sidebar = QScrollArea(); sidebar.setWidgetResizable(True); sidebar.setWidget(left_panel); sidebar.setFixedWidth(325)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.addWidget(sidebar, 0); body.addWidget(self.region_view, 1)
        tab = QWidget(); tab.setLayout(body); return tab

    def _ribbon_group(self, parent, title, actions):
        group = QFrame(); group.setObjectName("ribbonGroup"); layout = QVBoxLayout(group); layout.setContentsMargins(2, 2, 2, 2); layout.setSpacing(0); row = QHBoxLayout(); row.setSpacing(1)
        for label, callback in actions:
            button = QToolButton(); button.setText(label); button.setIcon(self._ribbon_icon(label)); button.setIconSize(QSize(25, 25)); button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon); button.setToolTip(label); button.clicked.connect(callback); row.addWidget(button)
        layout.addLayout(row); caption = QLabel(title); caption.setObjectName("ribbonTitle"); caption.setAlignment(Qt.AlignCenter); layout.addWidget(caption); parent.addWidget(group)

    def _ribbon_icon(self, label):
        pixmap = QPixmap(30, 28); pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#356b96"), 2.0)); painter.setBrush(Qt.NoBrush)
        text = label.lower()
        if text.startswith("beenden"):
            painter.drawLine(7, 6, 23, 22); painter.drawLine(23, 6, 7, 22)
        elif text.startswith("auswahl"):
            painter.drawLine(8, 4, 8, 23); painter.drawLine(8, 4, 22, 14); painter.drawLine(8, 4, 15, 21)
        elif text.startswith("linie"):
            painter.drawLine(5, 23, 25, 5); painter.drawEllipse(3, 21, 4, 4); painter.drawEllipse(23, 3, 4, 4)
        elif text.startswith("bogen") or text.startswith("tangential"):
            painter.drawArc(QRectF(4, 4, 22, 22), 20 * 16, 140 * 16)
            if text.startswith("tangential"): painter.drawLine(4, 24, 14, 24)
        elif text.startswith("trimmen"):
            painter.drawLine(4, 22, 25, 5); painter.drawLine(6, 6, 14, 14); painter.drawLine(16, 16, 24, 24)
        elif "maß" in text or "maã" in text:
            painter.drawLine(6, 4, 6, 24); painter.drawLine(24, 4, 24, 24); painter.drawLine(6, 7, 24, 7); painter.drawLine(6, 21, 24, 21)
        elif text.startswith("radius"):
            painter.drawEllipse(6, 7, 15, 15); painter.drawLine(18, 8, 27, 3)
        elif text.startswith("horizontal"):
            painter.drawLine(5, 14, 25, 14); painter.drawLine(5, 14, 10, 10); painter.drawLine(5, 14, 10, 18); painter.drawLine(25, 14, 20, 10); painter.drawLine(25, 14, 20, 18)
        elif text.startswith("vertikal"):
            painter.drawLine(15, 4, 15, 24); painter.drawLine(15, 4, 11, 9); painter.drawLine(15, 4, 19, 9); painter.drawLine(15, 24, 11, 19); painter.drawLine(15, 24, 19, 19)
        elif text.startswith("löschen") or text.startswith("lã¶schen"):
            painter.setPen(QPen(QColor("#9b3e3e"), 2.2)); painter.drawLine(6, 6, 24, 22); painter.drawLine(24, 6, 6, 22)
        elif text.startswith("undo") or text.startswith("redo"):
            painter.drawArc(QRectF(5, 6, 20, 17), 40 * 16, 250 * 16); painter.drawLine(6, 8, 6, 15); painter.drawLine(6, 8, 13, 8)
        elif text.startswith("öffnen") or text.startswith("ã¶ffnen"):
            painter.drawRect(5, 6, 20, 17); painter.drawLine(9, 10, 21, 10)
        elif text.startswith("speichern"):
            painter.drawRect(5, 4, 20, 20); painter.drawRect(9, 5, 12, 7); painter.drawRect(9, 17, 12, 7)
        elif text.startswith("einpassen"):
            painter.drawRect(7, 6, 16, 16); painter.drawLine(4, 4, 10, 4); painter.drawLine(4, 4, 4, 10); painter.drawLine(26, 24, 20, 24); painter.drawLine(26, 24, 26, 18)
        else:
            painter.drawLine(7, 14, 23, 14); painter.drawLine(23, 14, 17, 8); painter.drawLine(23, 14, 17, 20)
        painter.end(); return QIcon(pixmap)
