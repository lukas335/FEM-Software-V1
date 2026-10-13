"""Whole-study export actions; never use the current geometry/table selection."""
from pathlib import Path
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QHBoxLayout, QPushButton
from model.study_export import study_table, table_text
from model.study_storage import atomic_text


class StudyExportMixin:
    def build_export_controls(self):
        row = QHBoxLayout()
        self.export_kind = QComboBox()
        self.export_kind.addItem('Geometrieübersicht (gesamte Studie)', 'summary')
        self.export_kind.addItem('Alle Einzelrechnungen (gesamte Studie)', 'cases')
        self.export_button = QPushButton('Studientabelle exportieren …')
        self.export_copy_button = QPushButton('Tabelle kopieren')
        self.export_button.clicked.connect(self.export_study_table)
        self.export_copy_button.clicked.connect(self.copy_study_table)
        for widget in (self.export_kind, self.export_button, self.export_copy_button):
            widget.setToolTip('Alle Fälle der geladenen Studie, unabhängig von Auswahl und Sortierung. Δk/k_ref = (k − k_ref) / k_ref; Basis ist der kleinste geplante Studiendruck.')
            row.addWidget(widget)
        row.addStretch()
        return row

    def export_study_table(self):
        if self.storage is None:
            return False
        kind = self.export_kind.currentData()
        default = self.storage.directory / ('studienuebersicht.csv' if kind == 'summary' else 'studienfaelle.csv')
        path, format_name = QFileDialog.getSaveFileName(self, 'Gesamte Studie als Tabelle exportieren',
                str(default), 'CSV für Excel (deutsch) (*.csv);;CSV international (*.csv)')
        if not path:
            return False
        if Path(path).suffix.lower() != '.csv':
            self.status.setText('Bitte einen Dateinamen mit .csv wählen.')
            return False
        try:
            table = study_table(self.storage.cases, self.storage.config, kind)
            german = not format_name.startswith('CSV international')
            text = table_text(table, delimiter=';' if german else ',', decimal_comma=german)
            atomic_text(Path(path), '\ufeff' + text)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.status.setText(f'Tabelle nicht exportiert: {error}')
            return False
        self.status.setText(f'Gesamte Studie exportiert: {len(table.rows)} Zeilen · {path}')
        return True

    def copy_study_table(self):
        if self.storage is None:
            return False
        try:
            table = study_table(self.storage.cases, self.storage.config, self.export_kind.currentData())
            QApplication.clipboard().setText(table_text(table, delimiter='\t'))
        except (ValueError, TypeError, KeyError) as error:
            self.status.setText(f'Tabelle nicht kopiert: {error}')
            return False
        self.status.setText(f'{len(table.rows)} Zeilen der gesamten Studie kopiert – direkt in Excel einfügen.')
        return True
