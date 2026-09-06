import pytest
import os
import sys
import types
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import clTerminal
from clTerminal import TerminalManager, _get_linux_media_state, _get_windows_media_state


@pytest.fixture
def manager(mocker):
    mocker.patch.object(TerminalManager, "_load_shortcuts")
    m = TerminalManager()
    m.shortcuts = {"apps": {}, "folders": {}, "system_keywords": {}}
    return m


@pytest.fixture
def fake_windll(mocker):
    """ctypes.windll doesn't exist at all on non-Windows Python, so it must
    be injected (create=True) rather than just patched."""
    fake = MagicMock()
    mocker.patch("ctypes.windll", fake, create=True)
    return fake


class TestVolumeControlWindows:
    def test_sets_master_volume_via_pycaw(self, manager, mocker, fake_windll):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        fake_endpoint_volume = MagicMock()
        fake_audio_utilities = MagicMock()
        fake_audio_utilities.GetSpeakers.return_value.EndpointVolume = fake_endpoint_volume
        fake_pycaw_module = types.ModuleType("pycaw.pycaw")
        fake_pycaw_module.AudioUtilities = fake_audio_utilities
        mocker.patch.dict(sys.modules, {"pycaw": types.ModuleType("pycaw"), "pycaw.pycaw": fake_pycaw_module})

        success, msg = manager.execute_command("volume", level=40)

        assert success is True
        fake_endpoint_volume.SetMasterVolumeLevelScalar.assert_called_once_with(0.4, None)

    def test_clamps_level_to_0_100_range(self, manager, mocker, fake_windll):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        fake_endpoint_volume = MagicMock()
        fake_audio_utilities = MagicMock()
        fake_audio_utilities.GetSpeakers.return_value.EndpointVolume = fake_endpoint_volume
        fake_pycaw_module = types.ModuleType("pycaw.pycaw")
        fake_pycaw_module.AudioUtilities = fake_audio_utilities
        mocker.patch.dict(sys.modules, {"pycaw": types.ModuleType("pycaw"), "pycaw.pycaw": fake_pycaw_module})

        manager.execute_command("volume", level=250)

        fake_endpoint_volume.SetMasterVolumeLevelScalar.assert_called_once_with(1.0, None)

    def test_reports_error_when_pycaw_not_installed(self, manager, mocker, fake_windll):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        mocker.patch.dict(sys.modules, {"pycaw": None, "pycaw.pycaw": None})

        success, msg = manager.execute_command("volume", level=50)

        assert success is False
        assert "pycaw" in msg.lower()


