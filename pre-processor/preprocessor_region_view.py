from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsView

class RegionView(QGraphicsView):
    """Region canvas with direct contour-edge selection."""
    def __init__(self, window):
        super().__init__(); self.window = window; self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.window.reference_pick_active:
                self.window.pick_measurement_point(event.position().toPoint())
                event.accept()
                return
            # Reference glyphs/labels must not mask selectable contour edges.
            boundary = next((item.data(1) for item in self.items(event.position().toPoint())
                             if item.data(1) is not None), None)
            if boundary is not None:
                self.window.select_boundary(*boundary)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.window.reference_pick_active:
            self.window.reference_pick_button.setChecked(False)
            self.window.refresh_reference_ui()
            event.accept()
            return
        super().keyPressEvent(event)
