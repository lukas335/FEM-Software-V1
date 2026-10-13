"""Persisted, domain-specific delta-x measurement reference in the region tab."""
from model.measurement_definition import contour_references, reference_region
from preprocessor_markers import point_marker
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QGraphicsItem, QGraphicsSimpleTextItem, QGroupBox, QLabel, QPushButton, QVBoxLayout


class MeasurementReferenceMixin:
    def build_reference_group(self):
        self.reference_pick_active = False
        group = QGroupBox('Referenzpunkt für Δx')
        layout = QVBoxLayout(group)
        self.measurement_point = QComboBox()
        self.measurement_point.setToolTip('Konturpunkt und zugehöriger Materialbereich. An reibungsfreien Interfaces ist der Bereich entscheidend.')
        self.measurement_point.currentIndexChanged.connect(self.measurement_point_changed)
        self.reference_pick_button = QPushButton('Punkt im Bild wählen')
        self.reference_pick_button.setCheckable(True)
        self.reference_pick_button.toggled.connect(self.set_reference_pick_mode)
        self.reference_info = QLabel()
        self.reference_info.setWordWrap(True)
        layout.addWidget(QLabel('Δx = ux(Zustellung) − ux(Referenzpunkt)'))
        layout.addWidget(self.measurement_point)
        layout.addWidget(self.reference_pick_button)
        layout.addWidget(self.reference_info)
        return group

    def refresh_region_ui(self):
        super().refresh_region_ui()
        self.refresh_reference_ui()

    def saved_measurement_reference(self):
        metadata = self.document.data.get('metadata', {})
        point_id = metadata.get('sensor_tip_point')
        # Older DXF/editor snapshots stored only the point. Display the same
        # inferred owner as the main importer, until the user chooses one.
        owner = metadata.get('sensor_tip_region')
        if point_id and not owner:
            owner = reference_region(self.document.data)
        return point_id, owner

    def refresh_reference_ui(self):
        combo = self.measurement_point
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('Kein Punkt gewählt – Import-Standard', None)
        candidates = contour_references(self.document)
        for point, region_id in candidates:
            combo.addItem(f"{point['id']} · {region_id} · ({point['x']:.3g}, {point['y']:.3g}) mm",
                          (point['id'], region_id))
        selected = self.saved_measurement_reference()
        index = next((i for i in range(combo.count()) if combo.itemData(i) == selected), -1)
        if selected[0] and index < 0:
            combo.addItem('Ungültiger Referenzpunkt – bitte neu wählen', selected)
            index = combo.count() - 1
            message = 'Gespeicherter Punkt/Bereich fehlt. Import wird abgelehnt; bitte neu wählen.'
        elif selected[0]:
            message = 'Wird beim Speichern in die JSON-Datei übernommen. Punkt folgt der parametrischen Geometrie.'
        else:
            message = 'Ohne Auswahl bleibt die bisherige Messpunkt-Vorgabe beim Import erhalten.'
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        self.reference_info.setText(message)
        self.reference_pick_button.setEnabled(bool(candidates))

    def measurement_point_changed(self):
        reference = self.measurement_point.currentData()
        if reference and reference not in {(p['id'], r) for p, r in contour_references(self.document)}:
            return
        def store():
            metadata = self.document.data.setdefault('metadata', {})
            if reference:
                metadata['sensor_tip_point'], metadata['sensor_tip_region'] = reference
            else:
                metadata.pop('sensor_tip_point', None)
                metadata.pop('sensor_tip_region', None)
        self.mutate('Messreferenz wählen', store)
        self.reference_pick_button.setChecked(False)
        self.refresh()

    def set_reference_pick_mode(self, enabled):
        self.reference_pick_active = enabled
        self.region_view.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if enabled:
            self.reference_info.setText('Konturpunkt anklicken (Fangradius 8 px). Bei gemeinsamen Punkten zuerst den Bereich links wählen. Esc beendet die Auswahl.')
        self.draw_region_view()

    def pick_measurement_point(self, screen_position):
        candidates = contour_references(self.document)
        if not candidates:
            return
        def distance(candidate):
            point, _ = candidate
            from PySide6.QtCore import QPointF
            screen = self.region_view.mapFromScene(QPointF(point['x'], -point['y']))
            return (screen.x() - screen_position.x()) ** 2 + (screen.y() - screen_position.y()) ** 2
        nearest = min(candidates, key=distance)
        if distance(nearest) > 8 ** 2:
            self.reference_info.setText('Bitte einen markierten Konturpunkt anklicken.')
            return
        point_id = nearest[0]['id']
        owners = {region_id for point, region_id in candidates if point['id'] == point_id}
        item = self.region_list.currentItem()
        region_id = item.data(Qt.UserRole) if item else None
        if region_id not in owners:
            if len(owners) != 1:
                self.reference_info.setText('Dieser Punkt gehört zu mehreren Bereichen. Bitte den gewünschten Bereich links wählen.')
                return
            region_id = next(iter(owners))
        reference = (point_id, region_id)
        index = next(i for i in range(self.measurement_point.count())
                     if self.measurement_point.itemData(i) == reference)
        if self.measurement_point.currentIndex() == index:
            self.reference_pick_button.setChecked(False)
            self.refresh_reference_ui()
        else:
            self.measurement_point.setCurrentIndex(index)

    def draw_region_view(self):
        super().draw_region_view()
        if not hasattr(self, 'measurement_point'):
            return
        selected = self.saved_measurement_reference()
        seen = set()
        for point, region_id in contour_references(self.document):
            reference = (point['id'], region_id)
            if reference == selected:
                marker = point_marker(self.region_scene, point['x'], point['y'], radius=4,
                                      color='#ad4800', fill='#ffe5bd', z=30)
                marker.setToolTip(f"Δx-Referenz: {point['id']} · {region_id}")
                label = QGraphicsSimpleTextItem(f"Δx-Referenz · {region_id}", marker)
                label.setBrush(QColor('#813d08'))
                label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                label.setPos(8, -22)
            elif self.reference_pick_active and point['id'] not in seen:
                point_marker(self.region_scene, point['x'], point['y'])
            seen.add(point['id'])
