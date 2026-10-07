import math
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter
from PyQt6.QtWidgets import QWidget

from clUIScaler import UIScaler

# Defaults for core.json -> settings.clock_settings (any key may be left out).
DEFAULT_SETTINGS = {"enabled": True, "hour24": True, "seconds": True, "language": "en", "glow": 1.0}

NAMES = {
    "en": {
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    },
    "pt": {
        "days": ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"],
        "months": ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
    },
}

# Design (docs: the "Dashboard Clock" artboard), in 1080p pixels.
TOP = 258                # where the time's box starts, measured from the top of the window
TIME_PX = 84
SECONDS_PX = 26
DATE_PX = 16
RULE_WIDTH = 420
RULE_GAP = 14            # above and below the hairline
SECONDS_GAP = 16         # time -> seconds
SECONDS_LIFT = 10        # seconds sit this far above the time's bottom edge
DATE_SPACING = 0.34      # em
GLOW_MARGIN = 40         # room around the content for the glow (about 3 sigma of the widest layer)
WIDTH = 900

TIME_COLOR = QColor(255, 170, 0, 235)       # amber at 92%
DIM_COLOR = QColor(255, 170, 0, 140)        # seconds, 55%
DATE_COLOR = QColor(255, 170, 0, 158)       # 62%
RULE_COLOR = QColor(255, 170, 0, 140)
GLOW_MAX = 3.0
# (gaussian sigma px at 1080p, peak alpha, colour), scaled by settings["glow"].
# A wide soft bloom plus a tight core. Tuned against the first approved look
# (the digits repeated at a few radii): the same brightness at 6 and 14 px
# from the digits and the same reach, but a smooth falloff instead of steps.
GLOW_LAYERS = ((10, 0.26, (255, 140, 0)), (3, 0.10, (255, 170, 0)))


def load_settings(config: Optional[Dict] = None) -> Dict:
    """core.json's settings.clock_settings merged over the defaults; a value
    of the wrong type falls back to its default instead of breaking the clock."""
    merged = dict(DEFAULT_SETTINGS)
    for key, default in DEFAULT_SETTINGS.items():
        value = (config or {}).get(key, default)
        if key == "language":
            merged[key] = value if value in NAMES else default
        elif key == "glow":
            is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
            merged[key] = float(min(GLOW_MAX, max(0.0, value))) if is_number else default
        else:
            merged[key] = value if isinstance(value, bool) else default
    return merged


def format_parts(now: datetime, hour24: bool = True, language: str = "en") -> Tuple[str, str, str, str, str]:
    """(hours, minutes, seconds, am/pm marker or "", the full date line)."""
    names = NAMES.get(language, NAMES["en"])
    hour = now.hour if hour24 else (now.hour % 12 or 12)
    date = f"{names['days'][now.weekday()]} · {now.day} {names['months'][now.month - 1]} {now.year}".upper()
    return f"{hour:02d}", f"{now.minute:02d}", f"{now.second:02d}", ("" if hour24 else ("PM" if now.hour >= 12 else "AM")), date


def next_tick_ms(now: datetime, with_seconds: bool) -> int:
    """Milliseconds until the next change of the shown text: the next second
    (or minute, with seconds off), plus a few ms so the timer never fires just
    before the boundary and repaints the old value."""
    into_second = now.microsecond / 1000.0
    if with_seconds:
        return int(1000 - into_second) + 5
    return int((59 - now.second) * 1000 + (1000 - into_second)) + 5


def gaussian_blur(values: np.ndarray, sigma: float) -> np.ndarray:
    """Separable gaussian blur of a 2-D array, zero-padded at the edges (so
    a shape fully inside the array keeps its total weight)."""
    if sigma <= 0:
        return values.copy()
    radius = max(1, int(math.ceil(3 * sigma)))
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-(offsets ** 2) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    height, width = values.shape
    horizontal = np.zeros_like(values, dtype=np.float64)
    padded = np.pad(values.astype(np.float64), ((0, 0), (radius, radius)))
    for i, weight in enumerate(kernel):
        horizontal += weight * padded[:, i:i + width]
    result = np.zeros_like(horizontal)
    padded = np.pad(horizontal, ((radius, radius), (0, 0)))
    for i, weight in enumerate(kernel):
        result += weight * padded[i:i + height, :]
    return result


