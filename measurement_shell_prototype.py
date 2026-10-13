"""Backward-compatible launcher for the measurement-shell pre-processor.

The implementation now lives in ``pre-processor``. This thin module keeps
existing tests and the historical ``py measurement_shell_prototype.py`` command
working.
"""
from __future__ import annotations

import sys
from pathlib import Path


_PREPROCESSOR_DIR = Path(__file__).resolve().parent / "pre-processor"
if str(_PREPROCESSOR_DIR) not in sys.path:
    sys.path.insert(0, str(_PREPROCESSOR_DIR))

from preprocessor_model import MeasurementShellSketch
from preprocessor_history import CommandStack
from preprocessor_demo import demo_model
from preprocessor_app import Window, main

__all__ = ["MeasurementShellSketch", "CommandStack", "demo_model", "Window", "main"]


if __name__ == "__main__":
    main()
