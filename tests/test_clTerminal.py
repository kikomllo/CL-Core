import pytest
import os
import sys
import types
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import clTerminal
from clTerminal import (
    TerminalManager, _get_linux_media_state, _get_windows_media_state,
    _get_linux_spotify_state, _get_windows_spotify_state,
)


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


def _fake_windows_sessions_module(mocker, sessions):
    fake_audio_utilities = MagicMock()
    fake_audio_utilities.GetAllSessions.return_value = sessions
    fake_pycaw_module = types.ModuleType("pycaw.pycaw")
    fake_pycaw_module.AudioUtilities = fake_audio_utilities
    mocker.patch.dict(sys.modules, {"pycaw": types.ModuleType("pycaw"), "pycaw.pycaw": fake_pycaw_module})
    return fake_audio_utilities


def _fake_windows_session(name, volume=0.5, muted=False, no_process=False, pid=1234):
    s = MagicMock()
    if no_process:
        s.Process = None
    else:
        s.Process.name.return_value = name
        s.Process.pid = pid  # a real int -- passed to ctypes in _get_process_aumid
    s.SimpleAudioVolume.GetMasterVolume.return_value = volume
    s.SimpleAudioVolume.GetMute.return_value = muted
    return s


class TestListAppVolumesWindows:
    def test_groups_sessions_by_process_name_and_skips_system_sounds(self, manager, mocker):
        # Tests _list_app_volumes_windows() directly, not the filtered
        # list_app_volumes() (which now drops anything without a matching
        # media session -- see TestListAppVolumesMergesMediaSessions below).
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        spotify1 = _fake_windows_session("Spotify.exe", volume=0.7, muted=False)
        spotify2 = _fake_windows_session("Spotify.exe", volume=0.7, muted=False)  # 2nd session, same app
        chrome = _fake_windows_session("chrome.exe", volume=0.4, muted=True)
        system_sounds = _fake_windows_session("", no_process=True)
        _fake_windows_sessions_module(mocker, [spotify1, spotify2, chrome, system_sounds])

        apps = manager._list_app_volumes_windows()

        # id keeps the real process name (used to re-match sessions for
        # set/mute); name is the display-friendly, ".exe"-stripped version;
        # pid backs the exact AUMID lookup in list_app_volumes()'s merge.
        assert apps == [
            {"id": "Spotify.exe", "name": "Spotify", "volume": 70, "muted": False, "pid": 1234},
            {"id": "chrome.exe", "name": "chrome", "volume": 40, "muted": True, "pid": 1234},
        ]

    def test_returns_empty_list_when_pycaw_not_installed(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={})
        mocker.patch.dict(sys.modules, {"pycaw": None, "pycaw.pycaw": None})

        assert manager.list_app_volumes() == []


