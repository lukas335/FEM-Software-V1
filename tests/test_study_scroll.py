"""Regression for the study canvas/scroll-area size-hint feedback loop."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


class StudyScrollTests(unittest.TestCase):
    def test_scrolling_and_resizing_do_not_grow_study(self):
        app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        window = MainWindow()
        try:
            window.resize(1120, 780)
            window.show()
            window.tabs.setCurrentIndex(2)
            scroll = window.tabs.widget(2)
            bar = scroll.verticalScrollBar()
            for tab in (1, 0, 1):
                window.study.tabs.setCurrentIndex(tab)
                QTest.qWait(150)
                before = (window.study.height(), bar.maximum())
                for _ in range(12):
                    bar.setValue(bar.maximum())
                    QTest.qWait(20)
                    bar.setValue(0)
                    QTest.qWait(20)
                self.assertEqual((window.study.height(), bar.maximum()), before)
            hint = window.study.map_canvas.sizeHint()
            window.resize(1680, 1200)
            QTest.qWait(150)
            self.assertEqual(window.study.map_canvas.sizeHint(), hint)
            window.resize(1120, 780)
            QTest.qWait(150)
            self.assertEqual((window.study.height(), bar.maximum()), before)
            # Legitimate content expansion must still change the scroll range.
            window.study.tabs.setCurrentIndex(0)
            window.study.drive_control.mode.setCurrentIndex(1)
            QTest.qWait(100)
            collapsed = bar.maximum()
            window.study.drive_control.details.setChecked(True)
            QTest.qWait(150)
            self.assertGreater(bar.maximum(), collapsed)
            window.study.drive_control.details.setChecked(False)
            QTest.qWait(150)
            self.assertEqual(bar.maximum(), collapsed)
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
