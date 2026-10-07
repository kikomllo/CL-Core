import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _widget(mocker):
    import ui.clMediaWidget as clMediaWidget
    mocker.patch.object(clMediaWidget.ActionRouter, "dispatch", return_value=True)
    return clMediaWidget.MediaWidget()


class TestVolumeFaderHoverReveal:
    """The vertical volume fader is hidden (0 width) until the widget is
    hovered. fader_container is a normal layout sibling of content_layout
    (see the row assembled in __init__) -- revealing it just widens its own
    column via setFixedWidth, and ordinary layout reflow narrows the content
    column in response. No manual geometry or resize-driven repositioning is
    involved."""

    def test_fader_starts_collapsed(self, qapp, mocker):
        w = _widget(mocker)
        assert w.fader_container.width() == 0

    def test_hover_animates_fader_open_leave_closes_it(self, qapp, mocker):
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QEnterEvent
        w = _widget(mocker)
        w.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
        assert w._fader_anim.endValue() == w._fader_width

        w.leaveEvent(QEvent(QEvent.Type.Leave))
        assert w._fader_anim.endValue() == 0

    def test_fader_container_is_managed_by_the_content_row_layout(self, qapp, mocker):
        w = _widget(mocker)
        assert w.fader_container.parent() is w

    def test_bottom_button_row_is_unaffected_by_the_fader_opening(self, qapp, mocker):
        """The bottom button row (lyrics / Up Next / App Volume) is a direct
        self.layout child (outside the content row entirely), so it must not
        shift or narrow when the fader opens."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()
        w.resize(400, 300)
        QApplication.processEvents()
        positions_before = [b.x() for b in (w.lyrics_toggle_btn, w.queue_btn, w.app_volume_btn)]

        w._on_fader_width_changed(w._fader_width)
        QApplication.processEvents()
        positions_after = [b.x() for b in (w.lyrics_toggle_btn, w.queue_btn, w.app_volume_btn)]

        assert positions_after == positions_before

    def test_opening_the_fader_widens_its_own_column(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()
        w.resize(400, 300)
        QApplication.processEvents()

        w._on_fader_width_changed(w._fader_width)
        QApplication.processEvents()

        assert w.fader_container.width() == w._fader_width

    def test_fader_fits_flush_to_the_top_at_minimum_size_and_stays_fixed(self, qapp, mocker):
        """The fader's slider has a fixed height (see __init__) so it never
        grows taller as the widget itself grows (e.g. the App Volume drawer
        opening) -- it's pinned to the bottom of its own column instead,
        with the stretch above it absorbing any extra room."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()

        w.resize(300, 200)
        QApplication.processEvents()
        slider_height_at_200 = w.volume_slider.height()

        w.resize(300, 600)
        QApplication.processEvents()

        assert w.volume_slider.height() == slider_height_at_200

    def test_dragging_a_resize_handle_does_not_compound_into_runaway_growth(self, qapp, mocker):
        """Simulates the actual bug report: dragging a resize handle fires
        one resizeEvent per mouse-move step, each incrementally taller than
        the last. With the old live-position-based calculation, each step's
        fader-height fix could itself push progress_bar down further, so
        the NEXT step's target grew disproportionately -- compounding well
        past the user's own drag distance. It must now stay linear."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()

        heights = []
        for target_h in range(200, 400, 10):
            w.resize(300, target_h)
            QApplication.processEvents()
            heights.append(w.fader_container.height())

        # A single, stable fader height throughout is fine (that's the
        # actual fix); what must never happen is it ballooning far beyond
        # the widget's own, bounded resize range.
        assert max(heights) < 400

    def test_minimum_size_hint_does_not_track_the_widgets_own_current_height(self, qapp, mocker):
        """The actual runaway mechanism: fader_container.setFixedHeight()
        also raises its own minimum height, which fed straight into
        MediaWidget's minimumSizeHint() -- and DraggableWidget's resize-drag
        clamps every mouse-move step to max(minimumSizeHint(), mouse_delta).
        With the old code minimumSizeHint() tracked "current height + a
        constant", so it could keep forcing further growth on later resize
        events even without any further downward mouse movement at all."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()
        w.resize(300, 200)
        QApplication.processEvents()
        hint_before = w.minimumSizeHint().height()

        w.resize(300, 600)
        QApplication.processEvents()
        hint_after = w.minimumSizeHint().height()

        assert hint_after == hint_before


class TestVolumeSliderDispatch:
    """Dragging the fader only dispatches the real Spotify API call once the
    user releases the slider, not on every value change while dragging --
    clSpotify.py's volume action has no rate-limit guard of its own, so one
    request per pixel of drag would hammer the Web API."""

    def test_dragging_does_not_dispatch_immediately(self, qapp, mocker):
        w = _widget(mocker)
        w.volume_slider.setValue(40)
        w.router.dispatch.assert_not_called()

    def test_releasing_the_slider_dispatches_the_latest_value(self, qapp, mocker):
        w = _widget(mocker)
        w.volume_slider.setValue(40)
        w.volume_slider.setValue(70)
        w.router.dispatch.assert_not_called()

        w.volume_slider.sliderReleased.emit()

        w.router.dispatch.assert_called_once_with("spotify.control", action="volume", volume=70, silent=True)

    def test_muting_dispatches_immediately_since_it_never_emits_sliderReleased(self, qapp, mocker):
        """_toggle_mute() jumps the slider programmatically (setValue()),
        which never emits sliderReleased -- it must dispatch explicitly, or
        clicking mute would silently do nothing until the next real drag."""
        w = _widget(mocker)
        w.volume_slider.setValue(65)
        w.router.dispatch.reset_mock()

        w._toggle_mute()
        w.router.dispatch.assert_called_once_with("spotify.control", action="volume", volume=0, silent=True)

        w._toggle_mute()
        w.router.dispatch.assert_called_with("spotify.control", action="volume", volume=65, silent=True)

    def test_muting_remembers_and_restores_the_previous_volume(self, qapp, mocker):
        w = _widget(mocker)
        w.volume_slider.setValue(65)

        w._toggle_mute()
        assert w.volume_slider.value() == 0

        w._toggle_mute()
        assert w.volume_slider.value() == 65