def tint_image(alpha: np.ndarray, color: Tuple[int, int, int], peak: float) -> QImage:
    """An ARGB32-premultiplied image of `color` whose opacity is `alpha`
    (0..1, normalised to its own maximum) scaled by `peak`."""
    top = float(alpha.max())
    coverage = np.clip(alpha / top, 0.0, 1.0) * min(1.0, peak) if top > 0 else np.zeros_like(alpha)
    height, width = coverage.shape
    bgra = np.zeros((height, width, 4), dtype=np.uint8)
    bgra[..., 0] = np.round(coverage * color[2] ).astype(np.uint8)
    bgra[..., 1] = np.round(coverage * color[1]).astype(np.uint8)
    bgra[..., 2] = np.round(coverage * color[0]).astype(np.uint8)
    bgra[..., 3] = np.round(coverage * 255).astype(np.uint8)
    return QImage(bgra.data, width, height, width * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


class ClockDisplay(QWidget):
    """The date and time, centred above the visualizer's orb (see the
    "Dashboard Clock" design). A plain child of the main window created right
    after the visualizer, so it sits above it and below every widget made
    later; painted by hand, transparent to the mouse, and only running its
    timer while it's actually shown. A local UI element: reads the system
    clock, nothing else."""

    def __init__(self, parent=None, settings: Optional[Dict] = None, now_fn: Callable[[], datetime] = datetime.now):
        super().__init__(parent)
        self._now_fn = now_fn
        self.settings = load_settings(settings)
        self._parts = format_parts(self._now_fn(), self.settings["hour24"], self.settings["language"])
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._glow_cache: Optional[Tuple[tuple, list]] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # --- sizing / placement ---
    def content_height(self) -> int:
        s = UIScaler.get().scale
        return s(TIME_PX) + s(RULE_GAP) + 1 + s(RULE_GAP) + s(DATE_PX)

    def place(self, window_width: int, window_height: int) -> None:
        """Centred horizontally; the time's box starts TOP px down (scaled)."""
        s = UIScaler.get().scale
        width = s(WIDTH)
        margin = s(GLOW_MARGIN)
        self.setGeometry(window_width // 2 - width // 2, s(TOP) - margin, width, self.content_height() + 2 * margin)

    # --- visibility: ticking only while shown ---
    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()
        self._schedule()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def show_if_enabled(self) -> None:
        if self.settings["enabled"]:
            self.show()

    def _schedule(self) -> None:
        self._timer.start(next_tick_ms(self._now_fn(), self.settings["seconds"]))

    def _refresh(self) -> None:
        parts = format_parts(self._now_fn(), self.settings["hour24"], self.settings["language"])
        shown = parts if self.settings["seconds"] else parts[:2] + ("",) + parts[3:]
        previous = self._parts if self.settings["seconds"] else self._parts[:2] + ("",) + self._parts[3:]
        self._parts = parts
        if shown != previous:
            self.update()

    def _tick(self) -> None:
        self._refresh()
        self._schedule()

    # --- painting ---
    @staticmethod
    def _font(pixels: int, spacing_em: float = 0.0) -> QFont:
        font = QFont()
        font.setFamilies(["DejaVu Sans Mono", "Courier New", "monospace"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(pixels)
        if spacing_em:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, pixels * spacing_em)
        return font

    @staticmethod
    def _baseline(box_top: float, box_height: float, metrics: QFontMetrics) -> float:
        """Baseline of text laid out like CSS line-height: 1 inside the box."""
        return box_top + (box_height - (metrics.ascent() + metrics.descent())) / 2 + metrics.ascent()

    def paintEvent(self, event):
        s = UIScaler.get().scale
        hours, minutes, seconds, marker, date = self._parts
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        width = self.width()
        margin = s(GLOW_MARGIN)
        time_box_h = s(TIME_PX)

        time_font = self._font(s(TIME_PX))
        tm = QFontMetrics(time_font)
        time_text = f"{hours}:{minutes}"   # the colon is steady: it never blinks
        time_w = tm.horizontalAdvance(time_text)
        time_x = (width - time_w) / 2
        time_baseline = self._baseline(margin, time_box_h, tm)

        self._draw_glow(painter, tm, time_font, time_x, time_text)
        painter.setFont(time_font)
        painter.setPen(TIME_COLOR)
        painter.drawText(int(round(time_x)), int(round(time_baseline)), time_text)

        if self.settings["seconds"]:
            sec_font = self._font(s(SECONDS_PX))
            sm = QFontMetrics(sec_font)
            sec_box_bottom = margin + time_box_h - s(SECONDS_LIFT)
            sec_baseline = sec_box_bottom - (s(SECONDS_PX) - (sm.ascent() + sm.descent())) / 2 - sm.descent()
            painter.setFont(sec_font)
            painter.setPen(DIM_COLOR)
            painter.drawText(int(round(time_x + time_w + s(SECONDS_GAP))), int(round(sec_baseline)), seconds)

        if marker:
            marker_font = self._font(s(SECONDS_PX), 0.12)
            mm = QFontMetrics(marker_font)
            marker_w = mm.horizontalAdvance(marker)
            sec_box_bottom = margin + time_box_h - s(SECONDS_LIFT)
            marker_baseline = sec_box_bottom - (s(SECONDS_PX) - (mm.ascent() + mm.descent())) / 2 - mm.descent()
            painter.setFont(marker_font)
            painter.setPen(DIM_COLOR)
            painter.drawText(int(round(time_x - s(SECONDS_GAP) - marker_w)), int(round(marker_baseline)), marker)

        rule_y = margin + time_box_h + s(RULE_GAP)
        rule_w = s(RULE_WIDTH)
        gradient = QLinearGradient((width - rule_w) / 2, 0, (width + rule_w) / 2, 0)
        faded = QColor(RULE_COLOR.red(), RULE_COLOR.green(), RULE_COLOR.blue(), 0)
        gradient.setColorAt(0.0, faded)
        gradient.setColorAt(0.5, RULE_COLOR)
        gradient.setColorAt(1.0, faded)
        painter.fillRect(QRectF((width - rule_w) / 2, rule_y, rule_w, 1), gradient)

        date_font = self._font(s(DATE_PX), DATE_SPACING)
        dm = QFontMetrics(date_font)
        # Letter spacing also trails the last glyph; leave it out so the line
        # is optically centred.
        date_w = dm.horizontalAdvance(date) - s(DATE_PX) * DATE_SPACING
        date_top = rule_y + 1 + s(RULE_GAP)
        painter.setFont(date_font)
        painter.setPen(DATE_COLOR)
        painter.drawText(int(round((width - date_w) / 2)), int(round(self._baseline(date_top, s(DATE_PX), dm))), date)
        painter.end()

    def _glow_images(self, metrics: QFontMetrics, font: QFont, text: str):
        """The blurred halo layers for `text`, computed once and reused until
        the digits, the size or the strength change -- i.e. once a minute,
        not every second. Returns [(image, pad)] ready to draw."""
        scale = UIScaler.get().scale
        strength = self.settings["glow"]
        key = (text, font.pixelSize(), strength)
        if self._glow_cache and self._glow_cache[0] == key:
            return self._glow_cache[1]

        sigma_max = max(sigma for sigma, _, _ in GLOW_LAYERS)
        pad = int(math.ceil(3 * scale(sigma_max)))
        width = metrics.horizontalAdvance(text) + 2 * pad
        box_h = scale(TIME_PX)
        height = box_h + 2 * pad

        mask = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(0)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pad, int(round(pad + self._baseline(0, box_h, metrics))), text)
        painter.end()
        pointer = mask.constBits()
        pointer.setsize(mask.sizeInBytes())
        alpha = np.frombuffer(pointer, dtype=np.uint8).reshape(height, mask.bytesPerLine() // 4, 4)[:, :width, 3] / 255.0

        layers = []
        for sigma, peak, color in GLOW_LAYERS:
            blurred = gaussian_blur(alpha, scale(sigma))
            # Normalise against the widest layer's own scale of the same shape
            # so a thin layer keeps its tight core instead of being flattened.
            layers.append((tint_image(blurred, color, peak * strength), pad))
        self._glow_cache = (key, layers)
        return layers

    def _draw_glow(self, painter: QPainter, metrics: QFontMetrics, font: QFont, x: float, text: str) -> None:
        """A real gaussian halo behind the digits (see GLOW_LAYERS); strength
        0 turns it off."""
        if self.settings["glow"] <= 0:
            return
        margin = UIScaler.get().scale(GLOW_MARGIN)
        for image, pad in self._glow_images(metrics, font, text):
            painter.drawImage(int(round(x)) - pad, margin - pad, image)
