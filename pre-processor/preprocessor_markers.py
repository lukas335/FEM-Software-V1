"""Small point glyphs in screen pixels, independent of the CAD view scale."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem


def point_marker(scene, x, y, radius=2.0, color='#1155cc', fill='#ffffff', z=20):
    item = QGraphicsEllipseItem(-radius, -radius, 2 * radius, 2 * radius)
    item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
    item.setPos(x, -y)
    pen = QPen(QColor(color), 0.8)
    pen.setCosmetic(True)
    item.setPen(pen)
    item.setBrush(QBrush(QColor(fill)))
    item.setZValue(z)
    scene.addItem(item)
    return item


def center_marker(scene, x, y):
    path = QPainterPath()
    path.moveTo(-2, 0); path.lineTo(2, 0)
    path.moveTo(0, -2); path.lineTo(0, 2)
    item = QGraphicsPathItem(path)
    item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
    item.setPos(x, -y)
    pen = QPen(QColor('#4b78a8'), 0.8); pen.setCosmetic(True)
    item.setPen(pen); item.setZValue(19); scene.addItem(item)
    return item