class TestUpdateStatusSyncsVolume:
    """update_status() reflects the real Spotify volume onto the fader, but
    must never yank the slider out from under an in-progress user drag."""

    def test_volume_from_status_updates_the_slider(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"title": "Song", "artist": "Artist", "volume": 55})
        assert w.volume_slider.value() == 55

    def test_missing_volume_key_does_not_crash_or_change_the_slider(self, qapp, mocker):
        w = _widget(mocker)
        w.volume_slider.setValue(80)
        w.update_status({"title": "Song", "artist": "Artist"})
        assert w.volume_slider.value() == 80

    def test_does_not_override_an_in_progress_drag(self, qapp, mocker):
        w = _widget(mocker)
        w.volume_slider.setValue(80)
        mocker.patch.object(w.volume_slider, "isSliderDown", return_value=True)
        w.update_status({"title": "Song", "artist": "Artist", "volume": 10})
        assert w.volume_slider.value() == 80

    def test_syncs_extreme_state_even_though_the_update_is_signal_blocked(self, qapp, mocker):
        """Visual bug: Qt's own slider-position math leaves a stray ~1px
        sliver of the wrong color at true 0%/100% (see Theme.
        _slider_extreme_state_qss) unless the atMin/atMax dynamic
        properties are kept in sync -- update_status() sets the slider's
        value with blockSignals wrapped around it (so an in-progress drag's
        own value isn't clobbered by the signal loop), which means
        valueChanged never fires and the sync must happen explicitly."""
        w = _widget(mocker)
        w.update_status({"title": "Song", "artist": "Artist", "volume": 0})
        assert w.volume_slider.property("atMin") is True
        assert w.volume_slider.property("atMax") is False

        w.update_status({"title": "Song", "artist": "Artist", "volume": 100})
        assert w.volume_slider.property("atMin") is False
        assert w.volume_slider.property("atMax") is True


class TestProgressBarReflectsPlaybackPosition:
    """The time row shows a real filled progress bar (not just 'X:XX / Y:YY'
    text), driven by position/duration from update_status()."""

    def test_halfway_through_fills_the_bar_halfway(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"title": "Song", "artist": "Artist", "position": 60.0, "duration": 120.0})
        assert w.progress_bar.value() == 500
        assert w.position_lbl.text() == "1:00"
        assert w.duration_lbl.text() == "2:00"

    def test_zero_duration_does_not_divide_by_zero(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"title": "Song", "artist": "Artist", "position": 0.0, "duration": 0.0})
        assert w.progress_bar.value() == 0


