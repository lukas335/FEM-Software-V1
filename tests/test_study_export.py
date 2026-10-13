"""Whole-study tables, pressure baselines, locale-safe CSV and read-only GUI export."""
import copy
import csv
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from model.study import ReferenceGeometry, StudyConfig
from model.study_metrics import geometry_key, stiffness_groups
from model.study_export import StudyTable, study_table, table_text
from model.study_results import rank_geometries
from model.study_storage import StudyStorage
from solver.prototype import baseline


def example():
    config = StudyConfig(baseline(), (8., 10.), (1.,), (5., 15., 35.),
                         (ReferenceGeometry('Referenz ä', 9., 1.),), fit_mode='manual', pressure_mode='closed')
    cases = config.cases()
    for case in cases:
        p = case['parameters']['pressure_mmhg']
        k = 2 * (1 + (p - 5) / 30) * 8 / geometry_key(case)[0]
        case.update(status='success', result=dict(stiffness=k, warnings=[], r_squared=None,
                    delta_force_n=.2 * k, delta_displacement_mm=.2, contact_mm=.1,
                    fit_start_delta_mm=.01, fit_end_delta_mm=.21,
                    achieved_drive_mm=.5 - p / 200))
    return config, cases


class StudyExportMetricsTests(unittest.TestCase):
    def setUp(self):
        self.config, self.cases = example()

    def case(self, p, radius=8):
        return next(c for c in self.cases if geometry_key(c)[0] == radius and c['parameters']['pressure_mmhg'] == p)

    def test_summary_relative_stiffness_is_percent_not_end_drive_difference(self):
        before = copy.deepcopy(self.cases)
        groups = stiffness_groups(self.cases, self.config)
        self.assertAlmostEqual(groups[(8, 1, 1)]['relative_stiffness_change_pct'], 100)
        self.assertEqual(groups[(8, 1, 1)]['reference_pressure_mmhg'], 5)
        self.assertEqual(groups[(8, 1, 1)]['comparison_pressure_mmhg'], 35)
        row = next(r for r in rank_geometries(self.cases, self.config) if r['radius_mm'] == 8)
        self.assertAlmostEqual(row['relative_stiffness_change_pct'], 100)
        self.assertAlmostEqual(row['delta_end_drive_mm'], -.15)
        table = study_table(self.cases, self.config)
        self.assertEqual(len(table.rows), 3)
        self.assertIn('Relative Steifigkeitsänderung', table.headers[0])
        self.assertTrue(all(len(row) == len(table.headers) for row in table.rows))
        self.assertEqual(self.cases, before)

    def test_all_cases_including_reference_geometry_and_pressure_specific_changes(self):
        table = study_table(self.cases, self.config, 'cases')
        self.assertEqual(len(table.rows), len(self.cases))
        first = next(row for row in table.rows if row[1] == 8 and row[4] == 5)
        middle = next(row for row in table.rows if row[1] == 8 and row[4] == 15)
        self.assertEqual(first[0], 0)
        self.assertAlmostEqual(middle[0], 100 / 3)
        self.assertTrue(any(row[table.headers.index('Referenzgeometrie')] == 'Referenz ä' for row in table.rows))

    def test_failed_baseline_is_not_replaced_by_next_successful_pressure(self):
        self.case(5).update(status='failed', error='Solver abgebrochen')
        group = stiffness_groups(self.cases, self.config)[(8, 1, 1)]
        self.assertIsNone(group['reference_stiffness'])
        self.assertIsNone(group['relative_stiffness_change_pct'])
        self.assertEqual(group['reference_pressure_mmhg'], 5)
        table = study_table(self.cases, self.config, 'cases')
        self.assertTrue(all(row[0] is None for row in table.rows if row[1] == 8))
        self.assertIn('Basisdruck 5', group['notes'])

    def test_missing_end_pressure_does_not_change_comparison_pressure(self):
        self.case(35).update(status='pending', result=None)
        group = stiffness_groups(self.cases, self.config)[(8, 1, 1)]
        self.assertEqual(group['comparison_pressure_mmhg'], 35)
        self.assertIsNone(group['relative_stiffness_change_pct'])
        self.assertTrue(group['provisional'])
        self.assertIn('Vorläufig', group['status'])

    def test_missing_middle_pressure_keeps_valid_endpoint_comparison_but_marks_incomplete(self):
        self.case(15).update(status='invalid', result=None)
        group = stiffness_groups(self.cases, self.config)[(8, 1, 1)]
        self.assertAlmostEqual(group['relative_stiffness_change_pct'], 100)
        self.assertFalse(group['complete'])
        self.assertIn('2/3', group['status'])
        table = study_table(self.cases, self.config)
        row = next(row for row in table.rows if row[1] == 8)
        self.assertIsNone(row[table.headers.index('k bei 15 mmHg [N/mm]')])

    def test_zero_negative_nonfinite_or_ambiguous_baseline_remains_unavailable(self):
        for bad in (0., -2., float('nan'), float('inf')):
            self.case(5)['result']['stiffness'] = bad
            self.assertIsNone(stiffness_groups(self.cases, self.config)[(8, 1, 1)]['relative_stiffness_change_pct'])
        duplicate = copy.deepcopy(self.case(5)); duplicate['id'] = 'duplicate'
        self.cases.append(duplicate)
        self.assertIsNone(stiffness_groups(self.cases, self.config)[(8, 1, 1)]['reference_stiffness'])

    def test_zero_change_decreasing_stiffness_and_single_pressure_are_not_missing(self):
        for end, expected in ((2., 0.), (1., -50.)):
            self.case(35)['result']['stiffness'] = end
            self.assertEqual(stiffness_groups(self.cases, self.config)[(8, 1, 1)]['relative_stiffness_change_pct'], expected)
        config = replace(self.config, pressures_mmhg=(5.,))
        cases = [c for c in self.cases if c['parameters']['pressure_mmhg'] == 5]
        self.assertTrue(all(row[0] == 0 for row in study_table(cases, config).rows))
        self.assertTrue(all('nur ein Druckwert' in g['status'] for g in stiffness_groups(cases, config).values()))

    def test_csv_and_tsv_preserve_rows_quotes_unicode_and_numeric_precision(self):
        table = StudyTable(['Kennzahl [%]', 'Text', 'Fehlt'], [[-12.345678901234, 'ä; "Text"\nZeile', None], [0., '=2+2', True]])
        for delimiter, comma in ((';', True), (',', False), ('\t', True)):
            parsed = list(csv.reader(io.StringIO(table_text(table, delimiter=delimiter, decimal_comma=comma)), delimiter=delimiter))
            self.assertEqual(parsed[0], table.headers)
            self.assertAlmostEqual(float(parsed[1][0].replace(',', '.')), table.rows[0][0])
            self.assertEqual(parsed[1][1], table.rows[0][1])
            self.assertEqual(parsed[1][2], '')
            self.assertEqual(parsed[2][1], "'=2+2")
            self.assertEqual(parsed[2][2], 'Ja')

    def test_machine_mirror_adds_unambiguous_metric_without_changing_study_snapshots(self):
        with TemporaryDirectory() as directory:
            storage = StudyStorage.create(Path(directory), self.config)
            storage.cases = self.cases
            original = (storage.directory / 'study.json').read_bytes()
            storage.save()
            with (storage.directory / 'ranking.csv').open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(all(float(row['relative_stiffness_change_pct']) == 100 for row in rows))
            self.assertEqual((storage.directory / 'study.json').read_bytes(), original)


class StudyExportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.main_window import MainWindow
        self.temporary = TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.window = MainWindow(self.directory)
        self.panel = self.window.study
        self.config, self.cases = example()
        self.store = StudyStorage.create(self.directory / 'studies', self.config)
        self.store.cases = self.cases
        self.panel.storage = self.store
        self.panel.refresh_results(); self.panel.update_controls()

    def tearDown(self):
        self.window.settings_timer.stop(); self.window.close(); self.app.processEvents()
        self.temporary.cleanup()

    def test_export_all_geometry_rows_ignores_selection_and_preserves_source(self):
        self.panel.ranking.clearSelection(); self.panel.ranking.selectRow(0)
        before = copy.deepcopy(self.store.cases)
        source = {p.name: p.read_bytes() for p in self.store.directory.iterdir() if p.is_file()}
        path = self.directory / 'export.csv'
        with patch('app.study_export.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV für Excel (deutsch) (*.csv)')):
            self.assertTrue(self.panel.export_study_table())
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';'))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(float(row[0].replace(',', '.')) == 100 for row in rows[1:]))
        self.assertEqual(self.store.cases, before)
        self.assertEqual(source, {p.name: p.read_bytes() for p in self.store.directory.iterdir() if p.is_file()})

    def test_all_case_export_includes_failed_and_running_and_uses_international_numbers(self):
        self.cases[0].update(status='failed', error='Fehler ä')
        self.cases[1].update(status='running', result=None)
        self.panel.export_kind.setCurrentIndex(1)
        path = self.directory / 'cases.csv'
        with patch('app.study_export.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV international (*.csv)')):
            self.assertTrue(self.panel.export_study_table())
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), len(self.cases))
        self.assertIn('failed', {r['Status'] for r in rows})
        self.assertIn('running', {r['Status'] for r in rows})
        self.assertTrue(any('.' in r['k [N/mm]'] for r in rows))

    def test_clipboard_has_whole_table_and_disabled_without_study(self):
        self.assertTrue(self.panel.copy_study_table())
        rows = list(csv.reader(io.StringIO(self.app.clipboard().text()), delimiter='\t'))
        self.assertEqual(len(rows), 4)
        self.panel.new_study()
        self.assertFalse(self.panel.export_button.isEnabled())
        self.assertFalse(self.panel.export_copy_button.isEnabled())
        self.assertFalse(self.panel.copy_study_table())

    def test_cancel_or_locked_export_preserves_existing_destination(self):
        with patch('app.study_export.QFileDialog.getSaveFileName', return_value=('', '')):
            self.assertFalse(self.panel.export_study_table())
        path = self.directory / 'export.csv'; path.write_text('original', encoding='utf-8')
        with patch('app.study_export.QFileDialog.getSaveFileName', return_value=(str(path), '')), \
             patch('app.study_export.atomic_text', side_effect=PermissionError('locked')):
            self.assertFalse(self.panel.export_study_table())
        self.assertEqual(path.read_text(encoding='utf-8'), 'original')
        self.assertIn('nicht exportiert', self.panel.status.text())

    def test_relative_column_is_prominent_and_numeric_sorted(self):
        self.assertEqual(self.panel.ranking.horizontalHeaderItem(3).text(), 'Δk/k_ref [%]')
        for row in range(self.panel.ranking.rowCount()):
            self.assertEqual(self.panel.ranking.item(row, 3).text(), '100')
            self.assertTrue(self.panel.ranking.item(row, 3).font().bold())


if __name__ == '__main__':
    unittest.main()
