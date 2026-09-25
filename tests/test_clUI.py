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


class TestAppStateChangeDoesNotFightForFocus:
    """_on_app_state_changed reacts to QApplication going Active/Inactive --
    originally a Wayland-only fix for that platform's focus-stealing
    prevention spuriously marking the app Inactive. On Windows this handler
    has no such problem to compensate for, and unconditionally reactivating
    (activateWindow()/raise_() on text_input, a JarvisUI child) drags
    JarvisUI's whole top-level window to the front the instant the app
    becomes Active for ANY reason -- including simply focusing an unpinned
    dashboard widget, defeating the entire point of unpinning it."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def _ui(self, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        ui.is_fullscreen = True
        return ui

    def test_never_reactivates_on_windows_even_when_focus_was_lost(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.sys, "platform", "win32")
        ui = self._ui(mocker)
        mocker.patch.object(ui.text_input, "hasFocus", return_value=False)
        activate = mocker.patch.object(ui.text_input, "activateWindow")
        raise_ = mocker.patch.object(ui.text_input, "raise_")

        ui._on_app_state_changed(clUI.Qt.ApplicationState.ApplicationActive)

        activate.assert_not_called()
        raise_.assert_not_called()

    def test_does_not_reactivate_when_already_focused(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.sys, "platform", "linux")
        ui = self._ui(mocker)
        mocker.patch.object(ui.text_input, "hasFocus", return_value=True)
        activate = mocker.patch.object(ui.text_input, "activateWindow")

        ui._on_app_state_changed(clUI.Qt.ApplicationState.ApplicationActive)

        activate.assert_not_called()

    def test_reactivates_on_non_windows_when_focus_was_actually_lost(self, qapp, mocker):
        import clUI
        mocker.patch.object(clUI.sys, "platform", "linux")
        ui = self._ui(mocker)
        mocker.patch.object(ui.text_input, "hasFocus", return_value=False)
        activate = mocker.patch.object(ui.text_input, "activateWindow")

        ui._on_app_state_changed(clUI.Qt.ApplicationState.ApplicationActive)

        activate.assert_called_once()


class TestSaveUiStateNeverClobbersLayoutFromOverlay:
    """Overlay mode force-hides the drawer and every dashboard widget --
    that's a transient view change, not the user closing anything. A save
    taken while collapsed to overlay used to persist that blanket hidden
    state as if it were real, permanently losing the last real fullscreen
    layout the moment the user (or an ecosystem restart) touched overlay.
    save_ui_state() now only lets active_widgets/drawer_open/carousel_tab
    update while actually in fullscreen; a save from overlay must carry
    those fields forward from disk untouched."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_saving_from_overlay_preserves_the_last_fullscreen_layout(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        previous_layout = {
            "drawer_open": True,
            "carousel_tab": 2,
            "reminder_widget": {"visible": True},
            "active_widgets": {
                "widget_todo_list": {"visible": True, "pos": [500, 400], "size": [230, 100], "is_unpinned": False}
            },
            "current_monitor_idx": 0,
            "screen_size": [1920, 1080],
            "is_fullscreen": True
        }
        with open(fake_state_file, "w") as f:
            json.dump(previous_layout, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            assert ui.is_fullscreen is False  # cold start always opens in overlay
            ui.save_ui_state()

        with open(fake_state_file, "r") as f:
            saved = json.load(f)

        assert saved["is_fullscreen"] is False  # reflects the real current mode
        assert saved["active_widgets"] == previous_layout["active_widgets"]
        assert saved["drawer_open"] == previous_layout["drawer_open"]
        assert saved["carousel_tab"] == previous_layout["carousel_tab"]
        assert saved["reminder_widget"] == previous_layout["reminder_widget"]


class TestTextInputEscapeClearsFocus:
    """The fullscreen text-command box had no way to lose focus except
    clicking elsewhere -- Escape is the conventional way out of a focused
    text field and should just defocus it, not do nothing."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_escape_clears_focus_on_the_text_input(self, qapp, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        try:
            clear_focus = mocker.patch.object(ui.text_input, "clearFocus")

            from PyQt6.QtCore import QEvent
            from PyQt6.QtGui import QKeyEvent
            event = QKeyEvent(QEvent.Type.KeyPress, clUI.Qt.Key.Key_Escape, clUI.Qt.KeyboardModifier.NoModifier)
            consumed = ui.focus_filter.eventFilter(ui.text_input, event)

            clear_focus.assert_called_once()
            assert consumed is True
        finally:
            ui.close()


class TestDraggableWidgetUpdateScalingPreservesSize:
    """update_scaling() runs on every refresh_layout() call, which fires on
    ANY main-window resize -- an overlay <-> fullscreen transition, a
    monitor swap, even Wayland's geometry-correction resizes -- regardless
    of whether this widget's own content actually changed. It used to end
    with an unconditional adjustSize(), snapping back to the natural
    minimum content size every time and silently discarding a user's manual
    drag-resize (or a size just restored from ui_state.json)."""

    def test_a_manually_enlarged_widget_keeps_its_size_after_update_scaling(self, qapp):
        import clUI
        from PyQt6.QtWidgets import QLabel
        content = QLabel("content")
        wrapper = clUI.DraggableWidget("widget_test", "Test", content)
        try:
            wrapper.show()

            natural_hint = wrapper.sizeHint()
            enlarged_w = natural_hint.width() + 200
            enlarged_h = natural_hint.height() + 200
            wrapper.resize(enlarged_w, enlarged_h)

            wrapper.update_scaling()

            assert wrapper.width() == enlarged_w
            assert wrapper.height() == enlarged_h
        finally:
            # A top-level DraggableWidget left showing would otherwise linger
            # as a real on-screen window for the rest of the test session,
            # potentially skewing later tests' QCursor/active-screen detection.
            wrapper.close()

    def test_still_grows_to_fit_if_current_size_is_smaller_than_the_new_hint(self, qapp, mocker):
        import clUI
        from PyQt6.QtCore import QSize
        from PyQt6.QtWidgets import QLabel
        content = QLabel("content")
        wrapper = clUI.DraggableWidget("widget_test", "Test", content)
        try:
            wrapper.show()

            bigger_hint = QSize(wrapper.width() + 200, wrapper.height() + 200)
            mocker.patch.object(clUI.DraggableWidget, "sizeHint", return_value=bigger_hint)

            wrapper.update_scaling()

            assert wrapper.width() >= bigger_hint.width()
            assert wrapper.height() >= bigger_hint.height()
        finally:
            wrapper.close()

    def test_a_grow_here_is_persisted_to_ui_state_like_a_manual_resize_is(self, qapp, mocker):
        """Live bug: a size only ever reached automatically here (e.g.
        switching the notes widget from its list to a single note, whose
        content needs more room) was purely in-memory -- mouseReleaseEvent
        saves after a manual drag-resize, but this grow-only path never
        did, so it was silently lost on the next module restart, reverting
        to whatever was last manually saved."""
        import clUI
        from PyQt6.QtCore import QSize
        from PyQt6.QtWidgets import QLabel
        # main_window is just a plain attribute captured once at
        # construction (see TestSpawnWidgetMainWindowReference above) --
        # passing a mock as the real Qt `parent` itself isn't valid, so
        # construct with parent=None and assign main_window afterward.
        content = QLabel("content")
        wrapper = clUI.DraggableWidget("widget_test", "Test", content)
        main_window = mocker.MagicMock()
        main_window._restoring_ui_state = False
        wrapper.main_window = main_window
        try:
            wrapper.show()

            bigger_hint = QSize(wrapper.width() + 200, wrapper.height() + 200)
            mocker.patch.object(clUI.DraggableWidget, "sizeHint", return_value=bigger_hint)

            wrapper.update_scaling()

            main_window.save_ui_state.assert_called_once()
        finally:
            wrapper.close()

    def test_does_not_save_mid_restore(self, qapp, mocker):
        import clUI
        from PyQt6.QtCore import QSize
        from PyQt6.QtWidgets import QLabel
        content = QLabel("content")
        wrapper = clUI.DraggableWidget("widget_test", "Test", content)
        main_window = mocker.MagicMock()
        main_window._restoring_ui_state = True
        wrapper.main_window = main_window
        try:
            wrapper.show()

            bigger_hint = QSize(wrapper.width() + 200, wrapper.height() + 200)
            mocker.patch.object(clUI.DraggableWidget, "sizeHint", return_value=bigger_hint)

            wrapper.update_scaling()

            main_window.save_ui_state.assert_not_called()
        finally:
            wrapper.close()

    def test_does_not_save_when_no_actual_resize_happens(self, qapp, mocker):
        import clUI
        from PyQt6.QtWidgets import QLabel
        content = QLabel("content")
        wrapper = clUI.DraggableWidget("widget_test", "Test", content)
        main_window = mocker.MagicMock()
        main_window._restoring_ui_state = False
        wrapper.main_window = main_window
        try:
            wrapper.show()

            wrapper.update_scaling()  # already at its natural size -- no resize needed

            main_window.save_ui_state.assert_not_called()
        finally:
            wrapper.close()


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
        # The initial refresh_label() call is deferred (QTimer.singleShot(0, ...))
        # so construction never blocks on real device enumeration -- flush it.
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
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
        from PyQt6.QtWidgets import QApplication
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})
        # set_ui_mode("set_fullscreen") picks its target monitor from
        # wherever the mouse cursor actually is (see _screen_for_cursor) --
        # pinning it to the primary screen keeps this deterministic
        # regardless of the real cursor position or monitor count on the
        # machine running the test.
        mocker.patch.object(clUI.JarvisUI, "_screen_for_cursor", return_value=QApplication.primaryScreen())
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

        ui.close()
        QApplication.processEvents()


class TestLoadUiStateRestoresRealPositionOnReboot:
    """load_ui_state()'s first pass runs during __init__, before the window
    has resized off its tiny overlay geometry -- scaling a saved position
    against self.width()/height() there used to crush every widget toward
    the top-left corner using the overlay box's own tiny dimensions instead
    of the real monitor. Scaling against self.screen().geometry() instead
    keeps the restored position proportionate to the actual screen no
    matter which pass (pre- or post-fullscreen-resize) applies it."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_widget_lands_near_its_saved_position_not_crushed_to_top_left(self, qapp, fake_state_file, mocker):
        import clUI
        from PyQt6.QtWidgets import QApplication
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "1"})

        # set_ui_mode("set_fullscreen") picks its target monitor from
        # wherever the mouse cursor actually is (see _screen_for_cursor) --
        # pinning it to the primary screen keeps this deterministic
        # regardless of the real cursor position or monitor count on the
        # machine running the test.
        mocker.patch.object(clUI.JarvisUI, "_screen_for_cursor", return_value=QApplication.primaryScreen())
        screen_geom = QApplication.primaryScreen().geometry()

        saved_x = screen_geom.x() + int(screen_geom.width() * 0.6)
        saved_y = screen_geom.y() + int(screen_geom.height() * 0.6)
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": True, "current_monitor_idx": 0,
                "screen_size": [screen_geom.width(), screen_geom.height()],
                "active_widgets": {
                    "widget_todo_list": {"visible": True, "pos": [saved_x, saved_y], "size": [230, 100], "is_unpinned": False}
                }
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        w = ui.active_widgets["widget_todo_list"]
        # Same screen, same resolution saved -- position should come back
        # essentially unchanged, not crushed toward (0, 0) by the overlay
        # window's own tiny size.
        assert abs(w.x() - saved_x) < 20
        assert abs(w.y() - saved_y) < 20

        ui.close()
        QApplication.processEvents()


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