class TestAppVolumeDrawerGrowsTheWrapperInstead:
    """Opening the drawer used to just reveal the label in-place, stealing
    height from content_layout's flexible gap and visually pushing the
    progress bar/controls upward. It should instead grow the wrapping
    DraggableWidget downward, leaving existing content in place, and shrink
    back on close."""

    def _wrapped(self, mocker):
        import clUI
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        wrapper = clUI.DraggableWidget("widget_test_media", "Media Controls", w)
        wrapper.show()
        wrapper.resize(300, 400)
        QApplication.processEvents()
        return wrapper, w

    def test_opening_grows_the_wrapper_by_the_drawers_height(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        height_before = wrapper.height()

        w._toggle_app_volume()
        QApplication.processEvents()

        assert w._app_volume_delta > 0
        assert wrapper.height() == height_before + w._app_volume_delta

        wrapper.close()
        QApplication.processEvents()

    def test_opening_does_not_move_existing_content(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        title_y_before = w.title_lbl.y()
        bar_y_before = w.progress_bar.y()

        w._toggle_app_volume()
        QApplication.processEvents()

        assert w.title_lbl.y() == title_y_before
        assert w.progress_bar.y() == bar_y_before

        wrapper.close()
        QApplication.processEvents()

    def test_closing_shrinks_the_wrapper_back_to_its_original_height(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        height_before = wrapper.height()

        w._toggle_app_volume()  # open
        QApplication.processEvents()
        w._toggle_app_volume()  # close
        QApplication.processEvents()

        assert wrapper.height() == height_before
        assert w._app_volume_delta == 0

        wrapper.close()
        QApplication.processEvents()

    def test_closing_subtracts_the_exact_delta_that_was_applied_on_open(self, qapp, mocker):
        """Closing forces wrapper.height() - delta directly, where delta is
        what actually got applied on open -- not a call to
        wrapper.minimumSizeHint(), which can still read stale/inflated
        right after setVisible(False) (Qt doesn't always invalidate a
        layout's cached minimum synchronously), and previously left the
        wrapper clamped taller than intended after closing."""
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        height_before = wrapper.height()

        w._toggle_app_volume()  # open
        QApplication.processEvents()
        opened_delta = w._app_volume_delta
        height_open = wrapper.height()

        w._toggle_app_volume()  # close
        QApplication.processEvents()

        assert wrapper.height() == height_open - opened_delta
        assert wrapper.height() == height_before

        wrapper.close()
        QApplication.processEvents()

    def test_closing_returns_to_the_true_minimum_when_the_wrapper_starts_there(self, qapp, mocker):
        """A top-level window's layout enforces its minimum by explicitly
        calling wrapper.setMinimumSize() -- a real stored property, distinct
        from minimumSizeHint(), that resize() itself clamps against. That
        call only ever raises the stored minimum; nothing lowers it again on
        its own once the drawer closes and the true minimum drops back down.
        Starting from a comfortably larger size (see _wrapped()) never
        actually exercises that clamp, since resize() never needs to go
        anywhere near either minimum -- only starting at the widget's true
        natural minimum reproduces it."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        import clUI
        wrapper = clUI.DraggableWidget("widget_test_media_min", "Media Controls", w)
        wrapper.show()
        wrapper.resize(wrapper.minimumSizeHint())
        QApplication.processEvents()
        height_before = wrapper.height()

        w._toggle_app_volume()  # open
        QApplication.processEvents()
        w._toggle_app_volume()  # close
        QApplication.processEvents()

        assert wrapper.height() == height_before

        wrapper.close()
        QApplication.processEvents()

    def test_minimum_size_hint_grows_only_while_the_drawer_is_open(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        min_before = w.minimumSizeHint().height()

        w._toggle_app_volume()
        QApplication.processEvents()
        assert w.minimumSizeHint().height() > min_before

        w._toggle_app_volume()
        QApplication.processEvents()
        assert w.minimumSizeHint().height() == min_before

        wrapper.close()
        QApplication.processEvents()


class TestAppVolumeRows:
    """update_app_volumes() populates/updates/removes the drawer's per-app
    rows from clTerminal.py's list_app_volumes data; each row's slider
    dispatches on release (set_app_volume) and its mute button dispatches
    toggle_app_mute, mirroring the main Spotify volume fader's pattern."""

    def test_opening_the_drawer_requests_fresh_app_volumes(self, qapp, mocker):
        w = _widget(mocker)
        w._toggle_app_volume()
        w.router.dispatch.assert_any_call("terminal.app_volume", action="list_app_volumes")

    def test_opening_the_drawer_also_requests_output_devices(self, qapp, mocker):
        w = _widget(mocker)
        w._toggle_app_volume()
        w.router.dispatch.assert_any_call("terminal.app_volume", action="list_output_devices")

    def test_populates_a_row_per_app(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([
            {"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False},
            {"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": True},
        ])

        assert set(w.app_volume_rows.keys()) == {"spotify.exe", "chrome.exe"}
        assert w.app_volume_rows["spotify.exe"]["slider"].value() == 70
        assert w.app_volume_rows["chrome.exe"]["slider"].value() == 40
        assert w.app_volume_rows["chrome.exe"]["muted"] is True
        assert w.app_volume_empty_lbl.isVisibleTo(w.app_volume_body) is False

    def test_shows_empty_state_when_no_apps(self, qapp, mocker):
        # isVisibleTo(app_volume_body), not isVisible() -- the drawer
        # itself (app_volume_body) is never opened in this test, so a
        # plain isVisible() would read False regardless of the label's own
        # flag, since it also reflects every hidden ancestor above it.
        w = _widget(mocker)
        w.update_app_volumes([{"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False}])

        w.update_app_volumes([])

        assert w.app_volume_rows == {}
        assert w.app_volume_empty_lbl.isVisibleTo(w.app_volume_body) is True

    def test_syncs_extreme_state_on_creation_and_on_a_signal_blocked_update(self, qapp, mocker):
        """Visual bug: Qt's own slider-position math leaves a stray ~1px
        sliver of the wrong color at true 0%/100% (see Theme.
        _slider_extreme_state_qss) unless the atMin/atMax dynamic
        properties are kept in sync -- both on row creation and on a
        refresh, which sets the slider's value with blockSignals wrapped
        around it (so an in-progress drag isn't clobbered), meaning
        valueChanged never fires and the sync must happen explicitly."""
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 0, "muted": False}])
        slider = w.app_volume_rows["spotify.exe"]["slider"]
        assert slider.property("atMin") is True
        assert slider.property("atMax") is False

        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 100, "muted": False}])
        assert slider.property("atMin") is False
        assert slider.property("atMax") is True

    def test_removes_rows_for_apps_no_longer_present(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([
            {"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False},
            {"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False},
        ])

        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        assert set(w.app_volume_rows.keys()) == {"spotify.exe"}

    def test_updating_an_existing_app_reuses_its_row(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        row_widget = w.app_volume_rows["spotify.exe"]["widget"]

        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 55, "muted": True}])

        assert w.app_volume_rows["spotify.exe"]["widget"] is row_widget
        assert w.app_volume_rows["spotify.exe"]["slider"].value() == 55
        assert w.app_volume_rows["spotify.exe"]["muted"] is True

    def test_does_not_override_an_in_progress_row_drag(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        slider = w.app_volume_rows["spotify.exe"]["slider"]
        slider.setValue(30)
        mocker.patch.object(slider, "isSliderDown", return_value=True)

        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        assert slider.value() == 30

    def test_releasing_a_row_slider_dispatches_set_app_volume(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        slider = w.app_volume_rows["spotify.exe"]["slider"]
        slider.setValue(35)

        slider.sliderReleased.emit()

        w.router.dispatch.assert_called_with("terminal.app_volume", action="set_app_volume", target="spotify.exe", level=35, silent=True)

    def test_clicking_a_rows_mute_button_dispatches_toggle_app_mute(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        w.app_volume_rows["spotify.exe"]["mute_btn"].click()

        w.router.dispatch.assert_called_with("terminal.app_volume", action="toggle_app_mute", target="spotify.exe", silent=True)

    def test_row_has_a_device_button(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        assert "device_btn" in w.app_volume_rows["spotify.exe"]

    def test_drawer_grows_further_when_more_apps_appear_while_open(self, qapp, mocker):
        """The row list can change while the drawer is already open (a
        fresh app_volumes message arriving after the initial open request)
        -- the wrapper must grow to fit the new content, and still shrink
        back to the true original size on close afterward."""
        from PyQt6.QtWidgets import QApplication
        import clUI
        w = _widget(mocker)
        wrapper = clUI.DraggableWidget("widget_test_app_vol", "Media Controls", w)
        wrapper.show()
        wrapper.resize(300, 400)
        QApplication.processEvents()

        w._toggle_app_volume()  # open with no app data yet
        QApplication.processEvents()
        height_with_no_apps = wrapper.height()

        w.update_app_volumes([
            {"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False},
            {"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False},
        ])
        QApplication.processEvents()

        assert wrapper.height() > height_with_no_apps

        w._toggle_app_volume()  # close
        QApplication.processEvents()

        assert wrapper.height() == 400

        wrapper.close()

    def test_row_has_an_icon_placeholder(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        assert "icon_lbl" in w.app_volume_rows["spotify.exe"]

    def test_subtitle_is_hidden_when_app_has_no_matching_media_session(self, qapp, mocker):
        # clTerminal.py only sets now_playing when it matched this app's
        # audio session against an SMTC/MPRIS media session -- an app
        # without one (e.g. Discord voice chat) must stay hidden rather
        # than show a blank/fabricated line.
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])

        row = w.app_volume_rows["spotify.exe"]
        assert row["subtitle_lbl"].isVisibleTo(row["widget"]) is False

    def test_subtitle_shows_now_playing_text_when_provided(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False, "now_playing": "Lex Fridman Podcast #421"}])

        row = w.app_volume_rows["chrome.exe"]
        assert row["subtitle_lbl"].isVisibleTo(row["widget"]) is True
        assert row["subtitle_lbl"].text() == "Lex Fridman Podcast #421"

    def test_play_button_is_hidden_without_a_matching_media_session(self, qapp, mocker):
        # Only apps clTerminal.py matched to an SMTC/MPRIS session (see
        # media_player) get a play/pause button -- everything else (e.g.
        # Discord voice chat) shows only the volume bar.
        w = _widget(mocker)
        w.update_app_volumes([{"id": "discord.exe", "name": "Discord", "volume": 90, "muted": False}])

        row = w.app_volume_rows["discord.exe"]
        assert row["play_btn"].isVisibleTo(row["widget"]) is False

    def test_play_button_appears_for_a_matched_app(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{
            "id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False,
            "now_playing": "Midnight City", "is_playing": True, "media_player": "SpotifyAB.SpotifyMusic!Spotify",
        }])

        row = w.app_volume_rows["spotify.exe"]
        assert row["play_btn"].isVisibleTo(row["widget"]) is True

    def test_clicking_play_button_dispatches_toggle_app_playback_with_the_media_player_id(self, qapp, mocker):
        # target is the media_player id (SMTC AUMID / MPRIS player name),
        # not the audio-session id used for volume/mute -- the two OS APIs
        # address the same app differently.
        w = _widget(mocker)
        w.update_app_volumes([{
            "id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False,
            "now_playing": "Midnight City", "is_playing": True, "media_player": "SpotifyAB.SpotifyMusic!Spotify",
        }])

        w.app_volume_rows["spotify.exe"]["play_btn"].click()

        w.router.dispatch.assert_called_with("terminal.app_volume", action="toggle_app_playback", target="SpotifyAB.SpotifyMusic!Spotify", silent=True)

    def test_play_button_visibility_updates_when_a_media_session_appears(self, qapp, mocker):
        w = _widget(mocker)
        w.update_app_volumes([{"id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False}])
        row = w.app_volume_rows["chrome.exe"]
        assert row["play_btn"].isVisibleTo(row["widget"]) is False

        w.update_app_volumes([{
            "id": "chrome.exe", "name": "Chrome", "volume": 40, "muted": False,
            "now_playing": "Some Video", "is_playing": False, "media_player": "chrome",
        }])

        assert row["play_btn"].isVisibleTo(row["widget"]) is True


class TestOutputDeviceMenu:
    """The per-app output-device dropdown (headphones icon, left of the
    play button) mirrors ms-settings:apps-volume's own per-app device
    picker -- "System Default" plus whatever real output devices
    clTerminal.py's list_output_devices reported, cached once per drawer-
    open rather than re-fetched per row (see _request_output_devices)."""

    def test_dispatch_sends_set_app_output_device_with_target_and_device_id(self, qapp, mocker):
        w = _widget(mocker)

        w._dispatch_app_output_device("spotify.exe", "{render-1}")

        w.router.dispatch.assert_called_with(
            "terminal.app_volume", action="set_app_output_device", target="spotify.exe", device_id="{render-1}", silent=True,
        )

    def test_system_default_option_dispatches_an_empty_device_id(self, qapp, mocker):
        w = _widget(mocker)

        w._dispatch_app_output_device("spotify.exe", "")

        w.router.dispatch.assert_called_with(
            "terminal.app_volume", action="set_app_output_device", target="spotify.exe", device_id="", silent=True,
        )

    def test_menu_lists_system_default_plus_every_cached_output_device(self, qapp, mocker):
        from PyQt6.QtWidgets import QMenu
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        w.update_output_devices([
            {"id": "{render-1}", "name": "Speakers"},
            {"id": "{render-2}", "name": "Headset"},
        ])
        mocker.patch.object(QMenu, "exec")
        add_action_mock = mocker.patch.object(QMenu, "addAction")

        row = w.app_volume_rows["spotify.exe"]
        w._show_output_device_menu("spotify.exe", row["device_btn"])

        texts = [call.args[0].text() for call in add_action_mock.call_args_list]
        assert texts == ["System Default", "Speakers", "Headset"]

    def test_menu_shows_just_system_default_when_no_devices_cached_yet(self, qapp, mocker):
        from PyQt6.QtWidgets import QMenu
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        mocker.patch.object(QMenu, "exec")
        add_action_mock = mocker.patch.object(QMenu, "addAction")

        row = w.app_volume_rows["spotify.exe"]
        w._show_output_device_menu("spotify.exe", row["device_btn"])

        texts = [call.args[0].text() for call in add_action_mock.call_args_list]
        assert texts == ["System Default"]

    def test_clicking_the_device_button_opens_the_menu(self, qapp, mocker):
        from PyQt6.QtWidgets import QMenu
        w = _widget(mocker)
        w.update_app_volumes([{"id": "spotify.exe", "name": "Spotify", "volume": 70, "muted": False}])
        mock_exec = mocker.patch.object(QMenu, "exec")

        w.app_volume_rows["spotify.exe"]["device_btn"].click()

        mock_exec.assert_called_once()


class TestLyricsToggleButton:
    """First of the three bottom buttons (lyrics, Up Next, App Volume) --
    toggles LyricsDisplay, a sibling widget owned by JarvisUI, not this
    one. There's no signal wired between them; self.window() reaches
    JarvisUI the same way showEvent()/_tick() already do for other state."""

    def test_starts_enabled(self, qapp, mocker):
        w = _widget(mocker)
        assert w._lyrics_enabled is True

    def test_sits_in_the_middle_of_the_bottom_row_between_up_next_and_app_volume(self, qapp, mocker):
        """It used to sit next to the "SPOTIFY PLAYER" badge; the header now
        holds shuffle and repeat."""
        w = _widget(mocker)

        assert w.header_layout.indexOf(w.lyrics_toggle_btn) == -1
        lyrics = w.bottom_row.indexOf(w.lyrics_toggle_btn)
        assert w.bottom_row.indexOf(w.queue_btn) == lyrics - 1
        assert w.bottom_row.indexOf(w.app_volume_btn) == lyrics + 1

    def test_is_a_little_bigger_than_its_neighbours(self, qapp, mocker):
        w = _widget(mocker)
        assert w.lyrics_toggle_btn.width() > w.queue_btn.width()
        assert w.lyrics_toggle_btn.width() > w.app_volume_btn.width()
        assert w.queue_btn.width() == w.app_volume_btn.width()

    def test_the_circles_line_up_vertically_despite_the_size_difference(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.resize(300, 250)
        w.show()
        QApplication.processEvents()

        def circle_center_y(button):
            return button.y() + button._top + button._d / 2

        centers = [circle_center_y(b) for b in (w.queue_btn, w.lyrics_toggle_btn, w.app_volume_btn)]
        assert max(centers) - min(centers) <= 1

    def test_the_dot_follows_the_enabled_state(self, qapp, mocker):
        w = _widget(mocker)
        assert w.lyrics_toggle_btn.is_active() is True

        w.lyrics_toggle_btn.click()

        assert w.lyrics_toggle_btn.is_active() is False

    def test_clicking_disables_and_notifies_lyrics_display(self, qapp, mocker):
        w = _widget(mocker)
        fake_lyrics_display = mocker.MagicMock()
        fake_window = mocker.MagicMock(lyrics_display=fake_lyrics_display)
        w.window = lambda: fake_window

        w.lyrics_toggle_btn.click()

        assert w._lyrics_enabled is False
        fake_lyrics_display.set_enabled.assert_called_once_with(False)

    def test_clicking_twice_re_enables(self, qapp, mocker):
        w = _widget(mocker)
        fake_lyrics_display = mocker.MagicMock()
        fake_window = mocker.MagicMock(lyrics_display=fake_lyrics_display)
        w.window = lambda: fake_window

        w.lyrics_toggle_btn.click()
        w.lyrics_toggle_btn.click()

        assert w._lyrics_enabled is True
        fake_lyrics_display.set_enabled.assert_called_with(True)

    def test_clicking_without_a_real_window_does_not_raise(self, qapp, mocker):
        w = _widget(mocker)  # no parent -- self.window() is just itself, no lyrics_display attribute

        w.lyrics_toggle_btn.click()

        assert w._lyrics_enabled is False

    def test_showEvent_resyncs_from_the_real_lyrics_display_state(self, qapp, mocker):
        """Two MediaWidget instances exist (this dashboard one and the
        calendar carousel's own copy) -- toggling one must not leave the
        other showing a stale icon once it's shown again."""
        w = _widget(mocker)
        fake_lyrics_display = mocker.MagicMock(_lyrics_enabled=False)
        fake_window = mocker.MagicMock(lyrics_display=fake_lyrics_display, is_fullscreen=False)
        w.window = lambda: fake_window
        assert w._lyrics_enabled is True

        w.show()

        assert w._lyrics_enabled is False


class TestShuffleRepeatButtons:
    """Flank the transport controls. A click flips them optimistically and
    sends the action; a dashed "pending" ring shows until the player's next
    status confirms (so a stale status can't flip the button back)."""

    def test_both_start_off(self, qapp, mocker):
        w = _widget(mocker)
        assert w.shuffle_btn.is_active() is False
        assert w.repeat_btn.is_active() is False

    def test_they_sit_in_the_header_right_after_the_badge(self, qapp, mocker):
        """Where the lyrics toggle used to be; the stretch comes after them."""
        w = _widget(mocker)
        badge = w.header_layout.indexOf(w.badge_lbl)

        assert w.header_layout.indexOf(w.shuffle_btn) == badge + 1
        assert w.header_layout.indexOf(w.repeat_btn) == badge + 2

    def test_the_transport_row_is_just_prev_play_next(self, qapp, mocker):
        w = _widget(mocker)

        assert w.controls_layout.indexOf(w.shuffle_btn) == -1
        assert w.controls_layout.indexOf(w.repeat_btn) == -1
        order = [w.controls_layout.indexOf(b) for b in (w.prev_btn, w.play_btn, w.next_btn)]
        assert order == sorted(order) and -1 not in order

    def test_clicking_shuffle_turns_it_on_and_sends_the_action(self, qapp, mocker):
        w = _widget(mocker)

        w.shuffle_btn.click()

        assert w.shuffle_btn.is_active() is True
        w.router.dispatch.assert_called_with("spotify.control", silent=True, action="shuffle", enabled=True)

    def test_clicking_shuffle_again_turns_it_off(self, qapp, mocker):
        w = _widget(mocker)
        w.shuffle_btn.click()

        w.shuffle_btn.click()

        assert w.shuffle_btn.is_active() is False
        w.router.dispatch.assert_called_with("spotify.control", silent=True, action="shuffle", enabled=False)

    def test_repeat_cycles_off_context_track_off(self, qapp, mocker):
        w = _widget(mocker)
        sent = []
        for _ in range(4):
            w.repeat_btn.click()
            sent.append(w.router.dispatch.call_args.kwargs["mode"])

        assert sent == ["context", "track", "off", "context"]

    def test_repeat_track_uses_the_repeat_one_icon(self, qapp, mocker):
        w = _widget(mocker)
        w.repeat_btn.click()  # context
        assert w.repeat_btn._icon_name == "repeat.svg"
        w.repeat_btn.click()  # track
        assert w.repeat_btn._icon_name == "repeat_one.svg"
        assert w.repeat_btn.is_active() is True
        w.repeat_btn.click()  # off
        assert w.repeat_btn.is_active() is False

    def test_a_click_marks_the_button_pending_until_confirmed(self, qapp, mocker):
        w = _widget(mocker)
        w.shuffle_btn.click()
        assert w.shuffle_btn._pending is True

        w.update_status({"title": "T", "artist": "A", "shuffle": True})

        assert w.shuffle_btn._pending is False
        assert w.shuffle_btn.is_active() is True

    def test_a_stale_status_does_not_flip_the_button_back_while_pending(self, qapp, mocker):
        w = _widget(mocker)
        w.shuffle_btn.click()  # wants True

        w.update_status({"title": "T", "artist": "A", "shuffle": False})  # a reading from before the click landed

        assert w.shuffle_btn.is_active() is True
        assert w.shuffle_btn._pending is True

    def test_giving_up_on_a_confirmation_lets_the_next_status_win(self, qapp, mocker):
        w = _widget(mocker)
        w.shuffle_btn.click()
        w._clear_mode_pending()  # what the pending timer does

        w.update_status({"title": "T", "artist": "A", "shuffle": False})

        assert w.shuffle_btn.is_active() is False
        assert w.shuffle_btn._pending is False

    def test_status_shows_the_players_real_modes(self, qapp, mocker):
        w = _widget(mocker)

        w.update_status({"title": "T", "artist": "A", "shuffle": True, "repeat": "track"})

        assert w.shuffle_btn.is_active() is True
        assert w.repeat_btn._icon_name == "repeat_one.svg"

    def test_a_status_without_the_keys_leaves_them_alone(self, qapp, mocker):
        w = _widget(mocker)
        w.update_status({"title": "T", "artist": "A", "shuffle": True, "repeat": "context"})

        w.update_status({"title": "T", "artist": "A"})  # e.g. built from the Web API

        assert w.shuffle_btn.is_active() is True
        assert w.repeat_btn.is_active() is True


class TestSeekBar:
    """The thin progress bar is now clickable: a click seeks, and the new
    position shows immediately instead of waiting for the next status."""

    def _click_at(self, w, fraction):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        bar = w.progress_bar
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(int(bar.width() * fraction), bar.height() // 2))

    def _shown(self, mocker, **status):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.resize(400, 200)
        w.show()
        QApplication.processEvents()
        w.update_status({"title": "T", "artist": "A", "position": 10.0, "duration": 200.0, "status": "Paused", **status})
        return w

    def test_clicking_seeks_to_that_fraction_of_the_track(self, qapp, mocker):
        w = self._shown(mocker)

        self._click_at(w, 0.5)

        call = w.router.dispatch.call_args
        assert call.args == ("spotify.control",)
        assert call.kwargs["action"] == "seek"
        assert abs(call.kwargs["position_ms"] - 100_000) <= 2_000

    def test_the_new_position_shows_immediately(self, qapp, mocker):
        w = self._shown(mocker)

        self._click_at(w, 0.5)

        assert w.position_lbl.text() == "1:40"

    def test_nothing_is_sent_without_a_known_duration(self, qapp, mocker):
        w = self._shown(mocker, duration=0.0)
        w.router.dispatch.reset_mock()

        self._click_at(w, 0.5)

        w.router.dispatch.assert_not_called()

    def test_a_player_that_cannot_seek_ignores_clicks(self, qapp, mocker):
        w = self._shown(mocker, can_seek=False)
        w.router.dispatch.reset_mock()

        self._click_at(w, 0.5)

        w.router.dispatch.assert_not_called()

    def test_a_missing_can_seek_assumes_it_can(self, qapp, mocker):
        w = self._shown(mocker)  # no can_seek key at all
        assert w.progress_bar._seekable is True

    def test_still_reports_its_value_like_a_progress_bar(self, qapp, mocker):
        w = self._shown(mocker, position=50.0, duration=200.0)
        assert w.progress_bar.value() == 250


class TestBottomButtons:
    """Lyrics / Up Next / App Volume as small circles under the progress
    bar; the dot under one marks it on or open. Up Next and App Volume are
    drawers that share the wrapper-growing logic, one open at a time."""

    def test_up_next_and_app_volume_start_closed(self, qapp, mocker):
        w = _widget(mocker)
        assert w.queue_body.isHidden() is True
        assert w.app_volume_body.isHidden() is True
        assert w.queue_btn.is_active() is False
        assert w.app_volume_btn.is_active() is False

    def test_up_next_opens_and_asks_for_the_queue(self, qapp, mocker):
        w = _widget(mocker)

        w.queue_btn.click()

        assert w.queue_body.isHidden() is False
        assert w.queue_btn.is_active() is True
        w.router.dispatch.assert_called_with("spotify.control", action="queue", silent=True)

    def test_up_next_closes_on_a_second_click(self, qapp, mocker):
        w = _widget(mocker)
        w.queue_btn.click()

        w.queue_btn.click()

        assert w.queue_body.isHidden() is True
        assert w.queue_btn.is_active() is False

    def test_opening_app_volume_closes_up_next(self, qapp, mocker):
        w = _widget(mocker)
        w.queue_btn.click()

        w.app_volume_btn.click()

        assert w.queue_body.isHidden() is True
        assert w.app_volume_body.isHidden() is False
        assert (w.queue_btn.is_active(), w.app_volume_btn.is_active()) == (False, True)

    def test_opening_up_next_closes_app_volume(self, qapp, mocker):
        w = _widget(mocker)
        w.app_volume_btn.click()

        w.queue_btn.click()

        assert w.app_volume_body.isHidden() is True
        assert w.queue_body.isHidden() is False
        assert (w.queue_btn.is_active(), w.app_volume_btn.is_active()) == (True, False)

    def test_switching_drawers_leaves_the_wrapper_height_consistent(self, qapp, mocker):
        """Each drawer grows the wrapper by its own delta and shrinks it
        back; going queue -> apps -> closed must land on the starting height."""
        import clUI
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        wrapper = clUI.DraggableWidget("widget_test_media", "Media Controls", w)
        wrapper.show()
        wrapper.resize(300, 400)
        QApplication.processEvents()
        height_before = wrapper.height()

        w._toggle_queue()
        QApplication.processEvents()
        w._toggle_app_volume()
        QApplication.processEvents()
        w._toggle_app_volume()
        QApplication.processEvents()

        assert wrapper.height() == height_before
        assert w._app_volume_delta == 0

        wrapper.close()
        QApplication.processEvents()


class TestUpNextRows:
    def _queue(self, count=7):
        return {"remaining": 18, "queue": [
            {"title": f"Song {i}", "artist": f"Artist {i}", "duration_ms": 125_000 + i * 1000} for i in range(1, count + 1)
        ]}

    def test_shows_at_most_five_rows_and_the_total_in_the_caption(self, qapp, mocker):
        w = _widget(mocker)

        w.update_queue(self._queue(7))

        assert w.queue_rows_layout.count() == 5
        assert w.queue_caption_lbl.text() == "18 tracks, followed locally"

    def test_the_first_row_is_marked_next(self, qapp, mocker):
        from PyQt6.QtWidgets import QLabel
        w = _widget(mocker)
        w.update_queue(self._queue(3))

        def next_labels(row):
            return [l for l in row.findChildren(QLabel) if l.text() == "NEXT"]

        rows = [w.queue_rows_layout.itemAt(i).widget() for i in range(3)]
        assert len(next_labels(rows[0])) == 1
        assert next_labels(rows[1]) == [] and next_labels(rows[2]) == []

    def test_rows_show_the_duration_as_minutes_and_seconds(self, qapp, mocker):
        from PyQt6.QtWidgets import QLabel
        w = _widget(mocker)
        w.update_queue(self._queue(1))

        texts = [l.text() for l in w.queue_rows_layout.itemAt(0).widget().findChildren(QLabel)]

        assert "2:06" in texts  # 126_000 ms

    def test_an_update_replaces_the_old_rows(self, qapp, mocker):
        w = _widget(mocker)
        w.update_queue(self._queue(5))

        w.update_queue(self._queue(2))

        assert w.queue_rows_layout.count() == 2

    def test_an_empty_queue_says_so(self, qapp, mocker):
        w = _widget(mocker)

        w.update_queue({"remaining": 0, "queue": []})

        assert w.queue_rows_layout.count() == 0
        assert w.queue_caption_lbl.text() == "Nothing queued."

    def test_before_any_data_the_caption_says_loading(self, qapp, mocker):
        w = _widget(mocker)
        assert w.queue_caption_lbl.text() == "Loading..."

    def test_refresh_asks_for_a_fresh_fetch_and_dims_the_rows(self, qapp, mocker):
        w = _widget(mocker)
        w.update_queue(self._queue(3))

        w.queue_refresh_btn.click()

        w.router.dispatch.assert_called_with("spotify.control", action="queue_refresh", silent=True)
        assert w.queue_rows_opacity.opacity() == 0.3
        assert w.queue_caption_lbl.text() == "refreshing from Spotify..."

    def test_the_answer_ends_the_refresh(self, qapp, mocker):
        w = _widget(mocker)
        w.queue_refresh_btn.click()

        w.update_queue(self._queue(3))

        assert w.queue_rows_opacity.opacity() == 1.0
        assert w.queue_caption_lbl.text() == "18 tracks, followed locally"


class TestShuffleRepeatAreBareIcons:
    """Shuffle/repeat are just an icon with the on-dot under it (no circle);
    the three bottom buttons keep theirs."""

    def test_shuffle_and_repeat_have_no_circle(self, qapp, mocker):
        w = _widget(mocker)
        assert w.shuffle_btn._circle is False
        assert w.repeat_btn._circle is False

    def test_the_bottom_buttons_keep_their_circles(self, qapp, mocker):
        w = _widget(mocker)
        assert all(b._circle for b in (w.lyrics_toggle_btn, w.queue_btn, w.app_volume_btn))

    def test_bare_buttons_paint_without_error_in_every_state(self, qapp, mocker):
        w = _widget(mocker)
        w.show()
        for button in (w.shuffle_btn, w.repeat_btn):
            for active in (False, True):
                for pending in (False, True):
                    button.set_active(active)
                    button.set_pending(pending)
                    button.grab()  # forces a paintEvent


class TestHeaderAndBottomRowLayout:
    """Shuffle/repeat live in the header, and the bottom button row has no
    spare vertical padding of its own."""

    def _narrow(self, mocker):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.resize(250, 232)
        w.show()
        QApplication.processEvents()
        return w

    def test_the_header_icons_clear_the_fader_on_a_250px_widget(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        w = self._narrow(mocker)

        w._on_fader_width_changed(w._fader_width)
        QApplication.processEvents()

        repeat_right = w.repeat_btn.mapTo(w, w.repeat_btn.rect().topRight()).x()
        fader_left = w.fader_container.mapTo(w, w.fader_container.rect().topLeft()).x()
        assert repeat_right < fader_left

    def test_the_transport_row_keeps_its_original_gaps_with_the_fader_open(self, qapp, mocker):
        w = _widget(mocker)
        closed = w.controls_layout.spacing()

        w._on_fader_width_changed(w._fader_width)

        assert w.controls_layout.spacing() == closed

    def test_the_bottom_row_has_no_extra_vertical_margin(self, qapp, mocker):
        w = _widget(mocker)
        margins = w.bottom_row.contentsMargins()
        assert (margins.top(), margins.bottom()) == (0, 0)

    def test_bottom_buttons_only_reserve_room_for_the_dot_below(self, qapp, mocker):
        w = _widget(mocker)
        for button in (w.lyrics_toggle_btn, w.queue_btn, w.app_volume_btn):
            assert button.height() < button.width() + button.DOT_ZONE  # no room wasted above the circle

    def test_header_icons_share_a_centre_line_with_the_badge(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.resize(300, 250)
        w.show()
        QApplication.processEvents()

        badge_center = w.badge_lbl.y() + w.badge_lbl.height() / 2
        for button in (w.shuffle_btn, w.repeat_btn):
            assert abs(button.y() + button._top + button._d / 2 - badge_center) <= 1

    def test_the_header_row_stays_compact(self, qapp, mocker):
        """Icons plus the dot's room, no more: the dots must not make the
        header taller than the icons themselves need."""
        w = _widget(mocker)
        assert w.header_layout.sizeHint().height() <= w.shuffle_btn.height()

    def test_update_scaling_keeps_the_transport_gap(self, qapp, mocker):
        w = _widget(mocker)
        before = w.controls_layout.spacing()

        w.update_scaling()  # runs on every window resize

        assert w.controls_layout.spacing() == before


class TestSeekBarShowsNoHoverHint:
    def test_hovering_the_bar_shows_no_tooltip(self, qapp, mocker):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication, QToolTip
        w = _widget(mocker)
        w.resize(400, 200)
        w.show()
        QApplication.processEvents()
        w.update_status({"title": "T", "artist": "A", "position": 10.0, "duration": 200.0, "status": "Paused"})

        QTest.mouseMove(w.progress_bar, QPoint(w.progress_bar.width() // 2, w.progress_bar.height() // 2))
        QApplication.processEvents()

        assert QToolTip.isVisible() is False
        assert w.progress_bar.toolTip() == ""


class TestBottomPadding:
    """The dashboard's wrapper already leaves 15px under every widget, so the
    media widget adds none of its own beneath the bottom buttons -- only the
    dot's own room."""

    def test_the_widget_adds_no_bottom_margin_of_its_own(self, qapp, mocker):
        w = _widget(mocker)
        assert w.layout.contentsMargins().bottom() == 0

    def test_it_stays_zero_after_a_rescale(self, qapp, mocker):
        w = _widget(mocker)
        w.update_scaling()
        assert w.layout.contentsMargins().bottom() == 0

    def test_the_gap_below_the_bottom_circles_is_just_the_dot_room(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.resize(300, 250)
        w.show()
        QApplication.processEvents()

        for button in (w.queue_btn, w.lyrics_toggle_btn, w.app_volume_btn):
            circle_bottom = button.y() + button._top + button._d
            assert w.height() - circle_bottom <= 8


class TestMinimumWidthIsTheWidestOfAllStates:
    """Opening Up Next or App Volume used to force the widget wider (its rows
    need more room than the closed widget), and the minimum jumped with the
    state. The minimum width is now the widest any state needs, so it never
    changes when a drawer opens. Height deliberately still follows the open
    drawer: the drawers exist to grow the widget downward."""

    QUEUE = {"remaining": 3, "queue": [{"title": "25 DE ABRIL (feat. Plutonio)", "artist": "LON3R JOHNY, Plutonio", "duration_ms": 172258}]}
    APPS = [{"id": "spotify", "name": "spotify", "now_playing": "Mizzy Miles - FIM DO NADA", "volume": 100, "muted": False, "media_player": "spotify", "is_playing": True}]

    def _wrapped(self, mocker):
        import clUI
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        wrapper = clUI.DraggableWidget("widget_test_media", "Media Controls", w)
        wrapper.show()
        wrapper.resize(400, 300)
        QApplication.processEvents()
        return wrapper, w

    def test_the_minimum_width_is_the_same_closed_and_with_either_drawer_open(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        w.update_queue(self.QUEUE)
        widths = [w.minimumSizeHint().width()]

        w._toggle_queue()
        QApplication.processEvents()
        widths.append(w.minimumSizeHint().width())
        w._toggle_app_volume()
        w.update_app_volumes(self.APPS)
        QApplication.processEvents()
        widths.append(w.minimumSizeHint().width())
        w._toggle_app_volume()
        QApplication.processEvents()
        widths.append(w.minimumSizeHint().width())

        assert len(set(widths)) == 1

        wrapper.close()
        QApplication.processEvents()

    def test_it_covers_the_widest_row_of_either_drawer_before_any_data_exists(self, qapp, mocker):
        """Measured from sample rows, not from whatever rows are loaded, so
        the first open can't widen the widget."""
        w = _widget(mocker)
        before_data = w.minimumSizeHint().width()

        w.update_queue(self.QUEUE)
        w.update_app_volumes(self.APPS)
        w.queue_btn.click()
        w.queue_btn.click()

        assert w.minimumSizeHint().width() == before_data

    def test_opening_a_drawer_never_changes_the_widget_width(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        width_before = wrapper.width()

        w._toggle_queue()
        QApplication.processEvents()
        assert wrapper.width() == width_before
        w._toggle_app_volume()
        w.update_app_volumes(self.APPS)
        QApplication.processEvents()
        assert wrapper.width() == width_before

        wrapper.close()
        QApplication.processEvents()

    def test_the_height_still_follows_the_open_drawer(self, qapp, mocker):
        from PyQt6.QtWidgets import QApplication
        wrapper, w = self._wrapped(mocker)
        closed_min_height = w.minimumSizeHint().height()

        w.update_queue(self.QUEUE)
        w._toggle_queue()
        QApplication.processEvents()

        assert w.minimumSizeHint().height() > closed_min_height

        wrapper.close()
        QApplication.processEvents()

    def test_the_sizing_sample_row_is_discarded(self, qapp, mocker):
        w = _widget(mocker)
        assert "__sizing__" not in w.app_volume_rows
        assert w.app_volume_layout.count() == 1  # just the "no apps" label
