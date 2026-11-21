"""Colour picker for one light: the round colour button on a Smart Lights row and the popup it opens.

Swatches, the white temperature track and the button's own dot are painted by hand with raw `PyQt6.QtGui.QColor`:
they are real colours of a bulb and must not follow the skin (the Ultron skin recolours every warm hue in a stylesheet
or a `clSkinQt.QColor`, which would turn the orange swatch red). Only the chrome around them uses the theme.
The swatch names are the ones voice already understands (`config/entities.json`), so a click and "make the bedroom
purple" do the same thing.
"""
from PyQt6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QAbstractButton, QSlider, QSizePolicy
from PyQt6.QtCore import Qt, QPoint, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor as RawColor, QPainter, QPen, QLinearGradient, QGuiApplication
from clSkinQt import QColor                       # chrome only
from clTheme import Theme

# Name (as in config/entities.json) and its rgb. A test keeps this list in step with that file.
PALETTE = [("red", (255, 0, 0)), ("orange", (255, 165, 0)), ("yellow", (255, 255, 0)), ("green", (0, 255, 0)),
           ("cyan", (0, 255, 255)), ("blue", (0, 0, 255)), ("purple", (128, 0, 128)), ("hot pink", (255, 105, 180))]
# Name in entities.json, its white temperature (0 warm .. 100 cool), the short word on the swatch.
WHITES = [("warm white", 20, "warm"), ("daylight", 50, "day"), ("coolwhite", 80, "cool")]

# The same ranges clControl maps a 0-100 temperature onto.
KELVIN_RANGE = {"wiz": (2700, 6500), "tapo": (2500, 6500)}

DEBOUNCE_MS = 200                                  # a dragged slider sends once it settles, not on every pixel


def temp_to_kelvin(brand, temp):
    lo, hi = KELVIN_RANGE.get(str(brand).lower(), KELVIN_RANGE["tapo"])
    return int(round(lo + max(0, min(100, temp)) * (hi - lo) / 100))


def kelvin_to_temp(brand, kelvin):
    lo, hi = KELVIN_RANGE.get(str(brand).lower(), KELVIN_RANGE["tapo"])
    return int(round(max(0.0, min(1.0, (kelvin - lo) / (hi - lo))) * 100))


