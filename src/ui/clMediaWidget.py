import logging
from clTheme import Theme
from utils.clActionRouter import ActionRouter
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QProgressBar
from PyQt6.QtCore import QTimer, QSize, Qt, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap
from clUIScaler import UIScaler
from ui.clMarqueeLabel import MarqueeLabel

def s(val):
    return UIScaler.get().scale(val)

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

        # Header Badge
        header_layout = QHBoxLayout()
        self.badge_lbl = QLabel("SPOTIFY PLAYER")
        self.badge_lbl.setStyleSheet(Theme.get_style("BadgeLabel"))
        header_layout.addWidget(self.badge_lbl)
        header_layout.addStretch()
        self.content_layout.addLayout(header_layout)

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
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(s(4))
        self.progress_bar.setStyleSheet(Theme.get_style("MediaProgressBar"))
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
        fader_layout.setContentsMargins(0, 0, 0, 0)
        fader_layout.setSpacing(s(6))

        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        # Fixed to exactly the room it has at the widget's own minimum size
        # (the natural title-to-progress-bar span, minus the mute button and
        # the spacing above it) -- measured, not guessed, so it fits snugly
        # against the top with no leftover gap at minimum size but never
        # grows taller as the widget itself grows (e.g. the App Volume
        # drawer opening); the stretch above it (see fader_layout.addStretch
        # below) absorbs any extra room instead.
        self.volume_slider.setFixedHeight(s(98))
        self.volume_slider.setStyleSheet(Theme.get_style("MediaVolumeSlider"))
        _sync_slider_extreme_state(self.volume_slider)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        # Dispatches only once the user releases the slider, not on every
        # value change while dragging -- avoids hammering the Spotify Web
        # API with one request per pixel.
        self.volume_slider.sliderReleased.connect(self._dispatch_volume)

        self.mute_btn = HoverIconButton()
        self.mute_btn.setFixedSize(s(22), s(22))
        _set_hover_icon(self.mute_btn, "speaker.svg", s(16))
        self.mute_btn.setIconSize(QSize(s(16), s(16)))
        self.mute_btn.setStyleSheet("background: transparent; border: none;")
        self.mute_btn.clicked.connect(self._toggle_mute)

        # Stretch first, un-stretched slider+mute below it -- pins both to
        # the bottom of fader_container with any extra height showing as
        # empty space above them, instead of the slider itself stretching.
        fader_layout.addStretch(1)
        fader_layout.addWidget(self.volume_slider, 0, alignment=Qt.AlignmentFlag.AlignHCenter)
        fader_layout.addWidget(self.mute_btn, 0, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Pairs the main content column with the fader as ordinary layout
        # siblings -- narrowing the column when the fader opens is just
        # normal reflow, and the fader's own (fixed) width never feeds back
        # into anything live.
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        content_row.addLayout(self.content_layout, 1)
        content_row.addWidget(self.fader_container, 0)
        self.layout.addLayout(content_row)

        # App Volume -- collapsible per-app volume/mute mixer, backed by
        # clTerminal.py's pycaw (Windows) / pactl (Linux) session list.
        self.app_volume_tab = QPushButton("App Volume  ▾")
        self.app_volume_tab.setStyleSheet(Theme.get_style("MediaAppVolumeTab"))
        self.app_volume_tab.clicked.connect(self._toggle_app_volume)
        self.layout.addWidget(self.app_volume_tab)

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

        self._fader_anim = QVariantAnimation(self)
        self._fader_anim.setDuration(200)
        self._fader_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fader_anim.valueChanged.connect(self._on_fader_width_changed)

        self.position = 0.0
        self.duration = 0.0
        self.status = "Paused"
        self._waiting_for_status = False

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
        if self.app_volume_body.isVisible():
            base_height -= self.app_volume_body.minimumSizeHint().height() + self.layout.spacing()

        min_height = base_height + self._app_volume_room()
        return QSize(base.width(), min_height)

    def _app_volume_room(self):
        # Height the open drawer needs, or 0 when closed. Layouts skip
        # spacing around a hidden widget, so showing app_volume_body also
        # opens up one more gap (between it and app_volume_tab) that wasn't
        # there before.
        if not self.app_volume_body.isVisible():
            return 0
        return self.app_volume_body.sizeHint().height() + self.layout.spacing()

    def update_scaling(self):
        self._base_right_margin = s(15)
        self.layout.setContentsMargins(s(15), s(10), self._base_right_margin, 0)
        self.layout.setSpacing(s(6))
        self.content_layout.setSpacing(s(6))
        self._fader_width = s(30)
        if self.fader_container.width() > 0:
            self.fader_container.setFixedWidth(self._fader_width)
        if hasattr(self, 'progress_bar'):
            self.progress_bar.setFixedHeight(s(4))
        if hasattr(self, 'controls_layout'):
            self.controls_layout.setSpacing(s(18))
        if hasattr(self, 'prev_btn'):
            self.prev_btn.setFixedSize(32, 32)
            self.play_btn.setFixedSize(40, 40)
            self.next_btn.setFixedSize(32, 32)
        if hasattr(self, 'volume_slider'):
            self.volume_slider.setFixedHeight(s(98))

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
        opening = not self.app_volume_body.isVisible()
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
            self.app_volume_body.setVisible(True)
            self.updateGeometry()
            if wrapper is not None:
                room = self._app_volume_room()
                self._app_volume_delta = room
                if room > 0:
                    wrapper.resize(wrapper.width(), height_before + room)
            self._request_app_volumes()
        else:
            self.app_volume_body.setVisible(False)
            self.updateGeometry()
            delta = getattr(self, '_app_volume_delta', 0)
            if wrapper is not None and delta > 0:
                wrapper.resize(wrapper.width(), height_before - delta)
            self._app_volume_delta = 0

    def _sync_app_volume_drawer_size(self):
        """Re-applies the app-volume drawer's height delta after its
        content changes while it's already open (e.g. the per-app list
        refreshing) -- grows/shrinks the wrapper by the difference from
        what was already added, not by the drawer's full new height, so
        repeated refreshes don't compound. Safe to read wrapper.height()
        live here (unlike _toggle_app_volume's open branch): no
        setVisible() call happens in this path, so there's no implicit
        Qt auto-grow to get contaminated by."""
        if not self.app_volume_body.isVisible():
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

        # Same circular design as the main player's prev/next buttons, just
        # sized down to fit the row -- radius must be passed explicitly
        # since it's half of this button's own size, not the main
        # controls' 32px one MediaSmallBtn otherwise defaults to.
        play_btn = QPushButton()
        play_btn.setFixedSize(s(26), s(26))
        play_btn.setStyleSheet(Theme.get_style("MediaSmallBtn", radius=s(13)))

        slider = QSlider(Qt.Orientation.Horizontal)
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

        row_data = {
            "widget": row, "icon_lbl": icon_lbl, "name_lbl": name_lbl, "subtitle_lbl": subtitle_lbl,
            "play_btn": play_btn, "slider": slider, "mute_btn": mute_btn,
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

    def get_standalone_min_size(self):
        w = self.minimumSizeHint().width()
        h = self.minimumSizeHint().height()
        return max(200, w), max(120, h)