class TestLoadUiStateDoesNotShowReminderInOverlaySizedWindow:
    """Live bug: reminder_widget's saved visibility was restored
    unconditionally on every load_ui_state() call, including the very
    first one made in __init__ (restore_widgets=False, deliberately made
    while the window is still sized/positioned as the tiny overlay box --
    see the comment at that call site). A reminder saved visible from a
    previous fullscreen session showed up floating inside the tiny
    overlay square on every restart, instead of waiting for the real
    fullscreen restore the same way the draggable widgets already do."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_reminder_stays_hidden_on_the_restore_widgets_false_pass(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": False, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "reminder_widget": {"visible": True},
                "active_widgets": {},
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()

        assert ui.reminder_widget.isHidden()

    def test_reminder_does_show_once_a_real_restore_runs(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({
                "is_fullscreen": False, "current_monitor_idx": 0, "screen_size": [1920, 1080],
                "reminder_widget": {"visible": True},
                "active_widgets": {},
            }, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.load_ui_state()  # restore_widgets=True, matching set_ui_mode("set_fullscreen")'s own call

        assert not ui.reminder_widget.isHidden()


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


class TestClaudeWidgetShowsCachedScreenOnReopen:
    """jarvis/claude/screen is retained, so it can arrive before the widget is
    ever opened (e.g. right at MQTT connect) -- _handle_claude_screen must
    cache it regardless of whether the widget is currently open, and a freshly
    spawned/reopened ClaudeWidget must be populated from that cache
    immediately instead of starting blank until the next real change. Found
    live: the widget stayed empty across every fullscreen open/close/reopen
    until new Claude output happened to arrive on its own."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_screen_update_is_cached_even_when_the_widget_is_not_open(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            assert "widget_claude" not in ui.active_widgets

            ui._handle_claude_screen({"text": "hello from claude"})

        assert ui._last_claude_screen_text == "hello from claude"

    def test_reopening_the_widget_shows_the_cached_screen_immediately(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.is_fullscreen = True
            ui._handle_claude_screen({"text": "> is the door locked\n\nYes, it's locked."})

            ui._toggle_claude()  # first open
            wrapper = ui.active_widgets["widget_claude"]
            assert wrapper.content_widget.screen_view.toPlainText() == "> is the door locked\n\nYes, it's locked."

            ui._toggle_claude()  # close -- close_draggable_widget really destroys it
            assert "widget_claude" not in ui.active_widgets

            ui._toggle_claude()  # reopen -- must not be blank
            wrapper2 = ui.active_widgets["widget_claude"]
            assert wrapper2.content_widget.screen_view.toPlainText() == "> is the door locked\n\nYes, it's locked."


class TestClosedWidgetRemembersItsPositionForTheRestOfTheSession:
    """close_draggable_widget destroys the wrapper outright, so its position
    drops out of both active_widgets and the next ui_state.json save -- found
    live via the Claude widget always re-centering on reopen. Fixed by
    stashing geometry in _closed_widget_geometry before destroying it, and
    having spawn_widget prefer that over centering when present. Applies to
    every closable widget (Settings, Updates, Debug, Claude), not just Claude."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_reopening_a_closed_widget_restores_its_last_position_and_size(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.is_fullscreen = True

            ui._toggle_settings()  # first open -- centered
            wrapper = ui.active_widgets["widget_settings"]
            wrapper.move(321, 654)
            wrapper.resize(400, 300)

            ui._toggle_settings()  # close -- destroys it
            assert "widget_settings" not in ui.active_widgets

            ui._toggle_settings()  # reopen -- must land back where it was, not re-center
            wrapper2 = ui.active_widgets["widget_settings"]
            assert (wrapper2.x(), wrapper2.y()) == (321, 654)
            assert (wrapper2.width(), wrapper2.height()) == (400, 300)

    def test_a_never_closed_widget_is_unaffected_by_other_widgets_remembered_geometry(
        self, qapp, fake_state_file, mocker
    ):
        """Guards against the remembered-geometry lookup accidentally applying to the
        wrong widget or firing when nothing was ever closed."""
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.is_fullscreen = True

            ui._toggle_updates()
            ui.active_widgets["widget_updates"].move(999, 888)
            ui._toggle_updates()  # close -- only widget_updates should be remembered

            ui._toggle_settings()

        assert "widget_settings" not in ui._closed_widget_geometry
        assert ui._closed_widget_geometry["widget_updates"]["pos"] == [999, 888]


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


class TestClosedWidgetStaysClosedAcrossOverlayRoundTrip:
    """close_draggable_widget() used to only hide the widget, never drop it
    from active_widgets. Every _toggle_* method treats 'in active_widgets'
    as 'currently open', and set_ui_mode('set_fullscreen') unconditionally
    re-shows everything still in that dict on the way back from overlay --
    so a widget the user had actually closed silently reappeared the next
    time they returned to fullscreen from overlay."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_a_closed_widget_does_not_reappear_after_an_overlay_round_trip(self, qapp, fake_state_file, mocker):
        import clUI
        from PyQt6.QtWidgets import QLabel
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            ui.set_ui_mode("set_fullscreen")
            ui.spawn_widget("widget_updates", "System Updates", QLabel("content"))
            assert "widget_updates" in ui.active_widgets

            ui.close_draggable_widget("widget_updates")
            assert "widget_updates" not in ui.active_widgets

            ui.set_ui_mode("set_overlay")
            ui.set_ui_mode("set_fullscreen")

            assert "widget_updates" not in ui.active_widgets


class TestFullscreenAlwaysShowsTextInput:
    """text_input used to only ever get shown as a side effect of
    _on_app_state_changed reacting to the app going Active -- which just
    happened to fire right after showFullScreen() activates the window.
    Gating that handler off on Windows (see TestAppStateChangeDoesNotFightForFocus)
    silently broke this: with no direct show() call anywhere in
    set_ui_mode(), the text bar never appeared on Windows at all."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_text_input_is_visible_after_entering_fullscreen(self, qapp, fake_state_file, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        with open(fake_state_file, "w") as f:
            json.dump({"is_fullscreen": False, "current_monitor_idx": 0, "active_widgets": {}}, f)

        with patch.object(clUI, "STATE_FILE", fake_state_file):
            ui = clUI.JarvisUI()
            assert ui.text_input.isHidden()

            ui.set_ui_mode("set_fullscreen")

            assert ui.text_input.isVisible()


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


class TestSpawnWidgetOneTimeSizeFloor:
    """Live bug: TodoWidget.sizeHint() used to permanently floor itself at
    (350, 400) -- but DraggableWidget.update_scaling()'s grow-only resize
    consults sizeHint() on EVERY status refresh, not just the first one.
    So a size the user deliberately dragged smaller than the floor (or
    one ui_state.json had legitimately saved smaller) got silently grown
    back up to the floor the very next time the todo list refreshed
    (every task added/completed/deleted), defeating ui_state.json as the
    single source of truth for a widget's size. The floor now applies
    only once, here in spawn_widget(), for a widget with no saved size
    yet -- load_ui_state() always resizes to the real saved value right
    after this, for any widget already in ui_state.json, so this can
    never fight a restored or manually-chosen size."""

    @pytest.fixture(autouse=True)
    def no_real_mqtt_thread(self, mocker):
        import clUI
        mocker.patch.object(clUI.MqttThread, "start")

    def test_a_brand_new_todo_widget_gets_floored_to_its_comfortable_minimum(self, qapp, mocker):
        import clUI
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        mocker.patch.object(clUI.ActionRouter, "dispatch", return_value=True)
        ui = clUI.JarvisUI()
        mocker.patch.object(ui, "save_ui_state")
        ui.is_fullscreen = True

        ui.spawn_widget("widget_todo_list", "To-Do List", clUI.TodoWidget())
        wrapper = ui.active_widgets["widget_todo_list"]

        assert wrapper.width() >= 350
        assert wrapper.height() >= 400

    def test_a_widget_with_no_get_standalone_min_size_is_left_at_its_natural_size(self, qapp, mocker):
        import clUI
        from PyQt6.QtWidgets import QLabel
        mocker.patch.dict(os.environ, {"JARVIS_REBOOT": "0"})
        ui = clUI.JarvisUI()
        mocker.patch.object(ui, "save_ui_state")
        ui.is_fullscreen = True

        ui.spawn_widget("widget_plain", "Plain", QLabel("hi"))
        wrapper = ui.active_widgets["widget_plain"]

        # No floor applied -- just whatever the wrapper's own natural sizeHint is.
        assert wrapper.size() == wrapper.sizeHint()


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


class TestLyricsDisplay:
    """Karaoke-style lyrics area: hidden unless Spotify is playing AND a
    lyrics payload matching the exact track media_status currently
    reports has arrived. The backend sends the FULL synced lyric sheet
    once per track (not a running current/next pair) -- this widget holds
    the whole list and picks the current line by index itself, advancing
    locally on a timer as interpolated position crosses each line's own
    timestamp, animating the transition. Holding the whole sheet (rather
    than only ever a current+next pair resolved by a rare backend update)
    means every subsequent line is already known, no matter how long the
    next backend update takes -- mirroring how MediaWidget's own progress
    bar already advances locally between its own infrequent status polls."""

    def _display(self, qapp):
        import clUI
        d = clUI.LyricsDisplay()
        d.resize(600, 120)
        # self.window() on a parentless widget returns itself -- this
        # stands in for "the dashboard is in fullscreen mode", which
        # _refresh_visibility() now requires (see TestLyricsDisplay
        # OverlayVisibility for the overlay-mode gating itself).
        d.is_fullscreen = True
        return d

    def _line(self, t, text):
        return {"time": t, "text": text}

    def _lines(self):
        return [
            self._line(0.0, "First line"),
            self._line(10.0, "Second line"),
            self._line(20.0, "Third line"),
        ]

    def test_hidden_by_default(self, qapp):
        d = self._display(qapp)
        assert d.isHidden()

    def test_word_wrap_is_disabled_on_every_row(self, qapp):
        d = self._display(qapp)
        assert d.next_lbl.wordWrap() is False
        assert d.current_lbl.wordWrap() is False
        assert d.prev_lbl.wordWrap() is False

    def test_current_line_color_is_toned_down_from_full_theme_primary(self, qapp):
        d = self._display(qapp)
        assert d.CURRENT_COLOR_RGBA == (255, 170, 0, 230)  # ~90% opacity, not the fully-opaque theme color
        assert d.CURRENT_COLOR in d.current_lbl.styleSheet()

    def test_stays_hidden_when_not_playing(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", False, 5.0, 30.0)
        assert d.isHidden()

    def test_stays_hidden_when_no_lyrics_found(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", False, [])
        d.set_playback("Song", "Artist", True, 5.0, 30.0)
        assert d.isHidden()

    def test_stays_hidden_when_lyrics_are_for_a_different_track(self, qapp):
        """Live bug this guards against: a lyrics payload for the
        previous track arriving just after media_status already reports
        the new one (or vice versa) must not show mismatched lyrics."""
        d = self._display(qapp)
        d.set_lyrics("Old Song", "Artist", True, self._lines())
        d.set_playback("New Song", "Artist", True, 5.0, 30.0)
        assert d.isHidden()

    def test_becomes_visible_when_playing_with_matching_lyrics(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)
        assert not d.isHidden()

    def test_stays_hidden_in_overlay_mode_even_with_matching_lyrics(self, qapp):
        """Live bug: this widget has no window/frame of its own and isn't
        part of the overlay-mode hide list any other way -- without
        checking is_fullscreen itself, it could show up floating inside
        the tiny overlay square whenever a media_status/lyrics update
        landed while the dashboard was collapsed to overlay."""
        d = self._display(qapp)
        d.is_fullscreen = False  # simulates the dashboard being in overlay mode

        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)

        assert d.isHidden()

    def test_hides_immediately_when_switching_to_overlay_while_shown(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)
        assert not d.isHidden()

        d.is_fullscreen = False
        d._refresh_visibility()

        assert d.isHidden()

    def test_is_enabled_by_default(self, qapp):
        d = self._display(qapp)
        assert d._lyrics_enabled is True

    def test_manually_disabling_hides_it_even_though_it_would_otherwise_show(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)
        assert not d.isHidden()

        d.set_enabled(False)

        assert d.isHidden()

    def test_re_enabling_shows_it_again_if_it_would_otherwise_be_showable(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)
        d.set_enabled(False)
        assert d.isHidden()

        d.set_enabled(True)

        assert not d.isHidden()

    def test_disabling_before_it_would_ever_show_keeps_it_hidden(self, qapp):
        d = self._display(qapp)
        d.set_enabled(False)

        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 5.0, 30.0)

        assert d.isHidden()

    def test_resolves_current_next_and_previous_by_position(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 12.0, 30.0)  # between line 2 (t=10) and line 3 (t=20)

        assert d.current_lbl.text() == "Second line"
        assert d.next_lbl.text() == "Third line"
        assert d.prev_lbl.text() == "First line"

    def test_before_the_first_line_shows_no_current_or_previous_line(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, -1.0, 30.0)

        assert d.current_lbl.text() == ""
        assert d.prev_lbl.text() == ""
        assert d.next_lbl.text() == "First line"

    def test_promotes_to_the_next_line_once_position_reaches_its_timestamp(self, qapp, mocker):
        """The promotion (and the animation it starts) must be triggered
        locally, off interpolated position, not wait for a fresh backend
        update -- with only one publish per track, there may not be
        another one for a long time."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)  # position=8s, just before line 2 (t=10)
        assert d._animating is False
        assert d.current_lbl.text() == "First line"

        mock_time.return_value = 1003.0  # 3 real seconds later -> position ~11s
        d._tick()

        assert d._animating is True
        assert d._current_index == 1
        # The static labels stay on their old text/hidden until the
        # animation finishes -- the temporary sliding labels carry the
        # visible transition instead (see _on_anim_finished). prev_lbl is
        # hidden too: without it, the incoming line's slide into the
        # previous row would overlap prev_lbl's still-visible, stale text
        # (both paint with a transparent background).
        assert d.current_lbl.isHidden()
        assert d.next_lbl.isHidden()
        assert d.prev_lbl.isHidden()

    def test_slide_labels_are_not_qlabels(self, qapp, mocker):
        """Live bug: the temporary sliding labels were real QLabels, whose
        color AND font-size were both silently overridden by
        Theme.get_global_stylesheet()'s app-wide "QLabel { color: ...;
        font-size: ...; }" rule the instant a value was set
        programmatically afterward (confirmed live for both properties,
        via QPalette and via QFont.setPixelSize() in turn) -- no matter
        how the override was attempted, that ancestor rule kept winning
        the QSS cascade for any property it also claimed. A plain
        QWidget that paints its own text via QPainter (_AnimatedLyricLabel)
        has no such rule targeting it at all."""
        import clUI
        from PyQt6.QtWidgets import QLabel
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)
        mock_time.return_value = 1003.0

        d._tick()

        assert isinstance(d._slide_up_lbl, clUI._AnimatedLyricLabel)
        assert not isinstance(d._slide_up_lbl, QLabel)
        assert d._slide_up_lbl.rgba == d.CURRENT_COLOR_RGBA
        assert d._slide_down_lbl.rgba == d.DIM_COLOR_RGBA
        assert d._slide_in_next_lbl.rgba == d.DIM_COLOR_RGBA

    def test_anim_step_updates_the_font_size_progressively_not_just_at_the_end(self, qapp, mocker):
        """Live bug: font-size looked frozen at the theme's global default
        (14px -- coincidentally identical to DIM_FONT_PX, which is why it
        read as "stuck at the small starting size") for the whole
        transition, only reaching the correct value once
        _on_anim_finished() swapped in the real static label. Each step
        must move the size, not just the first/last."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)
        mock_time.return_value = 1003.0
        d._tick()
        assert d._slide_up_lbl.font_px == d.DIM_FONT_PX  # starting point, before any step

        d._on_anim_step(0.3)
        size_at_30pct = d._slide_up_lbl.font_px

        d._on_anim_step(0.7)
        size_at_70pct = d._slide_up_lbl.font_px

        assert d.DIM_FONT_PX < size_at_30pct < size_at_70pct < d.CURRENT_FONT_PX

    def test_finishing_the_animation_reveals_the_correct_final_text(self, qapp, mocker):
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)
        mock_time.return_value = 1003.0
        d._tick()  # triggers the promotion + animation

        d._on_anim_finished()

        assert d._animating is False
        assert not d.current_lbl.isHidden()
        assert not d.next_lbl.isHidden()
        assert not d.prev_lbl.isHidden()
        assert d.current_lbl.text() == "Second line"
        assert d.next_lbl.text() == "Third line"
        assert d.prev_lbl.text() == "First line"

    def test_promote_creates_a_slide_in_label_for_the_brand_new_next_line(self, qapp, mocker):
        """Live gap: only the outgoing-current and outgoing-next lines
        animated -- the brand-new next line (now two ahead of the old
        current line) just popped into the top row at full size the
        instant the transition ended. It now enters on the same clock,
        starting small and below its resting spot."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)  # index 0, "First line"
        mock_time.return_value = 1003.0

        d._tick()  # promotes to index 1 ("Second line"); index 2 ("Third line") becomes the new next

        assert d._slide_in_next_lbl is not None
        assert d._slide_in_next_lbl.text == "Third line"
        assert d._slide_in_next_lbl.geometry().y() == d.NEXT_ENTRY_START_OFFSET_PX
        # Font size/weight live on the widget's own plain attributes, not
        # a stylesheet or QFont lookup -- see _AnimatedLyricLabel.
        assert d._slide_in_next_lbl.font_px == d.NEXT_ENTRY_START_FONT_PX

    def test_promote_creates_no_slide_in_label_when_there_is_no_further_line(self, qapp, mocker):
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 18.0, 30.0)  # index 1, "Second line"
        mock_time.return_value = 1003.0

        d._tick()  # promotes to index 2 ("Third line"); nothing comes after it

        assert d._slide_in_next_lbl is None

    def test_anim_step_interpolates_the_slide_in_label_toward_its_resting_style(self, qapp, mocker):
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)
        mock_time.return_value = 1003.0
        d._tick()

        d._on_anim_step(0.5)

        expected_font = int(d.NEXT_ENTRY_START_FONT_PX + (d.DIM_FONT_PX - d.NEXT_ENTRY_START_FONT_PX) * 0.5)
        expected_y = int(d.NEXT_ENTRY_START_OFFSET_PX * 0.5)
        assert d._slide_in_next_lbl.geometry().y() == expected_y
        assert d._slide_in_next_lbl.font_px == expected_font

        d._on_anim_step(1.0)

        assert d._slide_in_next_lbl.geometry().y() == 0
        assert d._slide_in_next_lbl.font_px == d.DIM_FONT_PX

    def test_finishing_the_animation_cleans_up_the_slide_in_label(self, qapp, mocker):
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 8.0, 30.0)
        mock_time.return_value = 1003.0
        d._tick()

        d._on_anim_finished()

        assert d._slide_in_next_lbl is None

    def test_a_forward_catch_up_jump_still_animates(self, qapp, mocker):
        """Live bug: a sparse position correction (SMTC's own timeline
        snapshot can go stale for several seconds between updates) can
        jump position far enough to skip past more than one line between
        two ticks -- landing directly on the far line with no animation
        made the catch-up look like a flat pop-in instead of the same
        scroll+enlarge every ordinary +1 advance gets. Any FORWARD jump
        (however many lines it skips) now animates the same way; only a
        backward jump or the very first resolution still snaps."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 1.0, 30.0)  # position=1s -> line 0

        mock_time.return_value = 1000.5
        d.set_playback("Song", "Artist", True, 22.0, 30.0)  # jumped straight to line 2
        d._tick()

        assert d._animating is True
        assert d._current_index == 2
        # The incoming line already carries the correct (target) text
        # throughout the animation, not whatever line 1 (the skipped-over
        # line that was showing as the small "next" preview) had.
        assert d._slide_up_lbl.text == "Third line"

        d._on_anim_finished()

        assert d._animating is False
        assert d.current_lbl.text() == "Third line"

    def test_a_backward_jump_snaps_directly_without_animating(self, qapp, mocker):
        """A real seek backward (or a rewind) has no sensible reverse
        animation in this revolver design -- it snaps directly."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", True, 22.0, 30.0)  # position=22s -> line 2

        mock_time.return_value = 1000.5
        d.set_playback("Song", "Artist", True, 1.0, 30.0)  # seeked back to line 0
        d._tick()

        assert d._animating is False
        assert d._current_index == 0
        assert d.current_lbl.text() == "First line"

    def test_does_not_advance_while_paused(self, qapp, mocker):
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines())
        d.set_playback("Song", "Artist", False, 8.0, 30.0)
        assert d.isHidden()  # not playing -- hidden, but position tracking must still not drift

        mock_time.return_value = 1010.0
        d.set_playback("Song", "Artist", True, 8.0, 30.0)  # resumed at the same reported position
        d._tick()

        assert d._animating is False
        assert d.current_lbl.text() == "First line"



