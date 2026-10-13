"""Editor file identity, Save/Save As and safe replacement of JSON documents."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtWidgets import QFileDialog


def write_sketch(path, data):
    """Serialize first; replace the file only after writing the complete JSON."""
    content = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class FileActionsMixin:
    def update_file_title(self):
        name = self.document_path.name if self.document_path else 'Neue Skizze'
        self.setWindowTitle(f'{name} — MeasurementShell')

    def save(self):
        if self.document_path is None:
            return self.save_as()
        return self._save_to(self.document_path)

    def save_as(self):
        initial = str(self.document_path) if self.document_path else 'measurement_shell.json'
        path, _ = QFileDialog.getSaveFileName(self, 'Skizze speichern unter', initial, 'JSON (*.json)')
        if not path:
            return False
        return self._save_to(Path(path).resolve())

    def _save_to(self, path):
        try:
            write_sketch(path, self.document.data)
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(f'Nicht gespeichert: {error}')
            return False
        # Do not change the active filename on a cancelled or failed Save As.
        self.document_path = path
        self.update_file_title()
        self.status.setText(f'Gespeichert: {path}')
        return True

    def load(self):
        initial = str(self.document_path) if self.document_path else ''
        path, _ = QFileDialog.getOpenFileName(self, 'Skizze öffnen', initial, 'JSON (*.json)')
        if not path:
            return False
        try:
            candidate = Path(path).resolve()
            data = json.loads(candidate.read_text(encoding='utf-8-sig'))
            repairs = self.document.import_data(data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.status.setText(f'Nicht geladen: {error}')
            return False
        self.document_path = candidate
        self.history.undo_stack.clear()
        self.history.redo_stack.clear()
        self.refresh()
        self.update_file_title()
        self.status.setText('Geladen: ' + str(candidate) + ('\n' + '\n'.join(repairs) if repairs else ''))
        return True
