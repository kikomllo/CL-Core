import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils import clLocalMedia
from utils.clLocalMedia import pick_main_window, send_spotify_command


class TestPickMainWindow:
    """Live finding: of Spotify's many windows, only the main UI window
    (Chrome_WidgetWin_1) reacts to WM_APPCOMMAND; the hidden
    Chrome_WidgetWin_0 ones silently ignore it."""

    def test_picks_the_spotify_main_window(self):
        windows = [(1, "firefox.exe", "Chrome_WidgetWin_1"), (2, "Spotify.exe", "Chrome_WidgetWin_1")]
        assert pick_main_window(windows) == 2

    def test_ignores_spotifys_hidden_helper_windows(self):
        assert pick_main_window([(5, "Spotify.exe", "Chrome_WidgetWin_0")]) is None

    def test_ignores_other_chromium_apps(self):
        assert pick_main_window([(1, "discord.exe", "Chrome_WidgetWin_1")]) is None

    def test_returns_none_when_spotify_is_not_running(self):
        assert pick_main_window([]) is None


class TestSendSpotifyCommand:
    def test_rejects_unknown_commands(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        send = mocker.patch("utils.clLocalMedia._send_app_command_windows")

        assert send_spotify_command("shuffle") is False
        send.assert_not_called()

    def test_windows_uses_the_window_message_first(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        app_command = mocker.patch("utils.clLocalMedia._send_app_command_windows", return_value=True)
        smtc = mocker.patch("utils.clLocalMedia._send_smtc_command_windows")

        assert send_spotify_command("next") is True
        app_command.assert_called_once_with("next")
        smtc.assert_not_called()

    def test_windows_falls_back_to_the_smtc_session_without_a_main_window(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        mocker.patch("utils.clLocalMedia._send_app_command_windows", return_value=False)
        smtc = mocker.patch("utils.clLocalMedia._send_smtc_command_windows", return_value=True)

        assert send_spotify_command("toggle") is True
        smtc.assert_called_once_with("toggle")

    def test_windows_reports_no_client_when_both_ways_fail(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        mocker.patch("utils.clLocalMedia._send_app_command_windows", return_value=False)
        mocker.patch("utils.clLocalMedia._send_smtc_command_windows", return_value=False)

        assert send_spotify_command("prev") is False

    def test_never_raises_on_an_unexpected_error(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        mocker.patch("utils.clLocalMedia._send_app_command_windows", side_effect=OSError("boom"))

        assert send_spotify_command("toggle") is False

    def test_linux_targets_spotifys_mpris_player(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "linux")
        run = mocker.patch("utils.clLocalMedia.subprocess.run", return_value=MagicMock(returncode=0))

        assert send_spotify_command("prev") is True

        cmd = run.call_args.args[0]
        assert "--dest=org.mpris.MediaPlayer2.spotify" in cmd
        assert cmd[-1] == "org.mpris.MediaPlayer2.Player.Previous"

    def test_linux_reports_no_client_when_spotify_is_not_on_dbus(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "linux")
        mocker.patch("utils.clLocalMedia.subprocess.run", return_value=MagicMock(returncode=1))

        assert send_spotify_command("toggle") is False

    def test_unsupported_os_has_no_local_client(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "darwin")

        assert send_spotify_command("toggle") is False


def _run_on(fake_session):
    """Stands in for _smtc_spotify_call: runs the operation on a fake Spotify session."""
    import asyncio

    def call(operation, label):
        return bool(asyncio.run(operation(fake_session)))
    return call


def _fake_session(**overrides):
    from unittest.mock import AsyncMock
    session = MagicMock()
    session.try_change_shuffle_active_async = AsyncMock(return_value=True)
    session.try_change_auto_repeat_mode_async = AsyncMock(return_value=True)
    session.try_change_playback_position_async = AsyncMock(return_value=True)
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


class TestShuffleRepeatSeekWindows:
    """try_* only means "request accepted" -- success also needs the
    session's own state to show it, so a silent no-op becomes an API fallback."""

    @pytest.fixture(autouse=True)
    def fast_polling(self, mocker):
        async def instant(_):
            return None
        mocker.patch("utils.clLocalMedia.asyncio.sleep", instant)

    def test_shuffle_is_confirmed_by_the_session_state(self, mocker):
        session = _fake_session()
        session.get_playback_info.return_value.is_shuffle_active = True
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._set_shuffle_windows(True) is True
        session.try_change_shuffle_active_async.assert_awaited_once_with(True)

    def test_shuffle_the_app_ignored_is_a_failure(self, mocker):
        session = _fake_session()
        session.get_playback_info.return_value.is_shuffle_active = False  # asked for True, never changed
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._set_shuffle_windows(True) is False

    def test_a_refused_request_is_a_failure(self, mocker):
        from unittest.mock import AsyncMock
        session = _fake_session(try_change_shuffle_active_async=AsyncMock(return_value=False))
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._set_shuffle_windows(True) is False

    @pytest.mark.parametrize("mode, number", [("off", 0), ("track", 1), ("context", 2)])
    def test_repeat_sends_the_matching_smtc_mode_and_checks_it(self, mocker, mode, number):
        import types
        session = _fake_session()
        session.get_playback_info.return_value.auto_repeat_mode = number
        fake_enum = MagicMock(side_effect=lambda value: ("mode", value))
        media = types.ModuleType("winsdk.windows.media")
        media.MediaPlaybackAutoRepeatMode = fake_enum
        mocker.patch.dict(sys.modules, {"winsdk": types.ModuleType("winsdk"), "winsdk.windows": types.ModuleType("winsdk.windows"), "winsdk.windows.media": media})
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._set_repeat_windows(mode) is True
        session.try_change_auto_repeat_mode_async.assert_awaited_once_with(("mode", number))

    def test_seek_converts_milliseconds_to_100ns_ticks(self, mocker):
        session = _fake_session()
        session.get_playback_info.return_value.playback_status = 5  # paused: position isn't extrapolated
        session.get_timeline_properties.return_value.position.total_seconds.return_value = 61.0
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._seek_windows(61_000) is True
        session.try_change_playback_position_async.assert_awaited_once_with(610_000_000)

    def test_a_seek_that_did_not_land_is_a_failure(self, mocker):
        session = _fake_session()
        session.get_playback_info.return_value.playback_status = 5
        session.get_timeline_properties.return_value.position.total_seconds.return_value = 5.0  # still near the start
        mocker.patch("utils.clLocalMedia._smtc_spotify_call", _run_on(session))

        assert clLocalMedia._seek_windows(120_000) is False


class TestShuffleRepeatSeekDispatch:
    def test_windows_routes_to_the_smtc_implementations(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        shuffle = mocker.patch("utils.clLocalMedia._set_shuffle_windows", return_value=True)
        repeat = mocker.patch("utils.clLocalMedia._set_repeat_windows", return_value=True)
        seek = mocker.patch("utils.clLocalMedia._seek_windows", return_value=True)

        assert clLocalMedia.set_spotify_shuffle(True) is True
        assert clLocalMedia.set_spotify_repeat("track") is True
        assert clLocalMedia.seek_spotify(30_500) is True

        shuffle.assert_called_once_with(True)
        repeat.assert_called_once_with("track")
        seek.assert_called_once_with(30_500)

    def test_an_unknown_repeat_mode_never_reaches_the_os(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        repeat = mocker.patch("utils.clLocalMedia._set_repeat_windows")

        assert clLocalMedia.set_spotify_repeat("forever") is False
        repeat.assert_not_called()

    def test_a_negative_seek_is_clamped_to_the_start(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "win32")
        seek = mocker.patch("utils.clLocalMedia._seek_windows", return_value=True)

        clLocalMedia.seek_spotify(-5000)

        seek.assert_called_once_with(0)

    @pytest.mark.parametrize("call, args", [
        (lambda: clLocalMedia.set_spotify_shuffle(True), ["shuffle", "On"]),
        (lambda: clLocalMedia.set_spotify_shuffle(False), ["shuffle", "Off"]),
        (lambda: clLocalMedia.set_spotify_repeat("off"), ["loop", "None"]),
        (lambda: clLocalMedia.set_spotify_repeat("track"), ["loop", "Track"]),
        (lambda: clLocalMedia.set_spotify_repeat("context"), ["loop", "Playlist"]),
        (lambda: clLocalMedia.seek_spotify(12_500), ["position", "12.500"]),
    ])
    def test_linux_drives_playerctl_against_spotify(self, mocker, call, args):
        mocker.patch("utils.clLocalMedia.sys.platform", "linux")
        mocker.patch("utils.clLocalMedia.shutil.which", return_value="/usr/bin/playerctl")
        run = mocker.patch("utils.clLocalMedia.subprocess.run", return_value=MagicMock(returncode=0))

        assert call() is True

        assert run.call_args.args[0] == ["playerctl", "--player=spotify", *args]

    def test_linux_without_playerctl_reports_no_local_client(self, mocker):
        mocker.patch("utils.clLocalMedia.sys.platform", "linux")
        mocker.patch("utils.clLocalMedia.shutil.which", return_value=None)
        run = mocker.patch("utils.clLocalMedia.subprocess.run")

        assert clLocalMedia.set_spotify_shuffle(True) is False
        run.assert_not_called()
