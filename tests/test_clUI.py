import pytest
import sys
import os
import json
import subprocess
import re
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def fake_state_file(tmp_path):
    return str(tmp_path / "ui_state.json")


class TestUiStateRestoreOnStartup:
    """A full ecosystem reboot tears down and respawns clUI.py entirely
    (clJarvis.py's stop_native explicitly saves state via jarvis/sys/ui_control
    before killing it). Without restoring is_fullscreen at startup, every
    reboot silently dropped the dashboard back to the small overlay widget
    regardless of what the user had open before. But a genuine cold ecosystem
    start must always open in overlay regardless of what was saved from the
    previous session -- clJarvis.py's start_native() sets JARVIS_REBOOT=1 to
    distinguish a respawn-within-an-active-session (reboot, crash recovery,
    single-module restart) from a true cold start.

    MqttThread.start is mocked in every test here -- JarvisUI() otherwise
    spins up a real QThread that opens a real MQTT connection, which must
    never happen from an automated test (a real ecosystem, this machine's
    own, may already be running and using that same broker)."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_restores_fullscreen_on_reboot_when_that_was_the_saved_mode(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_called_once_with("set_fullscreen")

    def test_stays_in_overlay_on_reboot_when_that_was_the_saved_mode(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_cold_start_always_opens_in_overlay_even_if_fullscreen_was_saved(self, qapp, fake_state_file, mocker):
        """The actual feature requested: a genuine cold ecosystem start
        (JARVIS_REBOOT absent/"0") must never restore fullscreen, even if
        the previous session ended in fullscreen mode."""
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_cold_start_with_no_reboot_env_var_at_all_also_stays_in_overlay(self, qapp, fake_state_file, mocker):
        """Running clUI.py standalone (e.g. for debugging) never sets
        JARVIS_REBOOT at all -- must behave like a cold start, not a reboot."""
        import clUI
        mocker.patch.dict(os.environ, {}, clear=False)
        os.environ.pop("JARVIS_REBOOT", None)
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": True, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_no_saved_state_file_does_not_crash_or_enter_fullscreen(self, qapp, tmp_path, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        missing_path = str(tmp_path / "does_not_exist.json")

        with patch.object(clUI, "STATE_FILE", missing_path), \
             patch.object(clUI.JarvisUI, "set_ui_mode") as mock_set_mode:
            clUI.JarvisUI()

        mock_set_mode.assert_not_called()

    def test_cold_start_does_not_restore_dashboard_widgets_even_if_saved_visible_and_pinned(self, qapp, fake_state_file, mocker):
        """A genuine cold start (JARVIS_REBOOT unset/'0') must stay clean --
        restoring a saved-visible widget here would pop it onto the overlay,
        and since is_fullscreen is still False at __init__ time, spawn_widget's
        overlay path would force it unpinned regardless of what was saved
        (the 'settings widget always opens unpinned on overlay' bug)."""
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": True, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "active_widgets": {
                    "widget_settings": {"visible": True, "pos": [100, 100], "size": [364, 424], "is_unpinned": False}
                }
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        assert "widget_settings" not in ui.active_widgets

    def test_reboot_into_fullscreen_restores_a_saved_pinned_widget_as_pinned(self, qapp, fake_state_file, mocker):
        """The restore loop's is_unpinned sync used to be one-directional
        (only ever forcing unpinned), so a widget saved as pinned stayed
        stuck unpinned forever once an overlay-mode spawn had forced it True."""
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": True, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "active_widgets": {
                    "widget_settings": {"visible": True, "pos": [100, 100], "size": [364, 424], "is_unpinned": False}
                }
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        assert "widget_settings" in ui.active_widgets
        assert ui.active_widgets["widget_settings"].is_unpinned is False


class TestOverlayIdleWidgetCleanup:
    """set_state('IDLE') closes floating widgets while in overlay mode --
    fires naturally any time nothing is actively speaking/listening/
    processing, which happens briefly on almost every turn. An options
    prompt (list_-prefixed) must survive that, or it gets closed moments
    after appearing, before the user can act on it."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_options_widget_survives_idle_state_in_overlay_mode(self, qapp, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        mocker.patch.object(ui, "save_ui_state")

        options_widget = MagicMock()
        other_widget = MagicMock()
        ui.active_widgets = {"list_choose_a_track": options_widget, "widget_media_controls": other_widget}

        ui.set_state("IDLE")

        options_widget.hide.assert_not_called()
        other_widget.hide.assert_called_once()