class TestSetAppVolumeAndMuteWindows:
    def test_set_app_volume_applies_to_every_session_for_that_app(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        s1 = _fake_windows_session("chrome.exe")
        s2 = _fake_windows_session("chrome.exe")
        _fake_windows_sessions_module(mocker, [s1, s2])

        success, msg = manager.execute_command("set_app_volume", target="chrome.exe", level=60)

        assert success is True
        s1.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.6, None)
        s2.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.6, None)

    def test_set_app_volume_reports_error_when_app_not_found(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        _fake_windows_sessions_module(mocker, [])

        success, msg = manager.execute_command("set_app_volume", target="ghost.exe", level=60)

        assert success is False

    def test_toggle_app_mute_flips_every_session_for_that_app(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        s1 = _fake_windows_session("chrome.exe", muted=False)
        s2 = _fake_windows_session("chrome.exe", muted=False)
        _fake_windows_sessions_module(mocker, [s1, s2])

        success, msg = manager.execute_command("toggle_app_mute", target="chrome.exe")

        assert success is True
        s1.SimpleAudioVolume.SetMute.assert_called_once_with(True, None)
        s2.SimpleAudioVolume.SetMute.assert_called_once_with(True, None)


SAMPLE_PACTL_OUTPUT = (
    "Sink Input #123\n"
    "\tMute: no\n"
    "\tVolume: front-left: 45875 /  70% / -8.94 dB,   front-right: 45875 /  70% / -8.94 dB\n"
    "\tProperties:\n"
    "\t\tapplication.name = \"Spotify\"\n"
    "\n"
    "Sink Input #456\n"
    "\tMute: yes\n"
    "\tVolume: front-left: 32768 /  50% / -12.00 dB,   front-right: 32768 /  50% / -12.00 dB\n"
    "\tProperties:\n"
    "\t\tapplication.name = \"Firefox\"\n"
)


class TestParsePactlSinkInputs:
    def test_parses_multiple_apps(self):
        apps = TerminalManager._parse_pactl_sink_inputs(SAMPLE_PACTL_OUTPUT)

        assert apps == [
            {"id": "123", "name": "Spotify", "volume": 70, "muted": False},
            {"id": "456", "name": "Firefox", "volume": 50, "muted": True},
        ]

    def test_skips_entries_without_an_application_name(self):
        output = "Sink Input #1\n\tMute: no\n\tVolume: front-left: 0 /  10% / 0 dB\n"

        assert TerminalManager._parse_pactl_sink_inputs(output) == []


class TestListAppVolumesLinux:
    def test_returns_parsed_apps(self, manager, mocker):
        # Tests _list_app_volumes_linux() directly, not the filtered
        # list_app_volumes() (which now drops anything without a matching
        # media session -- see TestListAppVolumesMergesMediaSessions below).
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/pactl")
        mocker.patch("clTerminal.subprocess.run").return_value.stdout = SAMPLE_PACTL_OUTPUT

        apps = manager._list_app_volumes_linux()

        assert apps == [
            {"id": "123", "name": "Spotify", "volume": 70, "muted": False},
            {"id": "456", "name": "Firefox", "volume": 50, "muted": True},
        ]

    def test_returns_empty_list_when_pactl_missing(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value=None)
        mocker.patch.object(manager, "_list_linux_media_sessions", return_value={})

        assert manager.list_app_volumes() == []


class TestSetAppVolumeAndMuteLinux:
    def test_set_app_volume_calls_pactl(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/pactl")
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("set_app_volume", target="123", level=80)

        assert success is True
        mock_popen.assert_called_once_with(["pactl", "set-sink-input-volume", "123", "80%"])

    def test_toggle_app_mute_flips_and_calls_pactl(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/pactl")
        mocker.patch("clTerminal.subprocess.run").return_value.stdout = SAMPLE_PACTL_OUTPUT
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("toggle_app_mute", target="123")

        assert success is True
        mock_popen.assert_called_once_with(["pactl", "set-sink-input-mute", "123", "1"])  # 123 was unmuted

    def test_toggle_app_mute_reports_error_when_not_found(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/pactl")
        mocker.patch("clTerminal.subprocess.run").return_value.stdout = SAMPLE_PACTL_OUTPUT

        success, msg = manager.execute_command("toggle_app_mute", target="999")

        assert success is False


class TestNormalizeAppKey:
    """The two OS APIs name the same app differently -- pycaw's
    'Spotify.exe' vs. SMTC's AUMID; pactl's 'Spotify' vs. MPRIS's
    'spotify.instance1234' -- so matching them needs both stripped down to
    the same comparable form."""

    @pytest.mark.parametrize("raw,expected", [
        ("Spotify.exe", "spotify"),
        ("chrome.EXE", "chrome"),
        ("spotify.instance1234", "spotify"),
        ("Spotify", "spotify"),
        ("", ""),
    ])
    def test_strips_exe_suffix_and_mpris_instance_suffix(self, raw, expected):
        assert TerminalManager._normalize_app_key(raw) == expected


class TestFindMatchingMediaSession:
    def test_matches_by_substring_either_direction(self, manager):
        sessions = {
            "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify": {"title": "Song", "status": "Playing", "player": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
            "chromium.instance5678": {"title": "Video", "status": "Paused", "player": "chromium.instance5678"},
        }

        spotify_match = manager._find_matching_media_session("Spotify.exe", sessions)
        assert spotify_match is not None
        assert spotify_match["title"] == "Song"

        chrome_match = manager._find_matching_media_session("chromium", sessions)
        assert chrome_match is not None
        assert chrome_match["title"] == "Video"

    def test_returns_none_when_nothing_matches(self, manager):
        sessions = {"spotify": {"title": "Song", "status": "Playing", "player": "spotify"}}

        assert manager._find_matching_media_session("discord.exe", sessions) is None

    def test_returns_none_for_an_empty_app_name(self, manager):
        assert manager._find_matching_media_session("", {"spotify": {}}) is None


class TestListAppVolumesMergesMediaSessions:
    """list_app_volumes() only returns apps with a matching media session
    (SMTC/MPRIS) -- it's a "what can I control" list, not a general volume
    mixer, so an app with volume but no media session (e.g. Discord voice
    chat) doesn't appear in the result at all."""

    def test_windows_matched_app_gets_now_playing_and_media_player(self, manager, mocker):
        # No AUMID for either process here (see _get_process_aumid) --
        # exercises the fuzzy name-matching fallback specifically. The
        # PID-exact-match path is covered separately below.
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        mocker.patch.object(manager, "_get_process_aumid", return_value=None)
        spotify = _fake_windows_session("Spotify.exe", volume=0.7, muted=False)
        discord = _fake_windows_session("Discord.exe", volume=0.9, muted=False)
        _fake_windows_sessions_module(mocker, [spotify, discord])
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify": {
                "title": "Midnight City", "artist": "M83", "status": "Playing",
                "player": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
            },
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["Spotify.exe"]["now_playing"] == "Midnight City"
        assert apps["Spotify.exe"]["is_playing"] is True
        assert apps["Spotify.exe"]["media_player"] == "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
        # Discord has volume but no matching media session -- excluded entirely.
        assert "Discord.exe" not in apps

    def test_windows_matches_by_exact_pid_aumid_when_names_share_nothing(self, manager, mocker):
        """Live bug: Netflix playing in Firefox never matched, because
        Firefox never registers a human-readable AUMID -- its own SMTC
        session reports an opaque, Windows-generated id ("308046B0AF4A39CB")
        that shares no substring with "firefox" at all. Asking Windows for
        the exact AUMID of Firefox's own process (via GetApplicationUserModelId)
        finds it directly, without needing the id to look like anything."""
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        firefox = _fake_windows_session("firefox.exe", volume=0.6, muted=False, pid=5555)
        _fake_windows_sessions_module(mocker, [firefox])
        mocker.patch.object(manager, "_get_process_aumid", side_effect=lambda pid: "308046B0AF4A39CB" if pid == 5555 else None)
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "308046B0AF4A39CB": {"title": "Stranger Things", "artist": "", "status": "Paused", "player": "308046B0AF4A39CB"},
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["firefox.exe"]["now_playing"] == "Stranger Things"
        assert apps["firefox.exe"]["media_player"] == "308046B0AF4A39CB"

    def test_windows_falls_back_to_a_different_process_with_the_same_name(self, manager, mocker):
        """Live bug (round 2): even after the exact-PID lookup above, real
        Firefox testing showed the PID pycaw picked as owning the audio
        session (19036) had no AUMID at all -- Firefox is multi-process,
        and the AUMID ended up registered on a *different* firefox.exe
        process that pycaw's own session enumeration never surfaced. Only
        a system-wide scan for every process sharing that same name finds it."""
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        firefox = _fake_windows_session("firefox.exe", volume=0.6, muted=False, pid=19036)
        _fake_windows_sessions_module(mocker, [firefox])
        # pycaw's own pid has no AUMID; a sibling process (19037, never seen
        # by pycaw at all) is the one actually registered with SMTC.
        mocker.patch.object(manager, "_get_process_aumid", side_effect=lambda pid: {19037: "308046B0AF4A39CB"}.get(pid))
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "308046B0AF4A39CB": {"title": "Stranger Things", "artist": "", "status": "Paused", "player": "308046B0AF4A39CB"},
        })

        fake_psutil = types.ModuleType("psutil")
        fake_psutil.process_iter = MagicMock(return_value=[
            MagicMock(pid=19036, info={"name": "firefox.exe"}),
            MagicMock(pid=19037, info={"name": "firefox.exe"}),
            MagicMock(pid=42, info={"name": "chrome.exe"}),
        ])
        mocker.patch.dict(sys.modules, {"psutil": fake_psutil})

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["firefox.exe"]["now_playing"] == "Stranger Things"
        assert apps["firefox.exe"]["media_player"] == "308046B0AF4A39CB"

    def test_windows_pairs_a_browser_with_a_leftover_media_session_when_unambiguous(self, manager, mocker):
        """Live bug (round 3), confirmed on a real machine: Firefox
        registers no AUMID on any of its running processes at all, and
        SMTC's own API exposes no PID either -- there's no attribute left
        to match on. The only way to catch this is pairing a still-
        unmatched browser with whatever media session nothing else
        claimed, when there's exactly one on each side."""
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        firefox = _fake_windows_session("firefox.exe", volume=0.44, muted=False, pid=19036)
        _fake_windows_sessions_module(mocker, [firefox])
        mocker.patch.object(manager, "_get_process_aumid", return_value=None)
        mocker.patch.object(manager, "_find_media_session_by_process_name", return_value=None)
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "308046B0AF4A39CB": {"title": "Netflix", "artist": "", "status": "Playing", "player": "308046B0AF4A39CB"},
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["firefox.exe"]["now_playing"] == "Netflix"
        assert apps["firefox.exe"]["is_playing"] is True
        assert apps["firefox.exe"]["media_player"] == "308046B0AF4A39CB"

    def test_windows_prefers_the_playing_session_over_a_paused_leftover(self, manager, mocker):
        """A browser with several tabs can leave several sessions behind --
        e.g. Netflix paused in one tab, YouTube actually playing in another.
        The actively playing one should win rather than making the whole
        match bail out as ambiguous."""
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        firefox = _fake_windows_session("firefox.exe", volume=0.44, muted=False, pid=19036)
        _fake_windows_sessions_module(mocker, [firefox])
        mocker.patch.object(manager, "_get_process_aumid", return_value=None)
        mocker.patch.object(manager, "_find_media_session_by_process_name", return_value=None)
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "308046B0AF4A39CB": {"title": "Netflix", "artist": "", "status": "Paused", "player": "308046B0AF4A39CB"},
            "9F1A22C3D4E5F607": {"title": "Some YouTube Video", "artist": "", "status": "Playing", "player": "9F1A22C3D4E5F607"},
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["firefox.exe"]["now_playing"] == "Some YouTube Video"
        assert apps["firefox.exe"]["is_playing"] is True
        assert apps["firefox.exe"]["media_player"] == "9F1A22C3D4E5F607"

    def test_windows_does_not_pair_when_multiple_browsers_are_unmatched(self, manager, mocker):
        """Pairing a browser with a leftover session is a guess -- with two
        browsers open and only one leftover session, there's no way to
        know which one it belongs to, so neither gets matched rather than
        risking controlling the wrong app's playback."""
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        firefox = _fake_windows_session("firefox.exe", volume=0.44, muted=False, pid=1)
        chrome = _fake_windows_session("chrome.exe", volume=0.5, muted=False, pid=2)
        _fake_windows_sessions_module(mocker, [firefox, chrome])
        mocker.patch.object(manager, "_get_process_aumid", return_value=None)
        mocker.patch.object(manager, "_find_media_session_by_process_name", return_value=None)
        mocker.patch.object(manager, "_list_windows_media_sessions", return_value={
            "308046B0AF4A39CB": {"title": "Netflix", "artist": "", "status": "Playing", "player": "308046B0AF4A39CB"},
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps == {}

    def test_linux_matched_app_gets_now_playing_and_media_player(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/pactl")
        mocker.patch("clTerminal.subprocess.run").return_value.stdout = SAMPLE_PACTL_OUTPUT
        mocker.patch.object(manager, "_list_linux_media_sessions", return_value={
            "spotify": {"title": "Midnight City", "artist": "M83", "status": "Playing", "player": "spotify"},
        })

        apps = {a["id"]: a for a in manager.list_app_volumes()}

        assert apps["123"]["now_playing"] == "Midnight City"  # Spotify's sink-input id
        assert apps["123"]["is_playing"] is True
        assert apps["123"]["media_player"] == "spotify"
        # Firefox (id 456) has no matching media session here -- excluded entirely.
        assert "456" not in apps


class TestListWindowsMediaSessions:
    """Live bug: Netflix playing in Firefox never showed a play/pause
    button, even though the OS's own media keys could control it -- a
    strict Playing(4)/Paused(5)-only filter was dropping browser-hosted
    DRM/streaming sessions that report one of SMTC's other states
    (Opened/Changing/Stopped) instead."""

    def test_includes_a_session_reporting_stopped_not_playing_or_paused(self, manager, fake_winsdk_smtc):
        session = MagicMock()
        session.source_app_user_model_id = "firefox.exe"
        session.get_playback_info.return_value.playback_status = 3  # Stopped
        session.try_get_media_properties_async = AsyncMock(return_value=MagicMock(title="Stranger Things", artist=""))
        fake_winsdk_smtc.get_sessions.return_value = [session]

        sessions = manager._list_windows_media_sessions()

        assert "firefox.exe" in sessions
        assert sessions["firefox.exe"]["title"] == "Stranger Things"
        assert sessions["firefox.exe"]["status"] == "Paused"  # anything but Playing(4) reads as not-playing

    def test_excludes_a_closed_session(self, manager, fake_winsdk_smtc):
        session = MagicMock()
        session.source_app_user_model_id = "ghost.exe"
        session.get_playback_info.return_value.playback_status = 0  # Closed
        fake_winsdk_smtc.get_sessions.return_value = [session]

        sessions = manager._list_windows_media_sessions()

        assert "ghost.exe" not in sessions

    def test_one_bad_session_does_not_hide_every_other_apps_match(self, manager, fake_winsdk_smtc):
        broken = MagicMock()
        broken.source_app_user_model_id = "broken.exe"
        broken.get_playback_info.side_effect = Exception("transient WinRT error")

        spotify = MagicMock()
        spotify.source_app_user_model_id = "Spotify.exe"
        spotify.get_playback_info.return_value.playback_status = 4  # Playing
        spotify.try_get_media_properties_async = AsyncMock(return_value=MagicMock(title="Midnight City", artist="M83"))

        fake_winsdk_smtc.get_sessions.return_value = [broken, spotify]

        sessions = manager._list_windows_media_sessions()

        assert "broken.exe" not in sessions
        assert sessions["Spotify.exe"]["title"] == "Midnight City"


class TestToggleAppPlayback:
    def test_windows_toggles_the_matching_smtc_session(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        session = MagicMock()
        session.source_app_user_model_id = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
        session.try_toggle_play_pause_async = AsyncMock()
        _fake_windows_sessions_module(mocker, [])  # unused by this path, just satisfies the pycaw import
        fake_session_manager = MagicMock()
        fake_session_manager.get_sessions.return_value = [session]
        fake_smtc_cls = MagicMock()
        fake_smtc_cls.request_async = AsyncMock(return_value=fake_session_manager)
        control_module = types.ModuleType("winsdk.windows.media.control")
        control_module.GlobalSystemMediaTransportControlsSessionManager = fake_smtc_cls
        mocker.patch.dict(sys.modules, {"winsdk.windows.media.control": control_module})

        success, msg = manager.execute_command("toggle_app_playback", target="SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")

        assert success is True
        session.try_toggle_play_pause_async.assert_called_once()

    def test_windows_reports_error_when_session_not_found(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "windows")
        fake_session_manager = MagicMock()
        fake_session_manager.get_sessions.return_value = []
        fake_smtc_cls = MagicMock()
        fake_smtc_cls.request_async = AsyncMock(return_value=fake_session_manager)
        control_module = types.ModuleType("winsdk.windows.media.control")
        control_module.GlobalSystemMediaTransportControlsSessionManager = fake_smtc_cls
        mocker.patch.dict(sys.modules, {"winsdk.windows.media.control": control_module})

        success, msg = manager.execute_command("toggle_app_playback", target="ghost")

        assert success is False

    def test_linux_calls_playerctl_play_pause_on_the_target_player(self, manager, mocker):
        mocker.patch("clTerminal.CURRENT_OS", "linux")
        mocker.patch("clTerminal.shutil.which", return_value="/usr/bin/playerctl")
        mock_popen = mocker.patch("clTerminal.subprocess.Popen")

        success, msg = manager.execute_command("toggle_app_playback", target="spotify")

        assert success is True
        mock_popen.assert_called_once_with(["playerctl", "--player", "spotify", "play-pause"])


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


class TestWindowsSpotifyState:
    """_get_windows_spotify_state scans every SMTC session for Spotify's own
    (matched by app user model ID) instead of whichever one the OS considers
    "current" -- the free, local counterpart to clSpotify.py's Web-API-based
    status(), used so the media widget's regular polling doesn't have to hit
    the real Spotify API just to show track/artist/position."""

    @pytest.mark.asyncio
    async def test_finds_spotify_among_multiple_sessions(self, fake_winsdk_smtc):
        browser_session = MagicMock()
        browser_session.source_app_user_model_id = "Chrome"
        spotify_session = MagicMock()
        spotify_session.source_app_user_model_id = "Spotify.exe"
        spotify_session.get_playback_info.return_value.playback_status = 4  # Playing
        spotify_session.try_get_media_properties_async = AsyncMock(return_value=MagicMock(title="Song", artist="Artist"))
        timeline = MagicMock()
        timeline.position.total_seconds.return_value = 5.0
        timeline.end_time.total_seconds.return_value = 180.0
        spotify_session.get_timeline_properties.return_value = timeline
        # Spotify isn't the first (or "current") session -- must not just grab session[0].
        fake_winsdk_smtc.get_sessions.return_value = [browser_session, spotify_session]

        state = await _get_windows_spotify_state()

        assert state == {"title": "Song", "artist": "Artist", "status": "Playing", "position": 5.0, "duration": 180.0}

    @pytest.mark.asyncio
    async def test_returns_none_when_spotify_has_no_session(self, fake_winsdk_smtc):
        browser_session = MagicMock()
        browser_session.source_app_user_model_id = "Chrome"
        fake_winsdk_smtc.get_sessions.return_value = [browser_session]

        assert await _get_windows_spotify_state() is None


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


class TestLinuxSpotifyState:
    """_get_linux_spotify_state targets the MPRIS player named 'spotify'
    specifically (playerctl -l), regardless of which player playerctl
    considers current."""

    @pytest.mark.asyncio
    async def test_finds_spotify_among_multiple_players(self, mocker):
        procs = [
            _fake_proc(b"chromium.instance1\nspotify\n"),  # playerctl -l
            _fake_proc(b"Song Title\n"),
            _fake_proc(b"Some Artist\n"),
            _fake_proc(b"Playing\n"),
            _fake_proc(b"12.5\n"),
            _fake_proc(b"200000000\n"),  # microseconds
        ]
        mocker.patch("clTerminal.asyncio.create_subprocess_exec", AsyncMock(side_effect=procs))

        state = await _get_linux_spotify_state()

        assert state == {"title": "Song Title", "artist": "Some Artist", "status": "Playing", "position": 12.5, "duration": 200.0}

    @pytest.mark.asyncio
    async def test_returns_none_when_spotify_is_not_running(self, mocker):
        mocker.patch("clTerminal.asyncio.create_subprocess_exec", AsyncMock(return_value=_fake_proc(b"chromium.instance1\n")))

        assert await _get_linux_spotify_state() is None
