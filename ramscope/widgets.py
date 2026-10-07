"""The RamScope icon, drawn in code so no image files are needed."""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap


def draw_chip(p: QPainter, size: int) -> None:
    """A RAM stick with golden contacts."""
    s = size / 64
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#d9a520"))
    for i in range(9):
        p.drawRect(QRectF((8 + i * 5.5) * s, 44 * s, 3 * s, 9 * s))
    p.setBrush(QColor("#1b6e3a"))
    p.drawRoundedRect(QRectF(3 * s, 12 * s, 58 * s, 34 * s), 4 * s, 4 * s)
    p.setBrush(QColor("#222"))
    for i in range(4):
        p.drawRect(QRectF((8 + i * 12.5) * s, 18 * s, 9.5 * s, 20 * s))


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        draw_chip(p, size)
        p.end()
        icon.addPixmap(pm)
    return icon
