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

    def test_app_volume_tab_width_is_unaffected_by_the_fader_opening(self, qapp, mocker):
        """app_volume_tab is a direct self.layout child (outside the content
        row entirely), so it must not narrow when the fader opens."""
        from PyQt6.QtWidgets import QApplication
        w = _widget(mocker)
        w.show()
        w.resize(400, 300)
        QApplication.processEvents()
        width_before = w.app_volume_tab.width()

        w._on_fader_width_changed(w._fader_width)
        QApplication.processEvents()
        width_after = w.app_volume_tab.width()

        assert width_after == width_before

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
        w.router.dispatch.assert_called_with("terminal.app_volume", action="list_app_volumes")

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
