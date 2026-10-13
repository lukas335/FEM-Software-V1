"""Qt application entry point for the measurement-shell pre-processor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from preprocessor_demo import demo_model
from preprocessor_history import CommandStack
from preprocessor_model import MeasurementShellSketch
from preprocessor_region_view import RegionView
from preprocessor_view import SketchView
from preprocessor_window_actions import WindowActionsMixin
from preprocessor_window_ui import WindowUiMixin
from preprocessor_reference_ui import MeasurementReferenceMixin
from preprocessor_file_actions import FileActionsMixin
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMainWindow


class Window(WindowUiMixin, FileActionsMixin, MeasurementReferenceMixin, WindowActionsMixin, QMainWindow):
    pass


def main():
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError:
        print("PySide6 ist nicht installiert; JSON-Demo wird validiert.")
        sketch = demo_model()
        print(json.dumps({"valid": not sketch.validate(), "errors": sketch.validate(),
                          "variants": sum(1 for _ in sketch.study_variants())}, indent=2))
        return
    document = MeasurementShellSketch()
    history = CommandStack(document)
    app = QApplication(sys.argv)
    window = Window(document, history)
    window.show()
    app.processEvents()
    window.fit_view()
    QTimer.singleShot(100, window.fit_view)
    sys.exit(app.exec())
