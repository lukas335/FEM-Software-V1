"""Save reuses the active file; Save As switches only after a successful write."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from measurement_shell_prototype import CommandStack, MeasurementShellSketch, Window
from preprocessor_file_actions import write_sketch


class EditorFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.document = MeasurementShellSketch()
        self.history = CommandStack(self.document)
        self.window = Window(self.document, self.history)

    def tearDown(self):
        self.window.close(); self.app.processEvents(); self.temporary.cleanup()

    def save_dialog(self, path):
        return patch('preprocessor_file_actions.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON'))

    def open_dialog(self, path):
        return patch('preprocessor_file_actions.QFileDialog.getOpenFileName', return_value=(str(path), 'JSON'))

    def test_new_document_save_prompts_once_and_then_overwrites_same_file(self):
        path = self.directory / 'sketch.json'
        self.assertIsNone(self.window.document_path)
        with self.save_dialog(path) as dialog:
            self.assertTrue(self.window.save())
        dialog.assert_called_once()
        self.assertEqual(self.window.document_path, path.resolve())
        self.document.add_point(3, 2)
        with patch('preprocessor_file_actions.QFileDialog.getSaveFileName') as dialog:
            self.assertTrue(self.window.save())
        dialog.assert_not_called()
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), self.document.data)
        self.assertIn(path.name, self.window.windowTitle())

    def test_opened_file_save_does_not_prompt_and_save_as_switches_target(self):
        original = self.directory / 'original.json'
        write_sketch(original, self.document.data)
        with self.open_dialog(original):
            self.assertTrue(self.window.load())
        self.document.add_point(2, 1)
        with patch('preprocessor_file_actions.QFileDialog.getSaveFileName') as dialog:
            self.assertTrue(self.window.save())
        dialog.assert_not_called()
        old_bytes = original.read_bytes()
        other = self.directory / 'copy.json'
        with self.save_dialog(other):
            self.assertTrue(self.window.save_as())
        self.document.add_point(5, 2)
        self.assertTrue(self.window.save())
        self.assertEqual(original.read_bytes(), old_bytes)
        self.assertEqual(json.loads(other.read_text(encoding='utf-8')), self.document.data)
        self.assertEqual(self.window.document_path, other.resolve())

    def test_cancel_save_as_or_open_keeps_document_target_and_history(self):
        path = self.directory / 'sketch.json'
        with self.save_dialog(path): self.window.save()
        self.history.run('Punkt', lambda: self.document.add_point(1, 2))
        before = self.document.clone_data()
        with self.save_dialog(''):
            self.assertFalse(self.window.save_as())
        with self.open_dialog(''):
            self.assertFalse(self.window.load())
        self.assertEqual(self.document.data, before)
        self.assertEqual(self.window.document_path, path.resolve())
        self.assertEqual(len(self.history.undo_stack), 1)

    def test_failed_write_preserves_original_and_active_filename(self):
        original = self.directory / 'original.json'
        with self.save_dialog(original): self.window.save()
        original_bytes = original.read_bytes()
        self.document.add_point(2, 1)
        with patch('preprocessor_file_actions.os.replace', side_effect=PermissionError('locked')):
            self.assertFalse(self.window.save())
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])
        failed = self.directory / 'missing-folder' / 'copy.json'
        with self.save_dialog(failed):
            self.assertFalse(self.window.save_as())
        self.assertEqual(self.window.document_path, original.resolve())
        self.assertIn('Nicht gespeichert', self.window.status.text())

    def test_failed_open_keeps_old_path_and_does_not_clear_history(self):
        original = self.directory / 'original.json'
        with self.save_dialog(original): self.window.save()
        self.history.run('Punkt', lambda: self.document.add_point(2, 1))
        before = self.document.clone_data()
        bad = self.directory / 'bad.json'; bad.write_text('{', encoding='utf-8')
        for path in (bad, self.directory / 'missing.json'):
            with self.open_dialog(path):
                self.assertFalse(self.window.load())
            self.assertEqual(self.document.data, before)
            self.assertEqual(self.window.document_path, original.resolve())
            self.assertEqual(len(self.history.undo_stack), 1)

    def test_successful_open_clears_history_but_save_does_not(self):
        path = self.directory / 'sketch.json'
        self.history.run('Punkt', lambda: self.document.add_point(1, 1))
        with self.save_dialog(path): self.window.save()
        self.assertEqual(len(self.history.undo_stack), 1)
        self.history.undo()
        with self.open_dialog(path): self.window.load()
        self.assertEqual(len(self.history.undo_stack), 0)
        self.assertEqual(len(self.history.redo_stack), 0)
        self.assertEqual(len(self.document.points), 1)

    def test_ctrl_s_and_ctrl_shift_s_work_in_both_tabs(self):
        path = self.directory / 'sketch.json'
        self.window.show(); self.window.activateWindow(); self.app.processEvents()
        with self.save_dialog(path) as dialog:
            QTest.keyClick(self.window, Qt.Key_S, Qt.ControlModifier)
            self.app.processEvents()
        dialog.assert_called_once()
        for tab in (0, 1):
            self.window.tabs.setCurrentIndex(tab); self.app.processEvents()
            self.document.add_point(tab + 2, 1)
            with patch('preprocessor_file_actions.QFileDialog.getSaveFileName') as dialog:
                QTest.keyClick(self.window, Qt.Key_S, Qt.ControlModifier)
                self.app.processEvents()
            dialog.assert_not_called()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), self.document.data)
        other = self.directory / 'copy.json'
        with self.save_dialog(other) as dialog:
            QTest.keyClick(self.window, Qt.Key_S, Qt.ControlModifier | Qt.ShiftModifier)
            self.app.processEvents()
        dialog.assert_called_once()
        self.assertEqual(self.window.document_path, other.resolve())


if __name__ == '__main__':
    unittest.main()