class TestLoadRecoloredSvgIcon:
    """Audio pill icons are hand-authored SVGs (vector, rendered by Qt
    itself) rather than emoji/font glyphs, specifically so they look
    identical on both machines regardless of installed fonts/emoji sets,
    and so the theme color can be applied directly instead of depending on
    a font glyph respecting text color at all."""

    def test_fill_color_is_replaced_with_the_requested_color(self, qapp, tmp_path):
        import clUI
        svg_path = tmp_path / "test_icon.svg"
        svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                             '<path fill="#000000" d="M0 0h24v24H0z"/></svg>')

        icon = clUI.load_recolored_svg_icon(str(svg_path), "#ffaa00", 24)

        assert not icon.isNull()

    def test_multiple_fill_occurrences_all_get_replaced(self, qapp, tmp_path):
        import clUI
        svg_path = tmp_path / "test_icon.svg"
        svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                             '<path fill="#123456" d="M0 0h10v10H0z"/>'
                             '<path fill="#abcdef" d="M10 10h10v10H10z"/></svg>')

        # Doesn't raise, and both paths' fills were substitutable (same
        # regex, applied to the whole file) -- rendering succeeding at all
        # confirms the substitution didn't corrupt the SVG's XML structure.
        icon = clUI.load_recolored_svg_icon(str(svg_path), "#ffaa00", 24)

        assert not icon.isNull()


