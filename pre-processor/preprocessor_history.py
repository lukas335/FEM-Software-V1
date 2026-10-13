from __future__ import annotations

from typing import Callable

class CommandStack:
    """Snapshot-based history for reliable undo/redo in the prototype."""
    def __init__(self, document): self.document, self.undo_stack, self.redo_stack = document, [], []
    def run(self, label: str, operation: Callable[[], None]):
        before = self.document.clone_data()
        try:
            operation()
            if self.document.regions or self.document.interfaces:
                try:
                    self.document.ensure_regions_current()
                except ValueError:
                    # Keep the previous assignment for later reconciliation.
                    # Validation/export reject it and the view never fills an
                    # open or missing contour while a sketch is being edited.
                    pass
        except Exception:
            self.document.replace_data(before)
            raise
        after = self.document.clone_data()
        if before != after: self.undo_stack.append((label, before, after)); self.redo_stack.clear()
    def undo(self):
        if self.undo_stack:
            label, before, after = self.undo_stack.pop(); self.document.replace_data(before); self.redo_stack.append((label, before, after))
    def redo(self):
        if self.redo_stack:
            label, before, after = self.redo_stack.pop(); self.document.replace_data(after); self.undo_stack.append((label, before, after))
