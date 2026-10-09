"""Small controls for the dashboard drawer (calendar + day sheet + dock + media pill).

Anything round and small is painted here with QPainter antialiasing, never with a stylesheet
`border-radius` (Qt draws those badly; see docs/context/ui.md).
"""
import logging

from PyQt6.QtCore import Qt, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from clTheme import Theme
from clUIScaler import UIScaler
from utils.clActionRouter import ActionRouter


def s(val):
    return UIScaler.get().scale(val)


CREAM = QColor("#ffe6cc")
ORANGE = QColor("#ffaa00")
INK = QColor("#180a02")


def letter_spaced(widget, px):
    """Absolute letter spacing; stylesheets can't do it."""
    font = widget.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px)
    widget.setFont(font)


class RoundButton(QPushButton):
    """Circle button painted by hand.

    kind: "dock"    idle tint circle, orange when selected (optional count badge)
          "add"     orange gradient circle (the + button)
          "solid"   flat orange circle (play)
          "ghost"   icon only, a faint circle on hover (prev, chevrons)
    The widget is `diameter` + a small margin all round so a badge can sit on the edge.
    """
    MARGIN = 3

    def __init__(self, diameter, icon_name, icon_px, kind="dock", ink=None, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._ink_override = QColor(ink) if ink else None
        self._diameter = diameter
        self._icon_name = icon_name
        self._icon_px = icon_px
        self._selected = False
        self._badge = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        # The global QPushButton sheet draws a square border and fill; the circle is painted here.
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")
        total = diameter + 2 * self.MARGIN
        self.setFixedSize(total, total)

    def set_icon_name(self, name):
        self._icon_name = name
        self.update()

    def set_selected(self, selected):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def set_badge(self, count):
        count = max(0, int(count))
        if self._badge != count:
            self._badge = count
            self.update()

    def badge(self):
        return self._badge

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def _ink(self):
        if self._ink_override is not None:
            return self._ink_override
        return INK if (self._kind in ("add", "solid") or (self._kind == "dock" and self._selected)) else CREAM

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = s(self.MARGIN)
        circle = QRectF(self.rect()).adjusted(m, m, -m, -m)
        p.setPen(Qt.PenStyle.NoPen)
        if self._kind == "add":
            grad = QLinearGradient(circle.topLeft(), circle.bottomRight())
            grad.setColorAt(0, QColor("#ffb92e"))
            grad.setColorAt(1, QColor("#ff8c00"))
            p.setBrush(grad)
        elif self._kind == "solid" or (self._kind == "dock" and self._selected):
            p.setBrush(ORANGE)
        elif self._kind == "ghost":
            p.setBrush(QColor(255, 150, 0, 40) if self.underMouse() else Qt.BrushStyle.NoBrush)
        else:
            p.setBrush(QColor(255, 150, 0, 56 if self.underMouse() else 33))
        p.drawEllipse(circle)

        if self._icon_name:
            px = s(self._icon_px)
            pix = Theme.get_icon(self._icon_name, px, self._ink().name()).pixmap(QSize(px, px))
            p.drawPixmap(int(circle.center().x() - px / 2), int(circle.center().y() - px / 2), pix)

        if self._badge:
            self._paint_badge(p)
        p.end()

    def _paint_badge(self, p):
        font = QFont(self.font())
        font.setPixelSize(s(10))
        font.setWeight(QFont.Weight.ExtraBold)
        p.setFont(font)
        text = str(min(self._badge, 99))
        h = s(16)
        w = max(h, QFontMetrics(font).horizontalAdvance(text) + s(9))
        rect = QRectF(self.width() - w, 0, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ORANGE)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(INK)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class SegmentedControl(QFrame):
    """Pill with one active segment (DAY / WEEK / MONTH)."""
    changed = pyqtSignal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setObjectName("SegmentPill")
        self.setStyleSheet(Theme.get_style("SegmentPill"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(s(3), s(3), s(3), s(3))
        layout.setSpacing(s(3))
        self._buttons = {}
        for key, label in items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(Theme.get_style("SegmentButton"))
            letter_spaced(btn, 1)
            btn.clicked.connect(lambda _=False, k=key: self._clicked(k))
            layout.addWidget(btn, 1)
            self._buttons[key] = btn

    def _clicked(self, key):
        self.set_value(key)
        self.changed.emit(key)

    def set_value(self, key):
        """key=None leaves every segment off (e.g. the year picker)."""
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)

    def value(self):
        return next((k for k, b in self._buttons.items() if b.isChecked()), None)


class EventDot(QWidget):
    """8 px event marker: solid for a local event, a hollow ring for a Google one."""

    def __init__(self, hollow=False, parent=None):
        super().__init__(parent)
        self._hollow = hollow
        self.setFixedSize(8, 8)

    def hollow(self):
        return self._hollow

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 1.5 * UIScaler.get().scale_float(1)
        rect = QRectF(self.rect()).adjusted(w / 2, w / 2, -w / 2, -w / 2)
        p.setPen(QPen(CREAM, w))
        p.setBrush(Qt.BrushStyle.NoBrush if self._hollow else CREAM)
        p.drawEllipse(rect)
        p.end()


class DayBars(QWidget):
    """Up to three little bars under a day number: filled = local, outlined = Google."""
    MAX = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = []
        self._ink = QColor(255, 230, 204, 191)
        self.setFixedSize(16, 7)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_bars(self, hollow_flags, selected=False):
        self._bars = list(hollow_flags)[: self.MAX]
        self._ink = INK if selected else QColor(255, 230, 204, 191)
        self.update()

    def bars(self):
        return list(self._bars)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, gap = s(2), s(2)
        for i, hollow in enumerate(self._bars):
            r = QRectF(0, i * (h + gap), self.width(), h)
            if hollow:
                p.setPen(QPen(self._ink, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._ink)
                p.drawRoundedRect(r, h / 2, h / 2)
        p.end()


class DragHandle(QWidget):
    """Decorative grab bar at the top of the day sheet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 4)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 170, 0, 102))
        p.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        p.end()


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of widening its layout: it asks for the
    width of its full text but can shrink to nothing."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def sizeHint(self):
        self.ensurePolished()   # the stylesheet's font only applies once polished
        hint = super().sizeHint()
        fm = QFontMetrics(self.font())
        # QLabel's own hint carries about one average character of padding; keep it so the text isn't clipped.
        hint.setWidth(fm.horizontalAdvance(self._full) + fm.averageCharWidth())
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def setText(self, text):
        self._full = text
        self._elide()

    def fullText(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        self.ensurePolished()
        width = max(0, self.width())
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, width) if width else self._full
        QLabel.setText(self, shown)


class CoverDisc(QWidget):
    """Round cover-art stand-in (the player status carries no artwork)."""

    def __init__(self, diameter, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, QColor("#ff9a1f"))
        grad.setColorAt(1, QColor("#5a1f00"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(r)
        p.end()


class MediaPill(QFrame):
    """Always-visible now-playing pill: cover, title/artist, previous and play/pause."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.router = ActionRouter()
        self.status = "Paused"
        self.setObjectName("MediaPill")
        self.setStyleSheet(Theme.get_style("MediaPill"))
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(s(10), s(10), s(12), s(10))
        layout.setSpacing(s(12))

        self.cover = CoverDisc(44)
        layout.addWidget(self.cover)

        text = QVBoxLayout()
        text.setSpacing(s(2))
        text.setContentsMargins(0, 0, 0, 0)
        self.title_lbl = ElidedLabel("Nothing playing")
        self.title_lbl.setStyleSheet(Theme.get_style("MediaPillTitle"))
        self.artist_lbl = ElidedLabel("")
        self.artist_lbl.setStyleSheet(Theme.get_style("SheetDim") + " font-size: 10px;")
        text.addStretch()
        text.addWidget(self.title_lbl)
        text.addWidget(self.artist_lbl)
        text.addStretch()
        layout.addLayout(text, 1)

        self.prev_btn = RoundButton(24, "skip_back.svg", 14, kind="ghost")
        self.prev_btn.setToolTip("Previous")
        self.prev_btn.clicked.connect(lambda: self._send("prev"))
        layout.addWidget(self.prev_btn)

        self.play_btn = RoundButton(40, "play.svg", 14, kind="solid")
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self.toggle)
        layout.addWidget(self.play_btn)

    def _send(self, action):
        try:
            self.router.dispatch("spotify.control", action=action, silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def toggle(self):
        self._show_status("Paused" if self.status == "Playing" else "Playing")
        self._send("toggle")

    def _show_status(self, status):
        self.status = status
        self.play_btn.set_icon_name("pause.svg" if status == "Playing" else "play.svg")

    def update_status(self, data):
        self.title_lbl.setText(data.get("title") or "Nothing playing")
        self.artist_lbl.setText(data.get("artist") or "")
        self._show_status(data.get("status", "Paused"))
