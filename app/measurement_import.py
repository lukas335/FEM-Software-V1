"""Optional editor import controls; the legacy measurement fields stay intact."""
import copy
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QPushButton

from model.measurement_definition import axis_references, contour_references, definition_errors, load_definition, reference_region, sketch_document, with_reference


class MeasurementImportMixin:
    def build_measurement_controls(self):
        self.measurement_source = QComboBox()
        self.measurement_source.addItem('Wie bisher: Radius und Wanddicken', 'legacy')
        self.measurement_source.addItem('Importierte Editor-Geometrie', 'imported')
        self.measurement_import_button = QPushButton('Messschale importieren …')
        self.measurement_import_button.clicked.connect(self.import_measurement)
        self.measurement_import_info = QLabel('Noch keine Editor-Datei geladen.')
        self.measurement_import_info.setWordWrap(True)
        self.measurement_reference = QComboBox()
        self.measurement_reference.setToolTip('Dieser Konturpunkt auf der Rotationsachse wird auf x=0 gelegt. Der Anfangsspalt zur Druckkugel wird von hier gemessen.')
        self.measurement_reference.currentIndexChanged.connect(self.measurement_reference_changed)
        self.measurement_sensor = QComboBox()
        self.measurement_sensor.setToolTip('Δx = Gesamtzustellung ux − axiale Verschiebung dieses Referenzpunkts. Bei reibungsfreiem Interface zählt der gewählte Materialbereich.')
        self.measurement_sensor.currentIndexChanged.connect(self.measurement_sensor_changed)
        self.measurement_source.currentIndexChanged.connect(self.measurement_source_changed)
        self.measurement_reference.setEnabled(False)
        self.measurement_sensor.setEnabled(False)
        return [('Definition', self.measurement_source), ('', self.measurement_import_button),
                ('Import', self.measurement_import_info), ('Kontakt-Referenz', self.measurement_reference),
                ('Messpunkt', self.measurement_sensor)]

    def measurement_source_changed(self):
        imported = self.measurement_source.currentData() == 'imported'
        for widget in (self.r1, self.tx1, self.ty1, self.material1):
            widget.setEnabled(not imported)
        self.measurement_reference.setEnabled(imported and self.imported_measurement is not None)
        self.measurement_sensor.setEnabled(imported and self.imported_measurement is not None)
        self._parameters_changed()

    def active_measurement_definition(self):
        if self.measurement_source.currentData() == 'legacy':
            return None
        # Never silently run the old geometry if import mode has no file.
        return copy.deepcopy(self.imported_measurement) if self.imported_measurement is not None else {}

    def set_imported_measurement(self, definition, source='', activate=True):
        errors = definition_errors(definition, require_drive=False)
        if errors:
            raise ValueError(' | '.join(errors))
        self.imported_measurement = copy.deepcopy(definition)
        self.measurement_import_path = source
        document = sketch_document(definition)
        for combo in (self.measurement_reference, self.measurement_sensor):
            combo.blockSignals(True)
            combo.clear()
        for point in axis_references(document):
            for region in document.regions:
                if any(point['id'] in (document.element(ref)['start_point'], document.element(ref)['end_point'])
                       for ref in region['boundary']):
                    self.measurement_reference.addItem(f"{point['id']} · {region['id']} · x={point['x']:.4g} mm",
                                                       (point['id'], region['id']))
        for point, region_id in contour_references(document):
            self.measurement_sensor.addItem(
                f"{point['id']} · {region_id} · ({point['x']:.4g}, {point['y']:.4g}) mm",
                (point['id'], region_id))
        for combo, role in ((self.measurement_reference, 'alignment'), (self.measurement_sensor, 'sensor')):
            point_id = (definition['metadata'].get('alignment_point', definition['metadata']['sensor_tip_point'])
                        if role == 'alignment' else definition['metadata']['sensor_tip_point'])
            selected = (point_id, reference_region(definition, role))
            index = next((index for index in range(combo.count()) if combo.itemData(index) == selected), -1)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        drive_count = sum(behavior == 'follow_translation' for region in document.regions
                          for behavior in region.get('boundary_motion', {}).values())
        self.measurement_import_info.setText(f'{Path(source).name or "Gespeicherter Import"}\n'
                    f'{len(document.regions)} Bereiche · {len(document.interfaces)} Interfaces · {drive_count} Zustellkanten')
        self.measurement_import_info.setToolTip(str(source) + '\nMaterialien und Randverhalten stammen aus der Editor-Datei. Alle äußeren Flächen sind mögliche Kontaktflächen.')
        if activate:
            self.measurement_source.setCurrentIndex(self.measurement_source.findData('imported'))
        self.measurement_source_changed()

    def import_measurement(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Messschale aus Editor importieren',
                                            str(self.measurement_import_path or ''), 'Editor-Skizze (*.json)')
        if not path:
            return
        try:
            self.set_imported_measurement(load_definition(path), path)
            self._update_preview()
            errors = definition_errors(self.imported_measurement)
            self.status.setText('Messschale importiert.' + (' ' + ' | '.join(errors) if errors else ''))
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.status.setText(f'Messschale nicht importiert: {error}')

    def measurement_reference_changed(self):
        reference = self.measurement_reference.currentData()
        if self.imported_measurement is not None and reference:
            self.imported_measurement = with_reference(self.imported_measurement, *reference)
            self._parameters_changed()

    def measurement_sensor_changed(self):
        reference = self.measurement_sensor.currentData()
        if self.imported_measurement is not None and reference:
            self.imported_measurement = with_reference(self.imported_measurement, *reference, role='sensor')
            self._parameters_changed()

    def measurement_import_settings(self):
        return dict(mode=self.measurement_source.currentData(), source=self.measurement_import_path,
                    definition=copy.deepcopy(self.imported_measurement))

    def restore_measurement_import(self, settings):
        if not settings:
            return
        if settings.get('mode') not in ('legacy', 'imported'):
            raise ValueError('Unbekannter Messschalen-Modus.')
        definition = settings.get('definition')
        if definition is not None:
            self.set_imported_measurement(definition, settings.get('source', ''), activate=False)
        self.measurement_source.setCurrentIndex(self.measurement_source.findData(settings['mode']))
        self.measurement_source_changed()