class TestVolumeControlLinux:
    def test_prefers_wpctl_when_available(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", side_effect=lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("volume", level=60)

        assert success is True
        mock_popen.assert_called_once_with(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "60%"])

    def test_falls_back_to_amixer_when_nothing_else_available(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", side_effect=lambda name: "/usr/bin/amixer" if name == "amixer" else None)
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("volume", level=30)

        assert success is True
        mock_popen.assert_called_once_with(["amixer", "sset", "Master", "30%"])


class TestMediaControlWindows:
    """Windows has no play/pause split -- both map to the same toggle key."""

    @pytest.mark.parametrize("action,expected_vk,expected_label", [
        ("media_play", 0xB3, "play/pause"),
        ("media_pause", 0xB3, "play/pause"),
        ("media_next", 0xB0, "next"),
        ("media_prev", 0xB1, "previous"),
    ])
    def test_sends_correct_virtual_key(self, manager, mocker, fake_windll, action, expected_vk, expected_label):
        mocker.patch("clTerminal.CURRENT_OS", "windows")

        success, msg = manager.execute_command(action)

        assert success is True
        assert expected_label in msg
        calls = fake_windll.user32.keybd_event.call_args_list
        assert calls[0].args[0] == expected_vk
        assert calls[1].args[0] == expected_vk


class TestMediaControlLinux:
    def test_uses_playerctl(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/playerctl")
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("media_next")

        assert success is True
        mock_popen.assert_called_once_with(["playerctl", "next"], stdout=mocker.ANY, stderr=mocker.ANY)

    def test_reports_error_when_playerctl_missing(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value=None)

        success, msg = manager.execute_command("media_play")

        assert success is False
        assert "playerctl" in msg.lower()

    def test_unsupported_os_reports_error(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "darwin")

        success, msg = manager.execute_command("media_play")

        assert success is False


class TestEcoModeWindows:
    def test_eco_on_pauses_media_and_powers_down_monitor(self, manager, mocker, fake_windll):
        mocker.patch("clTerminal.CURRENT_OS", "windows")

        success, msg = manager.execute_command("eco_mode_on")

        assert success is True
        fake_windll.user32.keybd_event.assert_called()
        fake_windll.user32.SendMessageW.assert_called_once_with(0xFFFF, 0x0112, 0xF170, 2)

    def test_eco_off_nudges_mouse_to_wake_display(self, manager, mocker, fake_windll):
        mocker.patch("clTerminal.CURRENT_OS", "windows")

        success, msg = manager.execute_command("eco_mode_off")

        assert success is True
        assert fake_windll.user32.mouse_event.call_count == 2


class TestEcoModeLinux:
    def test_eco_on_uses_playerctl_pause_and_xset(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/x")
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("eco_mode_on")

        assert success is True
        calls = [c.args[0] for c in mock_popen.call_args_list]
        assert ["playerctl", "pause"] in calls
        assert ["xset", "dpms", "force", "off"] in calls

    def test_eco_off_uses_xset(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/xset")
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("eco_mode_off")

        assert success is True
        mock_popen.assert_called_once_with(["xset", "dpms", "force", "on"], stdout=mocker.ANY, stderr=mocker.ANY)


@pytest.fixture
def fake_winsdk_smtc(mocker):
    fake_manager = MagicMock()
    fake_session_manager_cls = MagicMock()
    fake_session_manager_cls.request_async = AsyncMock(return_value=fake_manager)

    control_module = types.ModuleType("winsdk.windows.media.control")
    control_module.GlobalSystemMediaTransportControlsSessionManager = fake_session_manager_cls
    media_module = types.ModuleType("winsdk.windows.media")
    media_module.control = control_module
    windows_module = types.ModuleType("winsdk.windows")
    windows_module.media = media_module
    winsdk_module = types.ModuleType("winsdk")
    winsdk_module.windows = windows_module

    mocker.patch.dict(sys.modules, {
        "winsdk": winsdk_module,
        "winsdk.windows": windows_module,
        "winsdk.windows.media": media_module,
        "winsdk.windows.media.control": control_module,
    })
    return fake_manager


class TestWindowsMediaState:
    """_get_windows_media_state is SMTC-based (free, local, no Spotify Web
    API calls) -- the Windows equivalent of playerctl below."""

    @pytest.mark.asyncio
    async def test_reads_playing_session(self, fake_winsdk_smtc):
        session = MagicMock()
        session.get_playback_info.return_value.playback_status = 4  # Playing
        session.try_get_media_properties_async = AsyncMock(return_value=MagicMock(title="Song", artist="Artist"))
        timeline = MagicMock()
        timeline.position.total_seconds.return_value = 12.5
        timeline.end_time.total_seconds.return_value = 200.0
        session.get_timeline_properties.return_value = timeline
        fake_winsdk_smtc.get_current_session.return_value = session

        state = await _get_windows_media_state()

        assert state == {"title": "Song", "artist": "Artist", "status": "Playing", "position": 12.5, "duration": 200.0}

    @pytest.mark.asyncio
    async def test_returns_none_when_no_session(self, fake_winsdk_smtc):
        fake_winsdk_smtc.get_current_session.return_value = None

        assert await _get_windows_media_state() is None

    @pytest.mark.asyncio
    async def test_returns_none_for_stopped_status(self, fake_winsdk_smtc):
        session = MagicMock()
        session.get_playback_info.return_value.playback_status = 3  # Stopped
        fake_winsdk_smtc.get_current_session.return_value = session

        assert await _get_windows_media_state() is None


def _fake_proc(stdout: bytes):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class TestLinuxMediaState:
    @pytest.mark.asyncio
    async def test_reads_playing_track(self, mocker):
        procs = [
            _fake_proc(b"Song Title\n"),
            _fake_proc(b"Some Artist\n"),
            _fake_proc(b"Playing\n"),
            _fake_proc(b"12.5\n"),
            _fake_proc(b"200000000\n"),  # microseconds
        ]
        mocker.patch("clTerminal.asyncio.create_subprocess_exec", AsyncMock(side_effect=procs))

        state = await _get_linux_media_state()

        assert state == {"title": "Song Title", "artist": "Some Artist", "status": "Playing", "position": 12.5, "duration": 200.0}

    @pytest.mark.asyncio
    async def test_returns_none_when_not_playing_or_paused(self, mocker):
        procs = [_fake_proc(b"Song Title\n"), _fake_proc(b"Some Artist\n"), _fake_proc(b"Stopped\n")]
        mocker.patch("clTerminal.asyncio.create_subprocess_exec", AsyncMock(side_effect=procs))

        assert await _get_linux_media_state() is None