class TestAudioQuickSwitchPill:
    """The dashboard's mic/speaker quick-switch pills read the active
    device from core.json. Collapsed, they show only the icon (a circle);
    hovering expands them into a pill revealing the device name, and
    clicking (icon or expanded area) persists+dispatches a selection the
    same way Settings' Audio tab does."""

    def _pill(self, qapp, kind, current_device, mocker, grow_direction="right"):
        import clUI
        mocker.patch.object(clUI.AudioQuickSwitchPill, "_current_device", return_value=current_device)
        # Real hardware enumeration (pycaw/COM) isn't relevant to these tests
        # and is slow -- stub both the raw listing and the display-name
        # cleanup (an identity map: display == actual name is fine here).
        mocker.patch.object(clUI.AudioQuickSwitchPill, "_enumerate_options", return_value=[current_device])
        mocker.patch("utils.clAudioDevices.get_clean_display_names", return_value={current_device: current_device})
        pill = clUI.AudioQuickSwitchPill(kind, grow_direction=grow_direction)
        return pill

    def test_collapsed_icon_shows_the_svg_icon_not_the_device_name(self, qapp, mocker):
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        assert not pill.icon_btn.icon().isNull()
        assert pill.icon_btn.text() == ""

    def test_collapsed_label_is_hidden(self, qapp, mocker):
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        assert pill.label.isHidden()
        assert pill.width() == pill.diameter

    def test_hover_reveals_device_name_in_label(self, qapp, mocker):
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)

        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()  # simulates the hover-intent delay elapsing

        assert not pill.label.isHidden()
        assert pill.label.text() == "USB Mic"

    def test_leaving_collapses_back_and_hides_label(self, qapp, mocker):
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QEvent, QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()  # simulates the hover-intent delay elapsing

        pill.leaveEvent(QEvent(QEvent.Type.Leave))
        pill._anim.setCurrentTime(pill._anim.duration())  # fast-forward the collapse animation

        assert pill.width() == pill.diameter
        assert pill.label.isHidden()

    def test_tooltip_on_icon_carries_full_device_name(self, qapp, mocker):
        pill = self._pill(qapp, "output", "Speakers (Realtek Audio)", mocker)
        assert pill.icon_btn.toolTip() == "Speaker: Speakers (Realtek Audio)"

    def test_quick_pass_through_never_expands(self, qapp, mocker):
        """A cursor sweeping across several pills in a row used to fully
        expand-then-collapse each one it merely passed over, cascading into
        a whole-row recenter for every one -- the actual expand now waits
        for the hover-delay timer, so leaving before it fires must cancel
        it outright rather than starting (and then immediately reversing)
        the width animation."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QEvent, QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        collapsed_width = pill.width()

        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        assert pill._hover_delay_timer.isActive()

        pill.leaveEvent(QEvent(QEvent.Type.Leave))

        assert not pill._hover_delay_timer.isActive()
        assert pill.width() == collapsed_width
        assert pill.label.isHidden()

    def test_stuck_mid_animation_gets_corrected_by_the_safety_net(self, qapp, mocker):
        """Rapid hover/leave cycles across the dock row (each one moving
        every sibling pill via _reflow_widget_dock) can spuriously
        re-trigger enter/leaveEvent on a pill the cursor is sliding under,
        interrupting its own animation before finished() ever fires and
        leaving it stuck part-expanded with the label visibly stuck open.
        _settle_animation (scheduled after every transition) must force it
        back to whatever the pill's CURRENT hover state actually calls
        for."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()

        # Simulate the animation being interrupted mid-flight (a spurious
        # reflow-triggered leave/enter) so it never reaches its own
        # finished() signal, leaving it part-expanded with the label shown.
        pill._anim.setCurrentTime(pill._anim.duration() // 2)
        pill._anim.stop()
        assert pill.diameter < pill.width() < pill.expanded_width
        assert not pill.label.isHidden()

        # By the time the safety net fires, the mouse has actually left.
        mocker.patch.object(pill, "underMouse", return_value=False)
        pill._settle_animation(pill._anim_generation)

        assert pill.width() == pill.diameter
        assert pill.label.isHidden()

    def test_stale_safety_net_does_not_cut_short_a_newer_animation(self, qapp, mocker):
        """A safety net scheduled for a since-superseded transition must
        not snap a still-legitimate newer animation short -- only the
        latest one (matching the current _anim_generation) may act."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()
        stale_generation = pill._anim_generation

        pill._anim.setCurrentTime(pill._anim.duration() // 2)
        mid_width = pill.width()
        pill._animate_to(pill.diameter)  # a newer, still-legitimate transition

        pill._settle_animation(stale_generation)  # the OLD (superseded) safety net firing late

        assert pill.width() == mid_width, "a stale safety net must not touch a newer animation"

    def test_set_sizes_mid_hover_does_not_leave_the_label_stuck_visible(self, qapp, mocker):
        """set_sizes() (called on every refresh_layout(), e.g. a window
        resize) used to reset the width via setFixedWidth() directly,
        bypassing the label hide/reset logic entirely -- a resize while a
        pill happened to be mid-hover snapped it back to a collapsed
        circle with the label still shown, stuck, since nothing ever told
        it to hide. setPillWidth's hide check must fire no matter which
        caller changes the width, not just a completed animation."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)
        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()
        pill._anim.setCurrentTime(pill._anim.duration())
        assert not pill.label.isHidden()

        pill.set_sizes(pill.diameter, pill.expanded_width)

        assert pill.width() == pill.diameter
        assert pill.label.isHidden(), "label must not stay visible once set_sizes() snaps back to collapsed"

    def test_expand_first_frame_does_not_immediately_hide_the_label(self, qapp, mocker):
        """An expand animation starts AT the collapsed diameter and grows
        from there -- the width-change hide-check must key off intent
        (_target_expanded), not instantaneous width <= diameter, or every
        expand's own first frame would immediately undo its own show()."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        pill = self._pill(qapp, "input", "USB Mic", mocker)

        pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        pill._begin_expand()

        assert not pill.label.isHidden(), "the label must survive the expand animation's own first frame"

    def test_grow_left_keeps_right_edge_anchored_while_expanding(self, qapp, mocker):
        pill = self._pill(qapp, "input", "USB Mic", mocker, grow_direction="left")
        pill.set_sizes(35, 140)
        pill.set_anchor(500, 100)
        right_edge_before = pill.x() + pill.width()

        pill.setPillWidth(pill.expanded_width)

        assert pill.x() + pill.width() == right_edge_before

    def test_grow_right_keeps_left_edge_anchored_while_expanding(self, qapp, mocker):
        pill = self._pill(qapp, "output", "USB Mic", mocker, grow_direction="right")
        pill.set_sizes(35, 140)
        pill.set_anchor(500, 100)
        left_edge_before = pill.x()

        pill.setPillWidth(pill.expanded_width)

        assert pill.x() == left_edge_before

    def test_select_persists_and_dispatches_for_input(self, qapp, mocker):
        import clUI
        pill = self._pill(qapp, "input", "System Default", mocker)
        mock_update = mocker.patch.object(pill.loader, "update_json_atomic")
        mock_dispatch = mocker.patch.object(pill.router, "dispatch")

        pill._select("USB Mic")

        cb = mock_update.call_args.args[1]
        core = {}
        cb(core)
        assert core["settings"]["audio_settings"]["input_device"] == "USB Mic"
        mock_dispatch.assert_called_once_with("mic.state", action="set_input_device", device_name="USB Mic")

    def test_select_persists_and_dispatches_for_output(self, qapp, mocker):
        pill = self._pill(qapp, "output", "System Default", mocker)
        mock_update = mocker.patch.object(pill.loader, "update_json_atomic")
        mock_dispatch = mocker.patch.object(pill.router, "dispatch")

        pill._select("Speakers (Realtek Audio)")

        cb = mock_update.call_args.args[1]
        core = {}
        cb(core)
        assert core["settings"]["audio_settings"]["output_device"] == "Speakers (Realtek Audio)"
        mock_dispatch.assert_called_once_with("tts.control", action="set_output_device", device_name="Speakers (Realtek Audio)")


class TestWidgetDockRow:
    """The widget-toggle pills (Music/Lights/.../Debug) sit in one
    horizontal row centered in the left margin next to the text bar, each
    only owning its own hover-expand width rather than a fixed screen
    anchor -- _reflow_widget_dock() re-centers the whole row around a
    fixed midpoint on every width change (collapsed layout AND every frame
    of any pill's hover animation) so the group never drifts as one pill
    grows or shrinks."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def _ui(self, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        ui.resize(1920, 1080)
        ui.refresh_layout()
        return ui

    def _midpoint(self, pills):
        left = min(p.x() for p in pills)
        right = max(p.x() + p.width() for p in pills)
        return (left + right) // 2

    def test_collapsed_row_is_centered_and_level(self, qapp, mocker):
        ui = self._ui(mocker)
        pills = ui.widget_toggle_pills

        assert len(set(p.y() for p in pills)) == 1, "all pills must sit on the same row"
        assert self._midpoint(pills) == ui._widget_dock_center_x

    def test_row_stays_centered_while_one_pill_is_hover_expanded(self, qapp, mocker):
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        ui = self._ui(mocker)
        pills = ui.widget_toggle_pills
        for p in pills:
            p.show()

        hovered = pills[3]
        collapsed_width = hovered.width()
        hovered.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        hovered._begin_expand()  # simulates the hover-intent delay elapsing
        hovered._anim.setCurrentTime(hovered._anim.duration())

        assert hovered.width() > collapsed_width, "hovered pill must actually expand for this test to mean anything"
        assert self._midpoint(pills) == ui._widget_dock_center_x
        assert len(set(p.y() for p in pills)) == 1

    def test_short_label_keeps_the_same_icon_label_gap_as_a_longer_one(self, qapp, mocker):
        """MarqueeLabel.minimumSizeHint() always reports 50px regardless of
        content -- a short word like 'Music' measures narrower than that,
        so sizing the pill to the raw text width undershot what the label
        actually needs, and Qt's layout quietly ate the difference out of
        the icon-label spacing instead (2px instead of 6px), making the
        text start right against the icon."""
        from PyQt6.QtGui import QEnterEvent
        from PyQt6.QtCore import QPointF
        ui = self._ui(mocker)
        ui.show()  # internal icon/label geometry only resolves once actually shown
        qapp.processEvents()

        for pill, label in [(ui.btn_media, "Music"), (ui.btn_reminders, "Reminders")]:
            assert pill._widget_label == label
            pill.show()
            qapp.processEvents()
            pill.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
            pill._begin_expand()  # simulates the hover-intent delay elapsing
            pill._anim.setCurrentTime(pill._anim.duration())
            qapp.processEvents()

            gap = pill.label.x() - (pill.icon_btn.x() + pill.icon_btn.width())
            assert gap == pill._layout_spacing, f"{label}: expected {pill._layout_spacing}px icon-label gap, got {gap}px"


class TestLoadUiStateClampsStaleUnpinnedPosition:
    """A live machine's ui_state.json had every widget saved with an
    identical pos ([3569, 1641], well outside its actual 1920x1080 monitor)
    and is_unpinned: true, left over from testing on a different monitor
    arrangement earlier. Restoring it spawned each widget correctly (visible,
    updating on MQTT) but toggle_pin()'s unpin branch converts an
    already-clamped local position into a global one via mapToGlobal(),
    which reproduced the same off-screen position -- so the widget was
    genuinely there, just rendered outside any real screen ("logs show
    it updating, but not showing"). The restore loop must clamp the
    post-unpin global position back onto the current screen too."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_stale_offscreen_unpinned_widget_is_clamped_back_onto_screen(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": True, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "active_widgets": {
                    "widget_todo_list": {"visible": True, "pos": [3569, 1641], "size": [230, 100], "is_unpinned": True}
                }
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        w = ui.active_widgets["widget_todo_list"]
        screen_geom = ui.screen().geometry()

        assert w.is_unpinned is True
        assert not w.isHidden()
        assert screen_geom.x() <= w.x() <= screen_geom.x() + screen_geom.width() - 50
        assert screen_geom.y() <= w.y() <= screen_geom.y() + screen_geom.height() - 50


class TestLoadUiStateDoesNotClobberVisibilityMidRestore:
    """A reboot with saved is_fullscreen=True runs load_ui_state() twice:
    once in __init__ (while is_fullscreen is still False, pre-transition)
    and once more inside set_ui_mode('set_fullscreen'). The first pass's
    spawn_widget() call used to immediately call save_ui_state(), persisting
    an incomplete, still-in-overlay snapshot (widget just spawned, not yet
    hidden/shown per its real saved state) to disk -- clobbering the
    original 'visible: true' before the second pass ever got to read it, so
    a widget saved visible came back permanently hidden after a reboot."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_widget_saved_visible_stays_visible_after_reboot_into_fullscreen(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": True, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "active_widgets": {
                    "widget_todo_list": {"visible": True, "pos": [500, 400], "size": [230, 100], "is_unpinned": False}
                }
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        w = ui.active_widgets["widget_todo_list"]
        assert not w.isHidden()

    def test_restoring_flag_is_reset_after_a_successful_restore(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        assert getattr(ui, "_restoring_ui_state", False) is False


class TestFullscreenSwitchPreservesLiveWidgetState:
    """set_ui_mode('set_fullscreen') calls load_ui_state() on every manual
    overlay -> fullscreen switch, not just at boot. A widget already alive
    in memory (e.g. hidden a moment ago by set_overlay) must keep its live
    show/hide state -- previously, load_ui_state() re-applied the stale
    on-disk snapshot (typically 'everything hidden', saved during that same
    overlay transition) onto it, so switching overlay -> fullscreen made
    every previously-open widget disappear rather than reappear."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_already_visible_widget_survives_a_second_load_ui_state_pass(self, qapp, fake_state_file, mocker):
        import clUI
        from PyQt6.QtWidgets import QLabel
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.is_fullscreen = True
            ui.spawn_widget("widget_todo_list", "To-Do List", QLabel("content"))
            w = ui.active_widgets["widget_todo_list"]
            assert not w.isHidden()

            # Simulate the on-disk snapshot set_overlay() would have just saved
            # (everything hidden) landing in the SAME file this widget was
            # never part of -- then re-enter fullscreen, which re-reads it.
            with open(fake_state_file, "w") as f:
                json.dump({
                    "is_fullscreen": False, "current_monitor_idx": 0,
                    "active_widgets": {"widget_todo_list": {"visible": False, "pos": [10, 10], "size": [50, 50], "is_unpinned": False}}
                }, f)
            ui.load_ui_state()

        assert not w.isHidden(), "an already-live widget must not be re-hidden by a stale on-disk snapshot"


class TestOverlaySwitchHidesDebugButton:
    """set_overlay's hide-list was missing btn_debug -- set_fullscreen shows
    it whenever ECOSYSTEM_STATE is 'debug', but switching back to overlay
    never hid it again, leaving it drawn on top of the small idle overlay
    window (only noticed once it got a distinct icon)."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_btn_debug_hidden_after_switching_to_overlay(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.object(clUI, "ECOSYSTEM_STATE", "debug")
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.set_ui_mode("set_fullscreen")
            assert not ui.btn_debug.isHidden()

            ui.set_ui_mode("set_overlay")
            assert ui.btn_debug.isHidden()


class TestSpawnWidgetMainWindowReference:
    """spawn_widget()'s overlay-mode ('standalone') branch used to construct
    DraggableWidget with parent=None, which also left main_window (captured
    once at construction and never updated again) permanently None. A later
    re-pin -- e.g. after switching overlay -> fullscreen and clicking the pin
    button -- relies on main_window to know where to reparent into; without
    it, toggle_pin() left the widget parentless with Widget-only flags, and
    Windows drew its full default decorated chrome back onto it (the
    'title bar came back' bug)."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_standalone_spawned_widget_keeps_a_real_main_window_reference(self, qapp, mocker):
        import clUI
        from PyQt6.QtWidgets import QLabel
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        mocker.patch.object(ui, "save_ui_state")
        ui.is_fullscreen = False

        ui.spawn_widget("widget_test", "Test", QLabel("content"))
        wrapper = ui.active_widgets["widget_test"]

        assert wrapper.main_window is ui
        assert wrapper.parent() is None  # correctly detached to top-level for overlay display

        wrapper.toggle_pin(force_unpin=False)  # simulate switching to fullscreen + clicking pin

        assert wrapper.parent() is ui
        assert wrapper.isWindow() is False


@pytest.mark.skip(reason="Interactive GUI test")
def test_expose():
    os.environ["QT_QPA_PLATFORM"] = "xcb" # same as Jarvis
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel
    from PyQt6.QtCore import QTimer

    app = QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    w.setGeometry(100, 100, 400, 400)
    l = QLabel("Test Expose", w)
    w.show()

    def check_expose():
        wh = w.windowHandle()
        exposed = wh.isExposed() if wh else False
        print(f"Is exposed? {exposed}")
        sys.stdout.flush()

    t = QTimer()
    t.timeout.connect(check_expose)
    t.start(1000)

    QTimer.singleShot(5000, app.quit)
    sys.exit(app.exec())

def test_obs():
    def check_obs():
        my_pid = os.getpid()
        
        out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
        match = re.search(r'#\s*(.*)', out)
        if not match: 
            return "No stack match"
            
        win_ids = [w.strip() for w in match.group(1).split(',')]
        
        jarvis_idx = -1
        for i, wid_str in enumerate(win_ids):
            name = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_NAME")
            if "clUI.py" in name:
                jarvis_idx = i
                break
                
        if jarvis_idx == -1:
            return "Jarvis not found in stack"
            
        print(f"Jarvis is at index {jarvis_idx} of {len(win_ids)-1}")
        
        tx, ty, tw, th = (0, 0, 1920, 1080)
        
        for wid_str in win_ids[jarvis_idx+1:]:
            props = subprocess.getoutput(f"xprop -id {wid_str} _NET_WM_STATE _NET_WM_PID _NET_WM_NAME _NET_WM_WINDOW_TYPE")
            if "HIDDEN" in props:
                print(f"Ignoring {wid_str}: HIDDEN")
                continue
                
            pid_match = re.search(r'_NET_WM_PID.*?=\s*(\d+)', props)
            if pid_match:
                pid = int(pid_match.group(1))
                print(f"Window {wid_str} has PID {pid}")
                
            geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {wid_str}")
            pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
            geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
            
            name_match = re.search(r'_NET_WM_NAME.*?=\s*"(.*?)"', props)
            name = name_match.group(1) if name_match else "Unknown"
            
            if pos_match and geom_match:
                wx, wy = int(pos_match.group(1)), int(pos_match.group(2))
                ww, wh = int(geom_match.group(1)), int(geom_match.group(2))
                print(f"Checking window {wid_str} ({name}): pos=({wx},{wy}), geom=({ww}x{wh})")
                if ww <= 1 or wh <= 1:
                    print(f"Ignoring {wid_str}: too small")
                    continue
                    
                if not (wx + ww <= tx or wx >= tx + tw or wy + wh <= ty or wy >= ty + th):
                    print(f"!!! OBSCURED BY {wid_str} ({name}) !!!")
                    return True
                    
        return False
    check_obs()

@pytest.mark.skip(reason="Interactive GUI test")
def test_vis():
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel
    from PyQt6.QtCore import QTimer
    
    app = QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    w.setGeometry(100, 100, 400, 400)
    l = QLabel("Test Window", w)
    w.show()

    def check_vis():
        print(f"Is active: {w.isActiveWindow()}, visibleRegion empty: {w.visibleRegion().isEmpty()}")
        sys.stdout.flush()

    t = QTimer()
    t.timeout.connect(check_vis)
    t.start(2000)

    QTimer.singleShot(6000, app.quit)
    sys.exit(app.exec())

def test_xdotool():
    def get_active_window_rect():
        try:
            active_win = subprocess.getoutput("xdotool getactivewindow").strip()
            if not active_win.isdigit(): return None
            geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {active_win}")
            
            pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
            geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
            if pos_match and geom_match:
                x, y = int(pos_match.group(1)), int(pos_match.group(2))
                w, h = int(geom_match.group(1)), int(geom_match.group(2))
                return (x, y, w, h)
        except Exception as e:
            print(e)
        return None
    print(get_active_window_rect())

def test_xlib():
    try:
        from Xlib import display, X, Xatom
        d = display.Display()
        root = d.screen().root
        NET_CLIENT_LIST_STACKING = d.intern_atom('_NET_CLIENT_LIST_STACKING')

        reply = root.get_full_property(NET_CLIENT_LIST_STACKING, X.AnyPropertyType)
        if reply:
            win_ids = reply.value
            print(f"Found {len(win_ids)} windows")
            for wid in win_ids[-5:]: # Check top 5 windows
                try:
                    w = d.create_resource_object('window', wid)
                    geom = w.get_geometry()
                    coords = w.translate_coords(root, 0, 0)
                    print(f"Window {wid}: geom ({geom.x}, {geom.y}, {geom.width}, {geom.height}), abs coords ({coords.x}, {coords.y})")
                except Exception as e:
                    print(f"Error on {wid}: {e}")
    except ImportError:
        pytest.skip("Xlib not installed")
    except Exception as e:
        pytest.skip(f"Xlib display error: {e}")

def test_zorder():
    def get_obscuring_windows(target_rect):
        try:
            out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
            match = re.search(r'#\s*(.*)', out)
            if not match: return []
            
            win_ids = [w.strip() for w in match.group(1).split(',')]
            
            obscuring = []
            for wid in win_ids:
                state = subprocess.getoutput(f"xprop -id {wid} _NET_WM_STATE")
                if "HIDDEN" in state:
                    continue
                    
                geom_str = subprocess.getoutput(f"xdotool getwindowgeometry {wid}")
                pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_str)
                geom_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_str)
                if pos_match and geom_match:
                    x, y = int(pos_match.group(1)), int(pos_match.group(2))
                    w, h = int(geom_match.group(1)), int(geom_match.group(2))
                    
                    if not (x + w <= target_rect[0] or x >= target_rect[0] + target_rect[2] or 
                            y + h <= target_rect[1] or y >= target_rect[1] + target_rect[3]):
                        obscuring.append(wid)
            return obscuring
        except Exception as e:
            print(e)
            return []
    print(get_obscuring_windows((0,0, 1920,1080)))

def test_types():
    out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
    match = re.search(r'#\s*(.*)', out)
    if match:
        win_ids = [w.strip() for w in match.group(1).split(',')]
        for wid in win_ids:
            props = subprocess.getoutput(f"xprop -id {wid} _NET_WM_WINDOW_TYPE _NET_WM_NAME")
            print(f"{wid}: {props}")

def test_early():
    out = subprocess.getoutput("xprop -root _NET_CLIENT_LIST_STACKING")
    match = re.search(r'#\s*(.*)', out)
    if match:
        win_ids = [w.strip() for w in match.group(1).split(',')]
        for wid in win_ids:
            name = subprocess.getoutput(f"xprop -id {wid} _NET_WM_NAME")
            if "clUI" in name:
                print(f"Found clUI.py with id {wid}")


class TestDraggableWidgetPinWindowChrome:
    """toggle_pin() used to call setParent() and setWindowFlags() as two
    separate calls. On Windows, Qt doesn't reliably drop the native title
    bar/min/max/close chrome unless the parent and window flags change in
    one atomic setParent(parent, flags) call, so an 'unpinned' widget could
    render as a full OS window titled 'python3' instead of the intended
    frameless floating panel."""

    def test_unpin_reparents_and_reflags_atomically(self, qapp):
        from clUI import DraggableWidget
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtCore import Qt

        widget = DraggableWidget("test_widget", "Test", QLabel("content"))
        calls = []
        widget.setParent = lambda *a, **kw: calls.append(a)

        widget.toggle_pin(force_unpin=True)

        assert len(calls) == 1, "setParent must be called exactly once, not split into setParent()+setWindowFlags()"
        parent_arg, flags_arg = calls[0]
        assert parent_arg is None
        assert flags_arg & Qt.WindowType.FramelessWindowHint
        assert flags_arg & Qt.WindowType.Tool

    def test_repin_reparents_and_reflags_atomically(self, qapp):
        from clUI import DraggableWidget
        from PyQt6.QtWidgets import QLabel, QWidget
        from PyQt6.QtCore import Qt

        main_window = QWidget()
        widget = DraggableWidget("test_widget", "Test", QLabel("content"), parent=main_window)
        widget.toggle_pin(force_unpin=True)  # start unpinned

        calls = []
        widget.setParent = lambda *a, **kw: calls.append(a)

        widget.toggle_pin(force_unpin=False)

        assert len(calls) == 1
        parent_arg, flags_arg = calls[0]
        assert parent_arg is main_window
        assert flags_arg == Qt.WindowType.Widget
