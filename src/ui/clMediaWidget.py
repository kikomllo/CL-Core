import logging
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QProgressBar,
    QStyle, QStyleOptionSlider, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import QTimer, QSize, Qt, QVariantAnimation, QEasingCurve, QRectF, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QLinearGradient
from clUIScaler import UIScaler
from ui.clMarqueeLabel import MarqueeLabel

def s(val):
    return UIScaler.get().scale(val)

class ClickToPositionSlider(QSlider):
    """A QSlider whose groove click jumps straight to the clicked point --
    Qt's default style instead treats a groove click as a page-step nudge,
    which reads as "tap increases by a percentage" rather than "tap sets the
    level" for a volume fader.

    Handles press/move/release itself rather than computing the value once
    and handing off to the base class's mousePressEvent: this widget's QSS
    (see MediaVolumeSlider/AppVolumeSlider in clTheme.py) deliberately sets
    the handle to 0px as a fill-gradient fix, and a zero-size handle rect can
    never pass Qt's own hit-test, so delegating back to super() only ever
    re-hits the groove and re-triggers the page-step behavior this is meant
    to replace."""

    def _value_from_pos(self, pos) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        if self.orientation() == Qt.Orientation.Horizontal:
            span = max(1, groove.width() - 1)
            click_pos = int(pos.x()) - groove.x()
        else:
            span = max(1, groove.height() - 1)
            click_pos = int(pos.y()) - groove.y()
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), click_pos, span, opt.upsideDown)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_from_pos(event.position()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_from_pos(event.position()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

# Simple Icons (simpleicons.org) SVGs with their official brand color baked
# in -- matched against the app's display name (cross-platform, unlike
# Windows' raw process name) rather than an exact id.
APP_BRAND_ICONS = {
    "spotify": ("spotify.svg", "#1ED760"),
    "firefox": ("firefox.svg", "#FF7139"),
    "discord": ("discord.svg", "#5865F2"),
    "obs": ("obs.svg", "#302E31"),
    "chrome": ("chrome.svg", "#4285F4"),
    "brave": ("brave.svg", "#FB542B"),
    "opera": ("opera.svg", "#FF1B2D"),
    "vivaldi": ("vivaldi.svg", "#EF3939"),
    "razer": ("razer.svg", "#00FF00"),
}

def _brand_icon_for_app(name):
    name = name.lower()
    for key, icon in APP_BRAND_ICONS.items():
        if key in name:
            return icon
    return None

def _capitalize_first(name):
    return name[:1].upper() + name[1:] if name else name

def _set_hover_icon(btn, icon_name, size):
    """Grey at rest, orange on hover -- QSS alone can't recolor a QIcon's
    already-rendered pixmap, so both variants are pre-baked here."""
    btn.set_icons(Theme.get_icon(icon_name, size, Theme.C_TEXT_DIM), Theme.get_icon(icon_name, size, Theme.C_PRIMARY))

def _sync_slider_extreme_state(slider):
    """Toggles the atMin/atMax dynamic properties Theme's slider QSS keys
    off of (see Theme._slider_extreme_state_qss) -- must be called after
    every setValue, not just left to valueChanged, since a blockSignals-
    wrapped setValue (used when a fresh value arrives from the daemon)
    never fires that signal at all."""
    at_min = slider.value() <= slider.minimum()
    at_max = slider.value() >= slider.maximum()
    if slider.property("atMin") == at_min and slider.property("atMax") == at_max:
        return
    slider.setProperty("atMin", at_min)
    slider.setProperty("atMax", at_max)
    slider.style().unpolish(slider)
    slider.style().polish(slider)

class HoverIconButton(QPushButton):
    """A borderless icon button whose icon recolors on hover -- QSS can't
    touch an already-rendered QIcon pixmap, so this swaps between two
    pre-baked icons instead."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal_icon = None
        self._hover_icon = None

    def set_icons(self, normal_icon, hover_icon):
        self._normal_icon = normal_icon
        self._hover_icon = hover_icon
        super().setIcon(normal_icon)

    def enterEvent(self, event):
        if self._hover_icon is not None:
            super().setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._normal_icon is not None:
            super().setIcon(self._normal_icon)
        super().leaveEvent(event)

def _fmt_time(secs):
    secs = max(0, int(secs))
    return f"{secs // 60}:{secs % 60:02d}"

class DotToggleButton(QPushButton):
    """A small icon toggle with an "on" dot underneath. Drawn by hand: QSS
    can't put a dot outside a circular border, and the dot keeps the on
    state readable without relying on colour alone. circle=True is the same
    look as the main controls' MediaSmallBtn circles (dark fill, orange
    ring); circle=False is just the icon and the dot (shuffle/repeat). The
    widget has room for the dot below the icon (and, when centered, the same
    amount above so the icon stays vertically centred among its neighbours). Pending (clicked, awaiting
    confirmation) is a dashed ring (circle) or a dimmed icon with a hollow
    dot (bare)."""

    DOT_ZONE = 8
    _DIM_ICON = "#8a7a66"

    def __init__(self, diameter, icon_name, icon_px, circle=True, centered=True, parent=None):
        super().__init__(parent)
        self._d = diameter
        self._icon_name = icon_name
        self._icon_px = icon_px
        self._circle = circle
        self._active = False
        self._pending = False
        # centered: room above as well as below, so the icon lines up with
        # neighbours of other heights. Not centered: icon at the very top
        # and only the dot's room below -- for a row of its own, where the
        # extra room above would just be wasted vertical padding.
        self._top = self.DOT_ZONE / 2 if centered else 0
        self.setFixedSize(diameter, int(diameter + self._top + self.DOT_ZONE / 2 + (0 if centered else 1)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none;")
        self._refresh_icon()

    def is_active(self):
        return self._active

    def set_active(self, active):
        active = bool(active)
        if active != self._active:
            self._active = active
            self._refresh_icon()
            self.update()

    def set_pending(self, pending):
        if bool(pending) != self._pending:
            self._pending = bool(pending)
            self.update()

    def set_icon_name(self, icon_name):
        if icon_name != self._icon_name:
            self._icon_name = icon_name
            self._refresh_icon()
            self.update()

    def _refresh_icon(self):
        color = Theme.C_PRIMARY if self._active else self._DIM_ICON
        self.setIcon(Theme.get_icon(self._icon_name, self._icon_px, color))
        self.setIconSize(QSize(self._icon_px, self._icon_px))

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._pending:
            p.setOpacity(0.55)
        top = self._top
        circle = QRectF(0.5, top + 0.5, self._d - 1, self._d - 1)

        if self._circle:
            if self._active:
                fill, ring = QColor(255, 170, 0, 51), QColor(Theme.C_PRIMARY)
            elif self.underMouse():
                fill, ring = QColor(255, 150, 0, 89), QColor(Theme.C_PRIMARY)
            else:
                fill, ring = QColor(35, 18, 5, 204), QColor(255, 160, 0, 71)
            pen = QPen(ring, 1)
            if self._pending:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawEllipse(circle)
            pm = self.icon().pixmap(self.iconSize())
        else:
            # No circle to light up on hover, so the icon itself brightens.
            color = Theme.C_PRIMARY if self._active else (Theme.C_PRIMARY_HOVER if self.underMouse() else self._DIM_ICON)
            pm = Theme.get_icon(self._icon_name, self._icon_px, color).pixmap(self.iconSize())
        p.drawPixmap(int(circle.center().x() - pm.width() / 2), int(circle.center().y() - pm.height() / 2), pm)

        dot = QRectF(self.width() / 2 - 2, top + self._d + 1, 4, 4)
        if self._active and not self._pending:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Theme.C_PRIMARY))
            p.drawEllipse(dot)
        elif self._active and not self._circle:  # pending, bare: hollow dot
            p.setPen(QPen(QColor(Theme.C_PRIMARY), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


class SeekBar(QProgressBar):
    """The thin progress bar, now clickable: a taller invisible hit area
    (so it isn't a 4px target), a thicker track + knob on hover, and a grey
    fill when the player can't seek. Still a
    QProgressBar (0-1000), so value()/setValue() work as before."""

    seekRequested = pyqtSignal(float)  # fraction 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self.setFixedHeight(s(16))
        self._hover = False
        self._seekable = True

    def set_seekable(self, seekable):
        seekable = bool(seekable)
        if seekable != self._seekable:
            self._seekable = seekable
            self.setCursor(Qt.CursorShape.PointingHandCursor if seekable else Qt.CursorShape.ArrowCursor)
            self.update()

    def _fraction_at(self, x):
        return max(0.0, min(1.0, x / max(1, self.width())))

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hover = self._seekable
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def mousePressEvent(self, event):
        if self._seekable and event.button() == Qt.MouseButton.LeftButton:
            self.seekRequested.emit(self._fraction_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        thickness = s(6) if self._hover else s(4)
        track = QRectF(0, (self.height() - thickness) / 2, self.width(), thickness)
        radius = thickness / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 26))
        p.drawRoundedRect(track, radius, radius)

        span = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / span if span > 0 else 0.0
        fill_w = track.width() * max(0.0, min(1.0, fraction))
        if fill_w > 0:
            if self._seekable:
                grad = QLinearGradient(track.left(), 0, track.right(), 0)
                grad.setColorAt(0, QColor("#ff8c00"))
                grad.setColorAt(1, QColor("#ffcc00"))
                p.setBrush(grad)
            else:
                p.setBrush(QColor("#6f6355"))
            p.drawRoundedRect(QRectF(track.left(), track.top(), fill_w, thickness), radius, radius)

        if self._hover:
            knob = s(12)
            p.setPen(QPen(QColor(Theme.C_PRIMARY), 2))
            p.setBrush(QColor(Theme.C_TEXT))
            p.drawEllipse(QRectF(track.left() + fill_w - knob / 2, self.height() / 2 - knob / 2, knob, knob))
        p.end()


class MediaWidget(QWidget):
    def __init__(self, parent=None, grid_mode=False):
        super().__init__(parent)
        self.router = ActionRouter()
        self.grid_mode = grid_mode
        self._fader_width = s(30)
        self._last_nonzero_volume = 100

        self._base_right_margin = s(15)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(s(15), s(10), self._base_right_margin, 0)
        self.layout.setSpacing(s(6))

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(s(6))

        # Shuffle / repeat sit next to the badge (where the lyrics toggle used
        # to be; that one lives in the bottom button row now). Their state
        # comes from the local media session (free) via media_status; a click
        # flips them optimistically and shows a dimmed "pending" icon until
        # the next status confirms (or a short timeout gives up).
        self._shuffle_on = False
        self._repeat_mode = "off"
        self._pending_modes = {}
        self._queue_refreshing = False
        self.shuffle_btn = DotToggleButton(s(20), "shuffle.svg", s(16), circle=False, centered=False)
        self.shuffle_btn.setToolTip("Shuffle")
        self.shuffle_btn.clicked.connect(self._toggle_shuffle)
        self.repeat_btn = DotToggleButton(s(20), "repeat.svg", s(16), circle=False, centered=False)
        self.repeat_btn.setToolTip("Repeat")
        self.repeat_btn.clicked.connect(self._cycle_repeat)
        self._mode_pending_timer = QTimer(self)
        self._mode_pending_timer.setSingleShot(True)
        self._mode_pending_timer.timeout.connect(self._clear_mode_pending)

        # Header: the badge on the left, shuffle and repeat at the far right.
        # It sits above the content/fader row (added to self.layout below), so
        # opening the volume fader never narrows it or pushes the icons.
        self.header_layout = header_layout = QHBoxLayout()
        header_layout.setSpacing(s(8))
        self.badge_lbl = QLabel("SPOTIFY PLAYER")
        self.badge_lbl.setStyleSheet(Theme.get_style("BadgeLabel"))
        # Same height as the icons' own box, so the text and the icons share
        # a centre line without the header growing to fit the dots' room.
        self.badge_lbl.setFixedHeight(s(20))
        header_layout.addWidget(self.badge_lbl, 0, Qt.AlignmentFlag.AlignTop)
        header_layout.addStretch()
        header_layout.addWidget(self.shuffle_btn)
        header_layout.addWidget(self.repeat_btn)

        # Zero spacing keeps title/artist tight against each other; the
        # stretch below is a flexible gap so resizing the widget taller
        # pushes the controls/progress bar further down to fill the new
        # height, instead of leaving everything clustered under the artist
        # name.
        self.title_lbl = MarqueeLabel("No Media Playing", force_single_line=True)
        self.title_lbl.setStyleSheet(Theme.get_style("TitleLabel"))
        self.artist_lbl = MarqueeLabel("Unknown Artist", force_single_line=True)
        self.artist_lbl.setStyleSheet(Theme.get_style("SubtitleLabel"))
        self.content_layout.addWidget(self.title_lbl)
        self.content_layout.addWidget(self.artist_lbl)
        self.content_layout.addStretch(1)

        # Playback controls -- a normal layout row sitting directly above
        # the progress bar.
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(s(18))

        self.prev_btn = QPushButton()
        self.prev_btn.setFixedSize(32, 32)
        self.prev_btn.setIcon(Theme.get_icon("skip_back.svg", 16))
        self.prev_btn.setIconSize(QSize(16, 16))
        self.prev_btn.setStyleSheet(Theme.get_style("MediaSmallBtn"))
        self.prev_btn.clicked.connect(lambda: self.send_cmd("prev", silent=True))

        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setIcon(Theme.get_icon("play.svg", 18, "#ffffff"))
        self.play_btn.setIconSize(QSize(18, 18))
        self.play_btn.setStyleSheet(Theme.get_style("MediaPlayBtn"))
        self.play_btn.clicked.connect(self.toggle_optimistic)

        self.next_btn = QPushButton()
        self.next_btn.setFixedSize(32, 32)
        self.next_btn.setIcon(Theme.get_icon("skip_forward.svg", 16))
        self.next_btn.setIconSize(QSize(16, 16))
        self.next_btn.setStyleSheet(Theme.get_style("MediaSmallBtn"))
        self.next_btn.clicked.connect(lambda: self.send_cmd("next", silent=True))

        self.controls_layout.addStretch()
        self.controls_layout.addWidget(self.prev_btn)
        self.controls_layout.addWidget(self.play_btn)
        self.controls_layout.addWidget(self.next_btn)
        self.controls_layout.addStretch()
        self.content_layout.addLayout(self.controls_layout)

        self.time_layout = time_layout = QHBoxLayout()
        time_layout.setSpacing(s(8))
        self.position_lbl = QLabel("0:00")
        self.position_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        self.progress_bar = SeekBar()
        self.progress_bar.seekRequested.connect(self._on_seek_requested)
        self.duration_lbl = QLabel("0:00")
        self.duration_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        time_layout.addWidget(self.position_lbl)
        time_layout.addWidget(self.progress_bar, 1)
        time_layout.addWidget(self.duration_lbl)
        self.content_layout.addLayout(time_layout)

        # Volume fader -- a normal layout column next to content_layout (see
        # the row assembled below), hidden by fixing its width to 0 until
        # hovered.
        self.fader_container = QWidget()
        self.fader_container.setFixedWidth(0)
        fader_layout = QVBoxLayout(self.fader_container)
        # The top margin starts the bar at the title's cap height instead of
        # its label box, which has a few px of leading above the glyphs.
        fader_layout.setContentsMargins(0, s(4), 0, 0)
        fader_layout.setSpacing(s(6))

        self.volume_slider = ClickToPositionSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setStyleSheet(Theme.get_style("MediaVolumeSlider"))
        _sync_slider_extreme_state(self.volume_slider)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        # Dispatches only once the user releases the slider, not on every
        # value change while dragging -- avoids hammering the Spotify Web
        # API with one request per pixel.
        self.volume_slider.sliderReleased.connect(self._dispatch_volume)

        self.mute_btn = HoverIconButton()
        # 2px shorter than it is wide: the column is bottom-anchored, so the
        # icon (centred in the box) and the slider above it both sit lower,
        # putting the speaker's bottom level with the time labels' digits.
        self.mute_btn.setFixedSize(s(22), s(20))
        _set_hover_icon(self.mute_btn, "speaker.svg", s(16))
        self.mute_btn.setIconSize(QSize(s(16), s(16)))
        self.mute_btn.setStyleSheet("background: transparent; border: none;")
        self.mute_btn.clicked.connect(self._toggle_mute)

        # The column only spans the content below the header, so the slider
        # stretches from the title down to the mute button beside the progress
        # bar -- title-to-progress at any widget height.
        fader_layout.addWidget(self.volume_slider, 1, alignment=Qt.AlignmentFlag.AlignHCenter)
        fader_layout.addWidget(self.mute_btn, 0, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Pairs the main content column with the fader as ordinary layout
        # siblings -- narrowing the column when the fader opens is just
        # normal reflow, and the fader's own (fixed) width never feeds back
        # into anything live.
        self.layout.addLayout(self.header_layout)
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        content_row.addLayout(self.content_layout, 1)
        content_row.addWidget(self.fader_container, 0)
        self.layout.addLayout(content_row)

        # Bottom button row: Up Next, lyrics, App Volume -- small circles
        # under the progress bar. The dot under a button marks it on/open.
        # Up Next and App Volume are drawers that grow the widget downward;
        # only one is open at a time.
        #
        # The lyrics toggle drives LyricsDisplay (a sibling of this widget,
        # owned by JarvisUI, not this one) -- self.window() reaches it the
        # same way showEvent()/_tick() already reach JarvisUI for other
        # state, since there's no signal wired between the two widgets.
        self._lyrics_enabled = True
        # The middle button, a little bigger than its two neighbours.
        self.lyrics_toggle_btn = DotToggleButton(s(28), "music.svg", s(15), centered=False)
        self.lyrics_toggle_btn.setToolTip("Toggle lyrics")
        self.lyrics_toggle_btn.clicked.connect(self._toggle_lyrics)
        self._set_lyrics_toggle_icon()

        self.queue_btn = DotToggleButton(s(24), "list.svg", s(13), centered=False)
        self.queue_btn.setToolTip("Up Next")
        self.queue_btn.clicked.connect(self._toggle_queue)

        # App Volume -- collapsible per-app volume/mute mixer, backed by
        # clTerminal.py's pycaw (Windows) / pactl (Linux) session list.
        self.app_volume_btn = DotToggleButton(s(24), "speaker.svg", s(13), centered=False)
        self.app_volume_btn.setToolTip("App Volume")
        self.app_volume_btn.clicked.connect(self._toggle_app_volume)

        self.bottom_row = QHBoxLayout()
        self.bottom_row.setContentsMargins(0, 0, 0, 0)
        self.bottom_row.setSpacing(s(50))
        self.bottom_row.addStretch()
        # Different heights, so centre each vertically: the circles line up.
        center = Qt.AlignmentFlag.AlignVCenter
        self.bottom_row.addWidget(self.queue_btn, 0, center)
        self.bottom_row.addWidget(self.lyrics_toggle_btn, 0, center)
        self.bottom_row.addWidget(self.app_volume_btn, 0, center)
        self.bottom_row.addStretch()
        self.layout.addLayout(self.bottom_row)

        # Up Next -- the upcoming tracks, fetched from the Spotify API once
        # (clSpotify.py's `queue` action) and then followed locally; the
        # retained jarvis/sys/spotify_queue message feeds update_queue().
        self.queue_body = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_body)
        self.queue_layout.setContentsMargins(0, 8, 0, 8)
        self.queue_layout.setSpacing(s(8))
        queue_header = QHBoxLayout()
        queue_header.setContentsMargins(0, 0, 0, 0)
        self.queue_caption_lbl = QLabel("Loading...")
        self.queue_caption_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        self.queue_refresh_btn = QPushButton()
        self.queue_refresh_btn.setFixedSize(s(22), s(22))
        self.queue_refresh_btn.setIcon(Theme.get_icon("refresh.svg", s(11)))
        self.queue_refresh_btn.setIconSize(QSize(s(11), s(11)))
        self.queue_refresh_btn.setStyleSheet(Theme.get_style("MediaSmallBtn", radius=s(11)))
        self.queue_refresh_btn.setToolTip("Refresh from Spotify")
        self.queue_refresh_btn.clicked.connect(self._refresh_queue)
        queue_header.addWidget(self.queue_caption_lbl, 1)
        queue_header.addWidget(self.queue_refresh_btn)
        self.queue_layout.addLayout(queue_header)
        self.queue_rows_widget = QWidget()
        self.queue_rows_layout = QVBoxLayout(self.queue_rows_widget)
        self.queue_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_rows_layout.setSpacing(s(8))
        self.queue_rows_opacity = QGraphicsOpacityEffect(self.queue_rows_widget)
        self.queue_rows_opacity.setOpacity(1.0)
        self.queue_rows_widget.setGraphicsEffect(self.queue_rows_opacity)
        self.queue_layout.addWidget(self.queue_rows_widget)
        self.queue_body.hide()
        self.layout.addWidget(self.queue_body)
        self._queue_loaded = False
        self._queue_total = 0
        self._queue_refresh_timer = QTimer(self)
        self._queue_refresh_timer.setSingleShot(True)
        self._queue_refresh_timer.timeout.connect(self._end_queue_refresh)

        self.app_volume_body = QWidget()
        self.app_volume_layout = QVBoxLayout(self.app_volume_body)
        self.app_volume_layout.setContentsMargins(0, 8, 0, 8)
        self.app_volume_layout.setSpacing(s(10))
        self.app_volume_empty_lbl = QLabel("No other apps are playing audio.")
        self.app_volume_empty_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        self.app_volume_layout.addWidget(self.app_volume_empty_lbl)
        self.app_volume_body.hide()
        self.layout.addWidget(self.app_volume_body)
        self.app_volume_rows = {}
        self._app_volume_delta = 0
        # Cached once per drawer-open (see _toggle_app_volume) rather than
        # re-fetched per row -- it's the same system-wide device list for
        # every app, and rarely changes mid-session.
        self._output_devices = []

        self._fader_anim = QVariantAnimation(self)
        self._fader_anim.setDuration(200)
        self._fader_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fader_anim.valueChanged.connect(self._on_fader_width_changed)

        self.position = 0.0
        self.duration = 0.0
        self.status = "Paused"
        self._waiting_for_status = False
        self._drawers_min_width = 0
        self._measure_drawers_min_width()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def minimumSizeHint(self):
        # app_volume_body is a real (not floating) layout child, so once
        # it's visible, base already includes Qt's own natural contribution
        # from it -- but that natural contribution is width-independent and
        # doesn't account for actual word-wrapping, so it doesn't match
        # _app_volume_room()'s heightForWidth-based figure. Back it out
        # before adding ours, or the two would double-count and inflate the
        # minimum past what's really needed (which, since this same
        # minimumSizeHint() is what DraggableWidget.resize() clamps
        # against, silently clamped every attempt to shrink the wrapper
        # back down on close).
        base = super().minimumSizeHint()
        base_height = base.height()
        open_body = self._open_drawer_body()
        if open_body is not None:
            base_height -= open_body.minimumSizeHint().height() + self.layout.spacing()

        min_height = base_height + self._app_volume_room()
        # Width is the widest any state needs (closed, Up Next, App Volume),
        # so opening a drawer never widens the widget and the minimum doesn't
        # jump with the state. Height is the opposite on purpose: the drawers
        # exist to grow the widget downward.
        return QSize(max(base.width(), self._drawers_min_width), min_height)

    def _measure_drawers_min_width(self):
        """The widget width each drawer needs, measured from real sample rows
        (the widest a row of either drawer can be) rather than from whatever
        rows happen to exist now -- so the answer is the same before any data
        arrives and the first drawer open can't widen the widget. Called once
        at construction and again on rescale, never from inside a layout pass
        (it builds and discards a throwaway App Volume row)."""
        queue_row = self._create_queue_row(1, {"title": "x", "artist": "x", "duration_ms": 1000})
        app_row = self._create_app_volume_row({"id": "__sizing__", "name": "x", "media_player": "x"})
        widest_row = max(queue_row.minimumSizeHint().width(), app_row["widget"].minimumSizeHint().width())
        queue_row.deleteLater()
        self.app_volume_layout.removeWidget(app_row["widget"])
        app_row["widget"].hide()
        app_row["widget"].deleteLater()
        margins = self.layout.contentsMargins()
        self._drawers_min_width = widest_row + margins.left() + margins.right()

    def _open_drawer_body(self):
        """The Up Next or App Volume drawer that's currently open, if any."""
        # isHidden(), not isVisible(): that's the flag QLayout itself uses to
        # decide whether an item counts, and it doesn't depend on the whole
        # widget having been shown yet.
        for body in (self.app_volume_body, self.queue_body):
            if not body.isHidden():
                return body
        return None

    def _app_volume_room(self):
        # Height the open drawer (either one) needs, or 0 when both are
        # closed. Layouts skip spacing around a hidden widget, so showing a
        # drawer also opens up one more gap (between it and the button row)
        # that wasn't there before.
        body = self._open_drawer_body()
        if body is None:
            return 0
        return body.sizeHint().height() + self.layout.spacing()

    def update_scaling(self):
        self._base_right_margin = s(15)
        self.layout.setContentsMargins(s(15), s(10), self._base_right_margin, 0)
        self.layout.setSpacing(s(6))
        self.content_layout.setSpacing(s(6))
        self._fader_width = s(30)
        if self.fader_container.width() > 0:
            self.fader_container.setFixedWidth(self._fader_width)
        if hasattr(self, 'progress_bar'):
            self.progress_bar.setFixedHeight(s(16))
        if hasattr(self, 'controls_layout'):
            self.controls_layout.setSpacing(s(18))
        if hasattr(self, 'prev_btn'):
            self.prev_btn.setFixedSize(32, 32)
            self.play_btn.setFixedSize(40, 40)
            self.next_btn.setFixedSize(32, 32)
        if hasattr(self, '_drawers_min_width'):
            self._measure_drawers_min_width()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._animate_fader(self._fader_width)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate_fader(0)

    def _animate_fader(self, target_width):
        self._fader_anim.stop()
        self._fader_anim.setStartValue(self.fader_container.width())
        self._fader_anim.setEndValue(target_width)
        self._fader_anim.start()

    def _on_fader_width_changed(self, value):
        # fader_container is a normal layout sibling of content_layout now
        # (see the row assembled in __init__) -- changing its fixed width is
        # all that's needed, ordinary layout reflow handles the rest.
        self.fader_container.setFixedWidth(value)

    def _on_volume_changed(self, value):
        # Local-only feedback while dragging -- the real dispatch waits for
        # sliderReleased (see __init__) so a drag sends one request, not one
        # per pixel moved.
        if value > 0:
            self._last_nonzero_volume = value
        _set_hover_icon(self.mute_btn, "speaker_mute.svg" if value == 0 else "speaker.svg", s(16))
        _sync_slider_extreme_state(self.volume_slider)

    def _dispatch_volume(self):
        try:
            self.router.dispatch("spotify.control", action="volume", volume=self.volume_slider.value(), silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _toggle_mute(self):
        # setValue() here is a programmatic jump, not a drag, so it never
        # emits sliderReleased -- dispatch explicitly or a mute/unmute click
        # would silently do nothing until the next real drag.
        if self.volume_slider.value() > 0:
            self._last_nonzero_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._last_nonzero_volume or 100)
        self._dispatch_volume()

    def _toggle_app_volume(self):
        self._toggle_drawer(self.app_volume_body)

    def _toggle_queue(self):
        self._toggle_drawer(self.queue_body)

    def _toggle_drawer(self, body):
        # Shared by the App Volume and Up Next drawers: only one is open at
        # a time, so opening one first closes the other (each close/open
        # grows or shrinks the wrapper by its own delta, below).
        if body.isHidden():
            other = self.queue_body if body is self.app_volume_body else self.app_volume_body
            if not other.isHidden():
                self._toggle_drawer(other)

        # Opening used to just reveal the label in-place, which stole space
        # from content_layout's flexible gap instead of growing the widget
        # -- since the wrapper's overall size didn't change, that
        # compression visually pushed the progress bar and controls upward.
        # Growing the wrapper itself instead makes the drawer extend the
        # widget downward, leaving existing content in place. Closing
        # forces the wrapper back down by exactly the same delta that was
        # added on open.
        #
        # wrapper is a top-level window in standalone/unpinned mode, and Qt
        # won't let a top-level window get smaller than its layout's
        # minimum size -- the instant setVisible(True) below raises that
        # minimum, Qt auto-grows the wrapper to fit it, BEFORE any of our
        # own code runs. Reading "height before" after that call, like an
        # earlier version of this did, captured that already-inflated
        # height as the baseline, silently swallowing part of the real
        # delta into a "before" that was never actually the closed-drawer
        # height. Capturing it first avoids that entirely.
        opening = body.isHidden()
        wrapper = self.parentWidget()
        height_before = wrapper.height() if wrapper is not None else 0

        if wrapper is not None:
            # A top-level window's layout enforces "can't shrink below what
            # I need" by calling wrapper.setMinimumSize() explicitly -- a
            # real stored property, separate from minimumSizeHint(), that
            # resize() itself clamps against. That call only ever raises
            # the stored minimum; nothing lowers it again once the drawer
            # closes and the true minimum drops back down, so a later
            # resize() here could stay clamped to the old, larger one.
            # Clearing it lets our own resize below take effect; the layout
            # re-establishes a correct (smaller) one on its own the next
            # time it actually needs to.
            wrapper.setMinimumSize(0, 0)

        if opening:
            body.setVisible(True)
            self.updateGeometry()
            if wrapper is not None:
                room = self._app_volume_room()
                self._app_volume_delta = room
                if room > 0:
                    wrapper.resize(wrapper.width(), height_before + room)
            if body is self.app_volume_body:
                self._request_app_volumes()
                self._request_output_devices()
            else:
                self._request_queue()
        else:
            body.setVisible(False)
            self.updateGeometry()
            delta = getattr(self, '_app_volume_delta', 0)
            if wrapper is not None and delta > 0:
                wrapper.resize(wrapper.width(), height_before - delta)
            self._app_volume_delta = 0
        self._sync_drawer_buttons()

    def _sync_drawer_buttons(self):
        self.queue_btn.set_active(not self.queue_body.isHidden())
        self.app_volume_btn.set_active(not self.app_volume_body.isHidden())

    def _sync_app_volume_drawer_size(self):
        """Re-applies the open drawer's height delta after its content
        changes while it's already open (e.g. the per-app list or the queue
        refreshing) -- grows/shrinks the wrapper by the difference from
        what was already added, not by the drawer's full new height, so
        repeated refreshes don't compound. Safe to read wrapper.height()
        live here (unlike _toggle_drawer's open branch): no
        setVisible() call happens in this path, so there's no implicit
        Qt auto-grow to get contaminated by."""
        if self._open_drawer_body() is None:
            return
        wrapper = self.parentWidget()
        if wrapper is None:
            return
        wrapper.setMinimumSize(0, 0)
        room = self._app_volume_room()
        diff = room - self._app_volume_delta
        if diff != 0:
            wrapper.resize(wrapper.width(), wrapper.height() + diff)
        self._app_volume_delta = room

    def _request_app_volumes(self):
        try:
            self.router.dispatch("terminal.app_volume", action="list_app_volumes")
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _request_output_devices(self):
        try:
            self.router.dispatch("terminal.app_volume", action="list_output_devices")
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def update_output_devices(self, devices):
        """Caches the system's real playback devices (see clTerminal.py's
        list_output_devices) for the per-app output-device dropdown --
        same list for every app row, so it's fetched once per drawer-open
        rather than per row."""
        self._output_devices = devices

    def update_app_volumes(self, apps):
        """Rebuilds the App Volume drawer's per-app rows from a fresh apps
        list (see clTerminal.py's list_app_volumes). Called whenever
        jarvis/sys/app_volumes delivers new data, not just while the
        drawer is open, so it's ready to show immediately next time it
        opens."""
        current_ids = {app.get("id") for app in apps if app.get("id")}
        for app_id in list(self.app_volume_rows.keys()):
            if app_id not in current_ids:
                self.app_volume_rows.pop(app_id)["widget"].deleteLater()

        for app in apps:
            app_id = app.get("id")
            if not app_id:
                continue
            if app_id in self.app_volume_rows:
                self._update_app_volume_row(self.app_volume_rows[app_id], app)
            else:
                self.app_volume_rows[app_id] = self._create_app_volume_row(app)

        self.app_volume_empty_lbl.setVisible(not apps)
        self._sync_app_volume_drawer_size()

    def _create_app_volume_row(self, app):
        # Layout mirrors the Media Widget Concept artifact's App Volume
        # rows: icon, then a two-line name/now-playing block, then a
        # play/pause toggle, then a compact mute+slider group on the right.
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(s(8))

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(s(20), s(20))
        self._set_app_icon(icon_lbl, app.get("name", ""))

        # MarqueeLabel (same as the main widget's title/artist) keeps a long
        # app name or now-playing title from growing the drawer's minimum
        # size -- it reports a fixed minimumSizeHint and scrolls on hover
        # instead of forcing the row wider.
        name_lbl = MarqueeLabel(_capitalize_first(app.get("name", "Unknown")), force_single_line=True)
        name_lbl.setStyleSheet(Theme.get_style("SubtitleLabel"))
        # "Currently playing" and play/pause both come from clTerminal.py
        # matching this app's audio session against the OS's media
        # transport sessions (SMTC/MPRIS) by name -- not every
        # audio-producing app has one (e.g. Discord voice chat), so both
        # start hidden and only appear once a match exists.
        subtitle_lbl = MarqueeLabel(app.get("now_playing", ""), force_single_line=True)
        subtitle_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(0)
        info_col.addWidget(name_lbl)
        info_col.addWidget(subtitle_lbl)

        # Opens the per-app output-device picker (see _show_output_device_menu)
        # -- mirrors ms-settings:apps-volume's own per-app device dropdown.
        device_btn = HoverIconButton()
        device_btn.setFixedSize(s(20), s(20))
        device_btn.setStyleSheet("background: transparent; border: none;")
        _set_hover_icon(device_btn, "headphones.svg", s(14))
        device_btn.setIconSize(QSize(s(14), s(14)))

        # Same circular design as the main player's prev/next buttons, just
        # sized down to fit the row -- radius must be passed explicitly
        # since it's half of this button's own size, not the main
        # controls' 32px one MediaSmallBtn otherwise defaults to.
        play_btn = QPushButton()
        play_btn.setFixedSize(s(26), s(26))
        play_btn.setStyleSheet(Theme.get_style("MediaSmallBtn", radius=s(13)))

        slider = ClickToPositionSlider(Qt.Orientation.Horizontal)
        slider.setFixedWidth(s(60))
        slider.setRange(0, 100)
        slider.setValue(app.get("volume", 100))
        slider.setStyleSheet(Theme.get_style("AppVolumeSlider"))
        slider.valueChanged.connect(lambda v, sl=slider: _sync_slider_extreme_state(sl))
        _sync_slider_extreme_state(slider)

        mute_btn = HoverIconButton()
        mute_btn.setFixedSize(s(20), s(20))
        mute_btn.setStyleSheet("background: transparent; border: none;")

        row_layout.addWidget(icon_lbl)
        row_layout.addLayout(info_col, 1)
        row_layout.addWidget(device_btn)
        row_layout.addWidget(play_btn)
        row_layout.addWidget(mute_btn)
        row_layout.addWidget(slider)
        # setVisible(True) on a widget with no parent WIDGET yet makes Qt
        # treat it as a real top-level window -- briefly a genuine, default-
        # chrome native HWND until it's reparented. info_col.addWidget()
        # alone doesn't parent subtitle_lbl to anything real: info_col is a
        # bare QLayout with no widget of its own until
        # row_layout.addLayout(info_col) above installs it onto row. Both
        # calls below must come after that point, never before.
        subtitle_lbl.setVisible(bool(app.get("now_playing")))
        play_btn.setVisible(bool(app.get("media_player")))
        self.app_volume_layout.addWidget(row)
        # A freshly created widget reports isVisible()==False until shown,
        # even once added to an already-visible parent's layout -- and
        # QLayout excludes invisible items from sizeHint() entirely, which
        # silently undercounted the drawer's required height for any row
        # added after the initial open.
        row.show()

        app_id = app["id"]
        slider.sliderReleased.connect(lambda aid=app_id: self._dispatch_app_volume(aid))
        mute_btn.clicked.connect(lambda checked=False, aid=app_id: self._toggle_app_mute_remote(aid))
        play_btn.clicked.connect(lambda checked=False, aid=app_id: self._toggle_app_playback_remote(aid))
        device_btn.clicked.connect(lambda checked=False, aid=app_id, btn=device_btn: self._show_output_device_menu(aid, btn))

        row_data = {
            "widget": row, "icon_lbl": icon_lbl, "name_lbl": name_lbl, "subtitle_lbl": subtitle_lbl,
            "play_btn": play_btn, "slider": slider, "mute_btn": mute_btn, "device_btn": device_btn,
            "muted": bool(app.get("muted", False)), "media_player": app.get("media_player"),
            "is_playing": bool(app.get("is_playing", True)),
        }
        self._set_app_mute_icon(row_data)
        self._set_app_play_icon(row_data)
        return row_data

    def _set_app_icon(self, icon_lbl, name):
        brand = _brand_icon_for_app(name)
        if brand:
            filename, hex_color = brand
            icon_lbl.setStyleSheet("background: transparent; border: none;")
            icon_lbl.setPixmap(Theme.get_icon(filename, s(20), hex_color).pixmap(s(20), s(20)))
        else:
            icon_lbl.setStyleSheet(Theme.get_style("AppIconPlaceholder"))
            icon_lbl.setPixmap(QPixmap())

    def _update_app_volume_row(self, row_data, app):
        row_data["name_lbl"].setText(_capitalize_first(app.get("name", "Unknown")))
        self._set_app_icon(row_data["icon_lbl"], app.get("name", ""))
        now_playing = app.get("now_playing", "")
        row_data["subtitle_lbl"].setText(now_playing)
        row_data["subtitle_lbl"].setVisible(bool(now_playing))
        # A media-session match can appear/disappear between refreshes
        # (e.g. a browser tab starts/stops playing something).
        row_data["media_player"] = app.get("media_player")
        row_data["play_btn"].setVisible(bool(row_data["media_player"]))
        row_data["is_playing"] = bool(app.get("is_playing", True))
        self._set_app_play_icon(row_data)
        # Never yank the slider out from under an in-progress user drag.
        if not row_data["slider"].isSliderDown():
            row_data["slider"].blockSignals(True)
            row_data["slider"].setValue(app.get("volume", row_data["slider"].value()))
            row_data["slider"].blockSignals(False)
            _sync_slider_extreme_state(row_data["slider"])
        row_data["muted"] = bool(app.get("muted", False))
        self._set_app_mute_icon(row_data)

    def _set_app_play_icon(self, row_data):
        icon_name = "pause.svg" if row_data["is_playing"] else "play.svg"
        row_data["play_btn"].setIcon(Theme.get_icon(icon_name, s(16), Theme.C_PRIMARY))
        row_data["play_btn"].setIconSize(QSize(s(16), s(16)))

    def _toggle_app_playback_remote(self, app_id):
        # Unlike the mute button, the resulting play/pause state isn't known
        # locally -- clTerminal.py re-publishes a fresh app_volumes list
        # after a successful toggle, which update_app_volumes() picks up to
        # refresh the icon (same pattern as _toggle_app_mute_remote).
        row_data = self.app_volume_rows.get(app_id)
        if row_data is None or not row_data.get("media_player"):
            return
        try:
            self.router.dispatch("terminal.app_volume", action="toggle_app_playback", target=row_data["media_player"], silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _set_app_mute_icon(self, row_data):
        icon_name = "speaker_mute.svg" if row_data["muted"] else "speaker.svg"
        _set_hover_icon(row_data["mute_btn"], icon_name, s(14))
        row_data["mute_btn"].setIconSize(QSize(s(14), s(14)))

    def _show_output_device_menu(self, app_id, anchor_btn):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        from PyQt6.QtCore import QPoint

        menu = QMenu(anchor_btn)
        menu.setStyleSheet(Theme.get_style("SettingsMenu"))

        default_action = QAction("System Default", menu)
        default_action.triggered.connect(lambda checked=False, aid=app_id: self._dispatch_app_output_device(aid, ""))
        menu.addAction(default_action)

        if self._output_devices:
            menu.addSeparator()
            for device in self._output_devices:
                action = QAction(device.get("name", "Unknown device"), menu)
                action.triggered.connect(
                    lambda checked=False, aid=app_id, did=device.get("id", ""): self._dispatch_app_output_device(aid, did)
                )
                menu.addAction(action)

        menu.exec(anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height())))

    def _dispatch_app_output_device(self, app_id, device_id):
        try:
            self.router.dispatch("terminal.app_volume", action="set_app_output_device", target=app_id, device_id=device_id, silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _dispatch_app_volume(self, app_id):
        row_data = self.app_volume_rows.get(app_id)
        if row_data is None:
            return
        try:
            self.router.dispatch("terminal.app_volume", action="set_app_volume", target=app_id, level=row_data["slider"].value(), silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _toggle_app_mute_remote(self, app_id):
        # Unlike the main Spotify mute button, the resulting mute state
        # isn't known locally -- clTerminal.py re-publishes a fresh
        # app_volumes list after a successful toggle, which update_app_
        # volumes() picks up to refresh the icon.
        try:
            self.router.dispatch("terminal.app_volume", action="toggle_app_mute", target=app_id, silent=True)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self.window(), 'is_fullscreen', False):
            self.send_cmd("status", silent=True)
        # Resyncs this instance's own button icon with the real, shared
        # state on LyricsDisplay -- there are two MediaWidget instances
        # (this dashboard one and the calendar carousel's own copy), each
        # with their own local _lyrics_enabled mirror, so toggling one
        # doesn't otherwise update the other's icon until it's shown again.
        lyrics_display = getattr(self.window(), 'lyrics_display', None)
        if lyrics_display is not None:
            self._lyrics_enabled = lyrics_display._lyrics_enabled
            self._set_lyrics_toggle_icon()

    def _set_lyrics_toggle_icon(self):
        self.lyrics_toggle_btn.set_active(self._lyrics_enabled)

    def _toggle_lyrics(self):
        self._lyrics_enabled = not self._lyrics_enabled
        self._set_lyrics_toggle_icon()
        lyrics_display = getattr(self.window(), 'lyrics_display', None)
        if lyrics_display is not None:
            lyrics_display.set_enabled(self._lyrics_enabled)

    def toggle_optimistic(self):
        self.status = "Paused" if self.status == "Playing" else "Playing"
        self.play_btn.setIcon(Theme.get_icon("pause.svg" if self.status == "Playing" else "play.svg", 18, "#ffffff"))
        self.send_cmd("toggle", silent=True)

    def _tick(self):
        if self.status == "Playing" and self.duration > 0:
            self.position += 1.0
            if self.position >= self.duration:
                self.position = self.duration
                if self.isVisible() and getattr(self.window(), 'is_fullscreen', False):
                    if not getattr(self, '_waiting_for_status', False):
                        self._waiting_for_status = True
                        self.send_cmd("status", silent=True)
            self._update_time_label()

    def _update_time_label(self):
        def fmt_time(secs):
            m = int(secs // 60)
            s = int(secs % 60)
            return f"{m}:{s:02d}"
        self.position_lbl.setText(fmt_time(self.position))
        self.duration_lbl.setText(fmt_time(self.duration))
        progress = self.position / self.duration if self.duration > 0 else 0.0
        self.progress_bar.setValue(int(max(0.0, min(1.0, progress)) * 1000))

    def send_cmd(self, action, silent=False):
        try:
            self.router.dispatch("spotify.control", action=action, silent=silent)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def update_status(self, data):
        self._waiting_for_status = False
        title = data.get("title", "Unknown")
        artist = data.get("artist", "Unknown")
        self.position = data.get("position", 0.0)
        self.duration = data.get("duration", 0.0)
        self.status = data.get("status", "Paused")
        # Absent can_seek (e.g. a status built from the Web API) means "assume yes".
        self.progress_bar.set_seekable(data.get("can_seek") is not False)
        self._apply_incoming_modes(data)

        self.title_lbl.setText(title)
        self.artist_lbl.setText(artist)

        self._update_time_label()
        self.play_btn.setIcon(Theme.get_icon("pause.svg" if self.status == "Playing" else "play.svg", 18, "#ffffff"))

        volume = data.get("volume")
        if isinstance(volume, int) and not self.volume_slider.isSliderDown():
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(max(0, min(100, volume)))
            self.volume_slider.blockSignals(False)
            _sync_slider_extreme_state(self.volume_slider)
            _set_hover_icon(self.mute_btn, "speaker_mute.svg" if volume == 0 else "speaker.svg", s(16))
            if volume > 0:
                self._last_nonzero_volume = volume

    # --- SHUFFLE / REPEAT / SEEK ---
    _REPEAT_CYCLE = {"off": "context", "context": "track", "track": "off"}
    _REPEAT_ICONS = {"off": "repeat.svg", "context": "repeat.svg", "track": "repeat_one.svg"}
    _MODE_PENDING_MS = 2500

    def _dispatch_control(self, **payload):
        try:
            self.router.dispatch("spotify.control", silent=True, **payload)
        except Exception as e:
            logging.error(f"MQTT Publish failed: {e}")

    def _show_modes(self, shuffle=None, repeat=None):
        """Reflects the player's shuffle/repeat on their buttons; None leaves that one alone."""
        if shuffle is not None:
            self._shuffle_on = bool(shuffle)
            self.shuffle_btn.set_active(self._shuffle_on)
        if repeat in self._REPEAT_CYCLE:
            self._repeat_mode = repeat
            self.repeat_btn.set_icon_name(self._REPEAT_ICONS[repeat])
            self.repeat_btn.set_active(repeat != "off")
            self.repeat_btn.setToolTip(f"Repeat: {repeat}")

    def _expect_mode(self, key, value, button):
        # Until the player confirms (its next status carries this value), a
        # status still showing the old value must not flip the button back.
        self._pending_modes[key] = value
        button.set_pending(True)
        self._mode_pending_timer.start(self._MODE_PENDING_MS)

    def _clear_mode_pending(self):
        """Gives up waiting for a confirmation; the next status shows the truth."""
        self._pending_modes.clear()
        self.shuffle_btn.set_pending(False)
        self.repeat_btn.set_pending(False)

    def _apply_incoming_modes(self, data):
        buttons = {"shuffle": self.shuffle_btn, "repeat": self.repeat_btn}
        for key, button in buttons.items():
            value = data.get(key)
            if value is None:
                continue
            if key in self._pending_modes:
                if value != self._pending_modes[key]:
                    continue  # stale reading from before our click landed
                del self._pending_modes[key]
                button.set_pending(False)
            self._show_modes(**{key: value})

    def _toggle_shuffle(self):
        enabled = not self._shuffle_on
        self._show_modes(shuffle=enabled)
        self._expect_mode("shuffle", enabled, self.shuffle_btn)
        self._dispatch_control(action="shuffle", enabled=enabled)

    def _cycle_repeat(self):
        mode = self._REPEAT_CYCLE[self._repeat_mode]
        self._show_modes(repeat=mode)
        self._expect_mode("repeat", mode, self.repeat_btn)
        self._dispatch_control(action="repeat", mode=mode)

    def _on_seek_requested(self, fraction):
        if self.duration <= 0:
            return
        self.position = fraction * self.duration
        self._update_time_label()
        self._dispatch_control(action="seek", position_ms=int(self.position * 1000))

    # --- UP NEXT ---
    QUEUE_VISIBLE_ROWS = 5

    def _request_queue(self):
        # Free when clSpotify.py already follows the queue locally; only the
        # first request after it (re)starts costs one Web API call.
        self.send_cmd("queue", silent=True)

    def _refresh_queue(self):
        self._set_queue_refreshing(True)
        self.send_cmd("queue_refresh", silent=True)
        self._queue_refresh_timer.start(4000)

    def _end_queue_refresh(self):
        self._set_queue_refreshing(False)

    def _set_queue_refreshing(self, refreshing):
        self._queue_refreshing = refreshing
        self.queue_rows_opacity.setOpacity(0.3 if refreshing else 1.0)
        if refreshing:
            self.queue_caption_lbl.setText("refreshing from Spotify...")
        elif not self._queue_loaded:
            self.queue_caption_lbl.setText("Loading...")
        elif self._queue_total:
            self.queue_caption_lbl.setText(f"{self._queue_total} tracks, followed locally")
        else:
            self.queue_caption_lbl.setText("Nothing queued.")

    def update_queue(self, data):
        """Rebuilds the Up Next rows from jarvis/sys/spotify_queue (see
        clSpotify.py's queue_payload). Called for every message, open or not,
        so it's ready the moment the drawer opens."""
        items = data.get("queue", [])
        self._queue_loaded = True
        self._queue_total = data.get("remaining", len(items))

        while self.queue_rows_layout.count():
            row = self.queue_rows_layout.takeAt(0).widget()
            if row is not None:
                row.setParent(None)
                row.deleteLater()
        for index, item in enumerate(items[:self.QUEUE_VISIBLE_ROWS], start=1):
            row = self._create_queue_row(index, item)
            self.queue_rows_layout.addWidget(row)
            # Same as the App Volume rows: a fresh widget reports invisible
            # until shown, and QLayout leaves invisible items out of sizeHint().
            row.show()

        self._queue_refresh_timer.stop()
        self._set_queue_refreshing(False)
        self._sync_app_volume_drawer_size()

    def _create_queue_row(self, index, item):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(s(8))

        number_lbl = QLabel(str(index))
        number_lbl.setFixedWidth(s(20))
        number_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number_lbl.setStyleSheet(Theme.get_style("DimLabel"))

        title_lbl = MarqueeLabel(item.get("title") or "Unknown", force_single_line=True)
        title_lbl.setStyleSheet(f"color: {Theme.C_TEXT_TITLE}; font-size: {Theme.F_NORMAL}; font-weight: bold; border: none; background: transparent;")
        artist_lbl = MarqueeLabel(item.get("artist") or "", force_single_line=True)
        artist_lbl.setStyleSheet(Theme.get_style("DimLabel"))
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(0)
        info_col.addWidget(title_lbl)
        info_col.addWidget(artist_lbl)

        duration_lbl = QLabel(_fmt_time((item.get("duration_ms") or 0) / 1000.0))
        duration_lbl.setMinimumWidth(s(32))
        duration_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        duration_lbl.setStyleSheet(Theme.get_style("DimLabel"))

        row_layout.addWidget(number_lbl)
        row_layout.addLayout(info_col, 1)
        if index == 1:
            next_lbl = QLabel("NEXT")
            next_lbl.setStyleSheet(f"color: {Theme.C_PRIMARY}; font-size: 10px; letter-spacing: 1px; border: 1px solid rgba(255,140,0,128); border-radius: 9px; padding: 1px 7px; background: transparent;")
            row_layout.addWidget(next_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        row_layout.addWidget(duration_lbl)
        return row

    def get_standalone_min_size(self):
        w = self.minimumSizeHint().width()
        h = self.minimumSizeHint().height()
        return max(200, w), max(120, h)