class TestLyricsDisplayDynamicWidth:
    """Live bug: word wrap is off (see TestLyricsDisplay), so a line too
    wide for the box's fixed default width just ran off the edges instead
    of being visible. The box now grows its width (never shrinking below
    its original default) to fit whatever the widest of the three current
    texts actually needs, staying horizontally centered as it grows."""

    def _display(self, qapp):
        import clUI
        d = clUI.LyricsDisplay()
        d.resize(400, 120)
        d.default_width = 400
        d.move(100, 500)
        d.is_fullscreen = True  # self.window() on a parentless widget is itself
        return d

    def _short_lines(self):
        return [
            {"time": 0.0, "text": "Short one"},
            {"time": 10.0, "text": "Short two"},
            {"time": 20.0, "text": "Short three"},
        ]

    def _lines_with_a_long_middle_line(self):
        return [
            {"time": 0.0, "text": "Short"},
            {"time": 10.0, "text": "This is a very long lyric line that will not fit in four hundred pixels"},
            {"time": 20.0, "text": "Also short"},
        ]

    def test_short_lines_keep_the_default_width(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._short_lines())

        d.set_playback("Song", "Artist", True, 1.0, 30.0)

        assert d.width() == 400

    def test_a_long_current_line_grows_the_widget_wider(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines_with_a_long_middle_line())

        d.set_playback("Song", "Artist", True, 12.0, 30.0)  # the long line, as current

        assert d.width() > 400

    def test_growth_stays_horizontally_centered(self, qapp):
        d = self._display(qapp)
        original_center_x = d.x() + d.width() // 2
        d.set_lyrics("Song", "Artist", True, self._lines_with_a_long_middle_line())

        d.set_playback("Song", "Artist", True, 12.0, 30.0)

        assert d.width() > 400
        assert d.x() + d.width() // 2 == original_center_x

    def test_promote_applies_the_resize_for_the_destination_layout(self, qapp, mocker):
        """_promote() calls _apply_dynamic_width() itself (not just
        _refresh_texts()), so the transition's own row width is already
        correct at the moment the animation starts, not just once it
        finishes."""
        mock_time = mocker.patch("clUI.time.time")
        mock_time.return_value = 1000.0
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines_with_a_long_middle_line())
        d.set_playback("Song", "Artist", True, 1.0, 30.0)  # current="Short"
        mock_time.return_value = 1011.0  # position ~12s -> promotes into the long line as current

        d._tick()

        assert d._animating is True
        assert d.width() > 400

    def test_returns_to_the_default_width_once_lines_are_short_again(self, qapp):
        d = self._display(qapp)
        d.set_lyrics("Song", "Artist", True, self._lines_with_a_long_middle_line())
        d.set_playback("Song", "Artist", True, 12.0, 30.0)  # the long line, as current
        assert d.width() > 400

        d.set_lyrics("Song2", "Artist2", True, self._short_lines())
        d.set_playback("Song2", "Artist2", True, 1.0, 30.0)

        assert d.width() == 400
