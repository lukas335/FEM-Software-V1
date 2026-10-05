"""Compact, opt-in coefficient editor for uncalibrated Mooney–Rivlin cards."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QDoubleSpinBox, QToolButton

from model.materials import MATERIALS


class MaterialCoefficients(QWidget):
    def __init__(self, selector):
        super().__init__()
        self.selector = selector
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QToolButton()
        button.setText("MR-Koeffizienten (MPa) ▸")
        button.setCheckable(True)
        button.setToolTip("Illustrative Startwerte, nicht für biologisches Gewebe kalibriert. μ = 2(c1+c2).")
        layout.addWidget(button)
        self.fields = QWidget()
        form = QFormLayout(self.fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.boxes = []
        for label in ("c1", "c2", "K"):
            box = QDoubleSpinBox()
            box.setDecimals(6)
            box.setRange(0, 1e6)
            box.setSingleStep(.001)
            form.addRow(label, box)
            self.boxes.append(box)
        layout.addWidget(self.fields)
        self.fields.hide()
        button.toggled.connect(self.fields.setVisible)
        selector.currentIndexChanged.connect(self.reset_material)
        self.reset_material()

    def reset_material(self):
        material = MATERIALS[self.selector.currentData()]
        supported = material.model == "mooney_rivlin"
        self.setVisible(supported)
        if supported:
            for box, value in zip(self.boxes, (material.c1_mpa, material.c2_mpa, material.bulk_modulus_mpa)):
                box.setValue(value)

    def values(self):
        if MATERIALS[self.selector.currentData()].model != "mooney_rivlin":
            return None
        return tuple(box.value() for box in self.boxes)
