from PyQt6.QtCore import Qt, QRectF, QSize
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QCheckBox

from clUIScaler import UIScaler

BASE_PX = 18
SUPERSAMPLE = 8


class RingCheckBox(QCheckBox):
    """Round checkbox: a thin orange ring when open, a filled disc when checked.

    Hand-painted because Qt's stylesheet `border-radius` draws small circles from coarse arc
    segments (lopsided, visibly faceted at ~18 px). Each state is rendered once at 8x and
    box-filtered down, which gives an even, symmetric edge at any scale, then cached.
    """
    _cache = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _diameter(self):
        return UIScaler.get().scale(BASE_PX)

    def sizeHint(self):
        d = self._diameter()
        return QSize(d, d)

    def minimumSizeHint(self):
        return self.sizeHint()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    @classmethod
    def _render(cls, diameter, dpr, checked, hover):
        key = (diameter, dpr, checked, hover)
        if key in cls._cache:
            return cls._cache[key]
        px = max(1, round(diameter * dpr))
        big = px * SUPERSAMPLE
        img = QImage(big, big, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = SUPERSAMPLE * dpr  # 1 logical px
        rect = QRectF(line / 2, line / 2, big - line, big - line)
        if checked:
            grad = QLinearGradient(0, 0, big, big)
            grad.setColorAt(0, QColor("#ff8c00"))
            grad.setColorAt(1, QColor("#e65c00"))
            p.setBrush(grad)
            p.setPen(QPen(QColor("#ffbb33"), line))
        else:
            p.setBrush(QColor(255, 170, 0, 64) if hover else Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ffaa00"), line))
        p.drawEllipse(rect)
        p.end()
        out = img.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        out.setDevicePixelRatio(dpr)
        cls._cache[key] = out
        return out

    def paintEvent(self, event):
        d = self._diameter()
        dpr = self.devicePixelRatioF()
        img = self._render(d, dpr, self.isChecked(), self.underMouse())
        p = QPainter(self)
        p.drawImage((self.width() - d) // 2, (self.height() - d) // 2, img)