def white_tint(kelvin):
    """What a white of this temperature looks like on screen (warm amber, near white, cool blue)."""
    stops = [(2500, (255, 170, 90)), (4600, (255, 244, 228)), (6500, (190, 214, 255))]
    k = max(stops[0][0], min(stops[-1][0], kelvin))
    for (k0, c0), (k1, c1) in zip(stops, stops[1:]):
        if k <= k1:
            f = (k - k0) / (k1 - k0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
    return stops[-1][1]


def display_colour(rgb=None, kelvin=None):
    """The colour a light's button shows: its rgb in colour mode, the tint of its temperature in white mode."""
    if rgb:
        return tuple(int(v) for v in rgb)
    return white_tint(kelvin if kelvin else 3000)


def colour_of_command(fields, brand):
    """The colour a light will have after these light.set fields, for the optimistic update of the button."""
    name = fields.get("color")
    for pname, rgb in PALETTE:
        if name == pname:
            return rgb
    for wname, temp, _ in WHITES:
        if name == wname:
            return white_tint(temp_to_kelvin(brand, temp))
    if fields.get("rgb"):
        return tuple(fields["rgb"])
    if "temp" in fields:
        return white_tint(temp_to_kelvin(brand, fields["temp"]))
    return None


class ColourButton(QAbstractButton):
    """A ring around a dot in the light's current colour (dimmed while the light is off)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Colour")
        self._rgb = display_colour()
        self._on = True
        self._open = False

    def set_light(self, rgb, on):
        self._rgb, self._on = tuple(rgb), bool(on)
        self.update()

    def set_open(self, is_open):
        self._open = bool(is_open)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QColor(Theme.C_PRIMARY)
        hot = self.underMouse() or self._open or self.hasFocus()
        ring.setAlpha(255 if hot else 110)
        p.setPen(QPen(ring, 1.2))
        if hot:
            fill = QColor(Theme.C_PRIMARY)
            fill.setAlpha(36)
            p.setBrush(fill)
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        dot = RawColor(*self._rgb)
        if not self._on:
            dot.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        d = self.width() * 0.52
        p.drawEllipse(QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d))


class Swatch(QAbstractButton):
    """A square of a real colour, or a wider tile of a white with a short word on it. Checkable."""
    hovered = pyqtSignal(str)

    def __init__(self, name, rgb, text="", parent=None):
        super().__init__(parent)
        self.name, self.rgb, self.text_on_swatch = name, tuple(rgb), text
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28 if not text else 30)

    def enterEvent(self, event):
        self.hovered.emit(self.name)
        super().enterEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(RawColor(255, 255, 255, 235 if self.underMouse() else 60), 1))
        p.setBrush(RawColor(*self.rgb))
        p.drawRoundedRect(r, 6, 6)
        if self.isChecked() or self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(Theme.C_TEXT), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        if self.text_on_swatch:
            p.setPen(RawColor(25, 12, 0, 215))
            font = p.font()
            font.setPixelSize(10)
            font.setBold(True)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text_on_swatch)


class GradientSlider(QSlider):
    """A horizontal slider whose groove is a painted gradient. Mouse and keys set the value directly, so the groove
    can be anything."""

    PAD = 9

    def __init__(self, stops, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.stops = stops                         # [(0.0..1.0, (r, g, b)), ...]
        self.setFixedHeight(22)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _value_at(self, x):
        span = max(1, self.width() - 2 * self.PAD)
        f = max(0.0, min(1.0, (x - self.PAD) / span))
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = 8
        groove = QRectF(self.PAD, (self.height() - h) / 2, self.width() - 2 * self.PAD, h)
        grad = QLinearGradient(groove.left(), 0, groove.right(), 0)
        for pos, rgb in self.stops:
            grad.setColorAt(pos, RawColor(*rgb))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(groove, h / 2, h / 2)
        span = self.maximum() - self.minimum()
        f = (self.value() - self.minimum()) / span if span else 0.0
        cx = groove.left() + f * groove.width()
        p.setBrush(QColor(Theme.C_TEXT))
        p.setPen(QPen(QColor(Theme.C_PRIMARY) if self.hasFocus() else RawColor(0, 0, 0, 150), 2))
        p.drawEllipse(QRectF(cx - 7, self.height() / 2 - 7, 14, 14))


class ColourPicker(QWidget):
    """The popup. Emits `chosen(fields)` with the light.set fields to send (color / temp / lum); it never talks to
    the lights itself, so the widget decides how it is dispatched. Closes on a click outside or Escape (Qt.Popup)."""
    chosen = pyqtSignal(dict)

    def __init__(self, name, brand, state=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        state = state or {}
        self.name, self.brand = name, str(brand).lower()
        self.lum = int(state.get("brightness") or 100)
        self.temp = kelvin_to_temp(self.brand, state["kelvin"]) if state.get("kelvin") else 50
        self._pending = {}
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._flush)

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(300)

        # A transparent window around one styled frame (how this app's dialogs do it): the stylesheet background of
        # a translucent top-level itself is not painted.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("ColourPickerCard")
        card.setStyleSheet(f"QFrame#ColourPickerCard {{ background: {Theme.C_BG_INPUT}; border: 1px solid {Theme.C_BORDER_FOCUS}; border-radius: 8px; }}")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(name)
        title.setStyleSheet(f"color: {Theme.C_TEXT}; font-size: {Theme.F_NORMAL}; font-weight: bold; border: none; background: transparent;")
        sub = QLabel(f"{state.get('brand_label') or self.brand.upper()} · colour")
        sub.setStyleSheet(f"color: {Theme.C_TEXT_DIM}; font-size: {Theme.F_TINY}; border: none; background: transparent;")
        head.addWidget(title)
        head.addWidget(sub)
        head.addStretch()
        lay.addLayout(head)

        lay.addWidget(self._section("COLOURS"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.swatches = []
        for cname, rgb in PALETTE:
            row.addWidget(self._swatch(Swatch(cname, rgb)))
        lay.addLayout(row)

        lay.addWidget(self._section("WHITES"))
        wrow = QHBoxLayout()
        wrow.setSpacing(6)
        for wname, temp, word in WHITES:
            kelvin = temp_to_kelvin(self.brand, temp)
            wrow.addWidget(self._swatch(Swatch(wname, white_tint(kelvin), f"{word} · {kelvin} K")))
        lay.addLayout(wrow)

        self.hint = QLabel("Hover a swatch for its voice command.")
        self.hint.setWordWrap(True)
        self.hint.setMinimumHeight(30)               # two lines, so a long light name does not make the popup jump
        self.hint.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.hint.setStyleSheet(f"color: {Theme.C_TEXT_DIM}; font-size: {Theme.F_TINY}; border: none; background: transparent;")
        lay.addWidget(self.hint)

        self.lum_value, self.lum_slider = self._slider(lay, "Brightness", [(0.0, (42, 28, 16)), (1.0, (255, 230, 204))], 1, 100, self.lum)
        self.lum_slider.valueChanged.connect(self._on_lum)
        self.temp_value, self.temp_slider = self._slider(
            lay, "White temperature", [(0.0, (255, 162, 77)), (0.5, (255, 243, 224)), (1.0, (188, 214, 255))], 0, 100, self.temp)
        self.temp_slider.valueChanged.connect(self._on_temp)
        self._show_values()

    # --- building blocks ---
    def _section(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {Theme.C_TEXT_DIM}; font-size: {Theme.F_TINY}; letter-spacing: 1px; border: none; background: transparent;")
        return lbl

    def _swatch(self, sw):
        sw.hovered.connect(self._on_hover)
        sw.clicked.connect(lambda checked=False, s=sw: self._on_swatch(s))
        self.swatches.append(sw)
        return sw

    def _slider(self, lay, title, stops, lo, hi, value):
        head = QHBoxLayout()
        lbl = QLabel(title.upper())
        lbl.setStyleSheet(f"color: {Theme.C_TEXT_DIM}; font-size: {Theme.F_TINY}; letter-spacing: 1px; border: none; background: transparent;")
        val = QLabel("")
        val.setStyleSheet(f"color: {Theme.C_TEXT}; font-size: {Theme.F_TINY}; border: none; background: transparent;")
        head.addWidget(lbl)
        head.addStretch()
        head.addWidget(val)
        lay.addLayout(head)
        slider = GradientSlider(stops)
        slider.setRange(lo, hi)
        slider.setValue(value)
        lay.addWidget(slider)
        return val, slider

    def _show_values(self):
        self.lum_value.setText(f"{self.lum} %")
        self.temp_value.setText(f"{temp_to_kelvin(self.brand, self.temp)} K")

    # --- behaviour ---
    def _on_hover(self, name):
        shown = "cool white" if name == "coolwhite" else name
        self.hint.setText(f"{shown} · say \"make the {self.name.lower()} {shown}\"")

    def _on_swatch(self, clicked):
        for sw in self.swatches:
            sw.setChecked(sw is clicked)
        self._send({"color": clicked.name, "lum": self.lum})

    def _on_lum(self, value):
        self.lum = int(value)
        self._show_values()
        self._queue({"lum": self.lum})

    def _on_temp(self, value):
        self.temp = int(value)
        self._show_values()
        for sw in self.swatches:
            sw.setChecked(False)
        self._queue({"temp": self.temp, "lum": self.lum})

    def _queue(self, fields):
        self._pending = fields
        self._debounce.start(DEBOUNCE_MS)

    def _flush(self):
        if self._pending:
            fields, self._pending = self._pending, {}
            self._send(fields)

    def _send(self, fields):
        self._debounce.stop()
        self._pending = {}
        self.chosen.emit(dict(fields))

    def closeEvent(self, event):
        self._flush()                                # a slider moved just before the popup closed still gets sent
        super().closeEvent(event)

    def show_near(self, anchor):
        """Opens just under `anchor` (a widget), flipped above it when there is no room below, kept on the screen."""
        self.adjustSize()
        below = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left() + 4, min(below.x(), area.right() - self.width() - 4))
        y = below.y()
        if y + self.height() > area.bottom() - 4:
            y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        self.move(x, max(area.top() + 4, y))
        self.show()
