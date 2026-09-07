import pytest, os, sys, json, types, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def mock_redis(mocker):
    return mocker.MagicMock()

@pytest.fixture
def spotify_manager(mock_redis):
    with patch.dict(os.environ, {"SPOTIPY_CLIENT_ID": "dummy", "SPOTIPY_CLIENT_SECRET": "dummy", "SPOTIPY_REDIRECT_URI": "dummy"}):
        with patch('spotipy.SpotifyOAuth'), patch('spotipy.Spotify'):
            from clSpotify import SpotifyManager
            m = SpotifyManager()
            m.redis = mock_redis
            return m

class TestPlayArtistFuzzy:
    """_play_artist_fuzzy previously trusted Spotify's top artist-search
    result unconditionally -- live failure: searching for 'Plutonio' returned
    an unrelated artist ('Dillaz') as the sole result, and it played anyway."""

    def test_plays_a_genuine_match(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device")
        mock_start = mocker.patch.object(spotify_manager.sp, 'start_playback')
        mocker.patch.object(spotify_manager.sp, 'search', return_value={
            "artists": {"items": [{"name": "Plutonio", "uri": "spotify:artist:1"}]}
        })

        success, msg = spotify_manager.execute_command("play", artist_name="Plutonio")

        assert success is True
        mock_start.assert_called_once_with(device_id="device", context_uri="spotify:artist:1")

    def test_rejects_an_unrelated_top_result(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device")
        mock_start = mocker.patch.object(spotify_manager.sp, 'start_playback')
        mocker.patch.object(spotify_manager.sp, 'search', return_value={
            "artists": {"items": [{"name": "Dillaz", "uri": "spotify:artist:2"}]}
        })

        success, msg = spotify_manager.execute_command("play", artist_name="Plutonio")

        assert success is False
        mock_start.assert_not_called()


class TestSpotify:
    def test_duck(self, spotify_manager, mocker):
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 80}, "is_playing": True})
        spotify_manager.execute_command("duck")
        mock_vol.assert_called_once_with(64, device_id="device")

    def test_track_search_and_cache(self, spotify_manager, mocker):
        mock_search = mocker.patch.object(spotify_manager.sp, 'search')
        mock_search.return_value = {
            "tracks": {
                "items": [
                    {"name": "Song A", "artists": [{"name": "Artist 1"}], "uri": "spotify:track:1", "popularity": 50},
                ]
            }
        }
        
        # Override the logic directly on the object to bypass real redis calls since we just want to verify logic flow
        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device_123")
        mocker.patch.object(spotify_manager, '_calculate_confidence', return_value=0.5)
        spotify_manager.perfect_match_threshold = 2.0  # Force it to fall through to cache
        spotify_manager.confidence_threshold = 2.0
        
        mock_hset = mocker.patch.object(spotify_manager.redis, 'hset')
        success, feedback = spotify_manager.execute_command("play", track_name="Song A")
        
        # Verify it dropped into the cache logic
        assert success is True, f"Failed: {feedback}"
        assert "CONFIDENCE_LOW|" in feedback

    def test_duck_prevents_volume_collapse(self, spotify_manager, mocker):
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        
        # Initial playback at 94%
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 94}, "is_playing": True})
        spotify_manager.execute_command("duck")
        assert spotify_manager.last_known_normal_volume == 94
        assert spotify_manager.pre_duck_volume == 94
        mock_vol.assert_called_with(75, device_id="device")

        # Unduck called
        spotify_manager.execute_command("unduck")
        assert spotify_manager.pre_duck_volume is None

        # Rapid second duck while current_playback still reports ducked 28%
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 28}, "is_playing": True})
        spotify_manager.execute_command("duck")
        assert spotify_manager.pre_duck_volume == 94  # Uses preserved last_known_normal_volume
        mock_vol.assert_called_with(75, device_id="device")

    def test_duck_always_forces_a_fresh_playback_read(self, spotify_manager, mocker):
        """_handle_ducking's 'duck' branch must bypass _get_current_playback's
        3-second cache -- during a rapid multi-turn exchange (e.g. the
        Spotify CONFIDENCE_LOW choice flow), an unduck can restore the real
        volume and then a duck can follow within that same 3-second window.
        A cached (pre-restore) reading there would make the code adopt the
        stale, already-lowered value as the new 'normal', permanently
        ratcheting the resting volume down on every duck/unduck cycle."""
        mock_get_playback = mocker.patch.object(
            spotify_manager, '_get_current_playback',
            return_value={"device": {"volume_percent": 68}, "is_playing": True}
        )
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mocker.patch.object(spotify_manager.sp, 'volume')

        spotify_manager.execute_command("duck")

        mock_get_playback.assert_called_once_with(force_refresh=True)

    def test_duck_trusts_own_state_over_a_stale_but_above_50_api_reading(self, spotify_manager, mocker):
        """Reproduces a live failure: Spotify Connect's own volume state can
        lag behind a PUT we just issued (e.g. an unduck to 82%), so a
        genuinely fresh GET immediately after can still report the
        pre-restore value -- observed live as an unduck-to-82% followed
        within the same second by a duck reading back 65%. Since 65 > 50,
        the old code's ducked-value heuristic never caught it and adopted 65
        as the new 'normal', permanently lowering the resting volume. A duck
        within 30s of our own last duck/unduck must trust our own record
        instead of re-trusting the API's volume_percent."""
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        # First duck/unduck cycle establishes the true baseline of 82%.
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 82}, "is_playing": True})
        spotify_manager.execute_command("duck")
        assert spotify_manager.last_known_normal_volume == 82
        spotify_manager.execute_command("unduck")

        # Immediately after, Spotify's own API still reports the stale
        # pre-restore reading (65%, itself >50 so the old heuristic trusted it).
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 65}, "is_playing": True})
        spotify_manager.execute_command("duck")

        assert spotify_manager.pre_duck_volume == 82
        mock_vol.assert_called_with(65, device_id="device")  # 82 * 0.8

    def test_duck_reverts_to_api_reading_after_a_long_gap(self, spotify_manager, mocker):
        """Once enough time has passed since our own last duck/unduck, the
        user could plausibly have changed the volume themselves -- the API
        reading should be trusted again rather than a far-stale local value."""
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 82}, "is_playing": True})
        spotify_manager.execute_command("duck")
        spotify_manager.execute_command("unduck")

        # Simulate a long gap since our last self-initiated change.
        spotify_manager._last_duck_cycle_time = time.time() - 60.0
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 70}, "is_playing": True})
        spotify_manager.execute_command("duck")

        assert spotify_manager.pre_duck_volume == 70
        mock_vol.assert_called_with(56, device_id="device")  # 70 * 0.8

    def test_explicit_volume_change_survives_the_next_duck_cycle(self, spotify_manager, mocker):
        """Reproduces a live failure: user says 'set volume to 100%' right
        after a duck/unduck cycle. The 'volume' action never updated
        last_known_normal_volume, so the next duck (still inside the 30s
        self-trust window) used the stale pre-change baseline and the
        following unduck silently reverted the user's explicit change."""
        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device")
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 87}, "is_playing": True})
        spotify_manager.execute_command("duck")
        spotify_manager.execute_command("unduck")

        spotify_manager.execute_command("volume", volume=100)
        assert spotify_manager.last_known_normal_volume == 100

        # API still reports the stale 87% immediately after our own PUT.
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 87}, "is_playing": True})
        spotify_manager.execute_command("duck")
        assert spotify_manager.pre_duck_volume == 100
        mock_vol.assert_called_with(80, device_id="device")  # 100 * 0.8

        spotify_manager.execute_command("unduck")
        mock_vol.assert_called_with(100, device_id="device")

class TestSpotifyEdgeCases:
    def test_search_zero_results(self, spotify_manager, mocker):
        mock_search = mocker.patch.object(spotify_manager.sp, 'search')
        mock_search.return_value = {"tracks": {"items": []}}

        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device_123")
        success, feedback = spotify_manager.execute_command("play", track_name="Nonexistent Song That Nobody Made")

        assert success is False
        assert "could not find" in feedback.lower() or "not found" in feedback.lower() or "0" in feedback

    def test_api_unauthorized_token_refresh(self, spotify_manager, mocker):
        from spotipy.exceptions import SpotifyException
        # Simulate a 401 Unauthorized exception
        mock_search = mocker.patch.object(spotify_manager.sp, 'search')
        mock_search.side_effect = SpotifyException(401, -1, "The access token expired")

        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device_123")
        success, feedback = spotify_manager.execute_command("play", track_name="Song A")

        # It should catch the exception and return a clean error without crashing
        assert success is False
        assert "spotify error" in feedback.lower() or "token" in feedback.lower() or "401" in feedback.lower() or "expired" in feedback.lower()

    def test_bare_play_403_on_freshly_woken_device_is_not_reported_as_already_playing(self, spotify_manager, mocker):
        """A freshly woken device with no prior queue/context also gets refused
        with a bare 403 -- that must not be reported as 'already playing' when
        nothing is actually playing."""
        from spotipy.exceptions import SpotifyException

        mocker.patch.object(spotify_manager, '_get_active_device', side_effect=[None, "device_123"])
        mocker.patch.object(spotify_manager, '_wake_up_spotify')
        mocker.patch.object(spotify_manager, '_get_current_playback', return_value=None)
        mocker.patch.object(
            spotify_manager.sp, 'start_playback',
            side_effect=SpotifyException(403, -1, "Player command failed: Restriction violated")
        )

        success, feedback = spotify_manager.execute_command("play")

        assert success is False
        assert "already playing" not in feedback.lower()

    def test_bare_play_403_while_genuinely_playing_reports_already_playing(self, spotify_manager, mocker):
        from spotipy.exceptions import SpotifyException

        mocker.patch.object(spotify_manager, '_get_active_device', side_effect=[None, "device_123"])
        mocker.patch.object(spotify_manager, '_wake_up_spotify')
        mocker.patch.object(spotify_manager, '_get_current_playback', return_value={"is_playing": True})
        mocker.patch.object(
            spotify_manager.sp, 'start_playback',
            side_effect=SpotifyException(403, -1, "Player command failed: Restriction violated")
        )

        success, feedback = spotify_manager.execute_command("play")

        assert success is False
        assert "already playing" in feedback.lower()


def _stub_module_tree(mocker, dotted_names_to_modules: dict):
    """Registers fake modules in sys.modules for every level of a dotted
    path (e.g. 'winsdk.windows.media.control'), so `from a.b.c import X`
    succeeds without the real (often platform-only) package installed."""
    mocker.patch.dict(sys.modules, dotted_names_to_modules)


@pytest.fixture
def fake_winsdk_smtc(mocker):
    """Stubs winsdk's SMTC (System Media Transport Controls) module tree,
    so the Windows-only direct-Spotify-targeting code can be tested on any
    OS without the real (Windows-only) winsdk package installed."""
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

    _stub_module_tree(mocker, {
        "winsdk": winsdk_module,
        "winsdk.windows": windows_module,
        "winsdk.windows.media": media_module,
        "winsdk.windows.media.control": control_module,
    })
    return fake_manager


def _fake_smtc_session(app_user_model_id: str):
    session = MagicMock()
    session.source_app_user_model_id = app_user_model_id
    session.try_play_async = AsyncMock()
    return session


class TestWakeUpSpotifyWindowsDirectTargeting:
    """_wake_up_spotify_windows must target Spotify's own SMTC session
    directly, instead of a generic OS media key the OS could route to
    whatever app currently owns media focus."""

    def test_sends_play_to_the_spotify_session_only(self, spotify_manager, fake_winsdk_smtc):
        spotify_session = _fake_smtc_session("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
        other_session = _fake_smtc_session("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic")
        fake_winsdk_smtc.get_sessions.return_value = [other_session, spotify_session]

        result = spotify_manager._wake_up_spotify_windows()

        assert result is True
        spotify_session.try_play_async.assert_awaited_once()
        other_session.try_play_async.assert_not_called()

    def test_returns_false_when_no_spotify_session_exists(self, spotify_manager, fake_winsdk_smtc):
        fake_winsdk_smtc.get_sessions.return_value = [_fake_smtc_session("Some.OtherApp")]

        assert spotify_manager._wake_up_spotify_windows() is False

    def test_returns_false_when_no_sessions_at_all(self, spotify_manager, fake_winsdk_smtc):
        fake_winsdk_smtc.get_sessions.return_value = []

        assert spotify_manager._wake_up_spotify_windows() is False

    def test_returns_false_when_winsdk_not_installed(self, spotify_manager, mocker):
        # Forces ImportError regardless of whether winsdk happens to be
        # installed in the environment running this test.
        mocker.patch.dict(sys.modules, {"winsdk.windows.media.control": None})

        assert spotify_manager._wake_up_spotify_windows() is False


@pytest.fixture
def fake_winreg(mocker):
    """Stubs the Windows-only winreg module so the registry-lookup fallback
    in _launch_spotify_app can be tested on any OS."""
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 1
    fake.HKEY_LOCAL_MACHINE = 2
    fake.OpenKey = MagicMock(side_effect=FileNotFoundError)
    fake.QueryValueEx = MagicMock()
    mocker.patch.dict(sys.modules, {"winreg": fake})
    return fake


class TestLaunchSpotifyAppWindows:
    """_launch_spotify_app replaces config/system.json's static 'spotify.exe
    on PATH' assumption (false for both Spotify's default installer, which
    doesn't add itself to PATH, and the Microsoft Store package, which has
    no discoverable .exe at all) with a real search."""

    def test_finds_classic_per_user_installer_path(self, spotify_manager, mocker, fake_winreg):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"})
        expected_path = r"C:\Users\Test\AppData\Roaming\Spotify\Spotify.exe"
        mocker.patch("clSpotify.os.path.exists", side_effect=lambda p: p == expected_path)
        mocker.patch("shutil.which", return_value=None)
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_popen.assert_called_once_with([expected_path])

    def test_falls_back_to_registry_install_location(self, spotify_manager, mocker, fake_winreg):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"})
        registry_path = r"D:\Games\Spotify\Spotify.exe"
        fake_winreg.OpenKey = MagicMock()  # first hive succeeds, no FileNotFoundError
        fake_winreg.QueryValueEx.return_value = (r"D:\Games\Spotify", 1)
        mocker.patch("clSpotify.os.path.exists", side_effect=lambda p: p == registry_path)
        mocker.patch("shutil.which", return_value=None)
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_popen.assert_called_once_with([registry_path])

    def test_falls_back_to_path_lookup(self, spotify_manager, mocker, fake_winreg):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch.dict(os.environ, {"APPDATA": ""})
        which_path = r"C:\Users\Test\AppData\Local\Microsoft\WindowsApps\spotify.exe"
        mocker.patch("clSpotify.os.path.exists", side_effect=lambda p: p == which_path)
        mocker.patch("shutil.which", return_value=which_path)
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_popen.assert_called_once_with([which_path])

    def test_falls_back_to_store_app_via_aumid_when_nothing_on_disk(self, spotify_manager, mocker, fake_winreg):
        """The Microsoft Store package has no .exe on disk or in the
        uninstall registry at all -- only Get-AppxPackage can find it."""
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"})
        mocker.patch("clSpotify.os.path.exists", return_value=False)
        mocker.patch("shutil.which", return_value=None)
        mock_run = mocker.patch("subprocess.run", return_value=MagicMock(stdout="SpotifyAB.SpotifyMusic_zpdnekdrzrea0\n"))
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_run.assert_called_once()
        mock_popen.assert_called_once_with(["explorer.exe", r"shell:AppsFolder\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"])

    def test_returns_false_when_nothing_found_anywhere(self, spotify_manager, mocker, fake_winreg):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"})
        mocker.patch("clSpotify.os.path.exists", return_value=False)
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("subprocess.run", return_value=MagicMock(stdout=""))
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is False
        mock_popen.assert_not_called()


class TestLaunchSpotifyAppLinux:
    def test_uses_path_lookup_first(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "linux")
        mocker.patch("shutil.which", return_value="/usr/bin/spotify")
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_popen.assert_called_once_with(["/usr/bin/spotify"])

    def test_falls_back_to_flatpak(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "linux")
        mocker.patch("shutil.which", side_effect=lambda name: "/usr/bin/flatpak" if name == "flatpak" else None)
        mocker.patch("subprocess.run", return_value=MagicMock(stdout="com.spotify.Client\norg.other.App\n"))
        mock_popen = mocker.patch("subprocess.Popen")

        result = spotify_manager._launch_spotify_app()

        assert result is True
        mock_popen.assert_called_once_with(["flatpak", "run", "com.spotify.Client"])

    def test_returns_false_when_nothing_found(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "linux")
        mocker.patch("shutil.which", return_value=None)

        assert spotify_manager._launch_spotify_app() is False


class TestWakeUpSpotifyEscalation:
    """_wake_up_spotify must escalate: wake an already-running client first,
    then launch it dynamically if it isn't running at all, only falling
    back to a blind media key as a last resort."""

    def test_windows_launches_app_and_retries_after_no_session_found(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch("clSpotify.time.sleep")
        mock_wake = mocker.patch.object(spotify_manager, "_wake_up_spotify_windows", side_effect=[False, True])
        mock_launch = mocker.patch.object(spotify_manager, "_launch_spotify_app", return_value=True)
        mock_pyautogui = mocker.patch("pyautogui.press")

        spotify_manager._wake_up_spotify()

        assert mock_wake.call_count == 2
        mock_launch.assert_called_once()
        mock_pyautogui.assert_not_called()

    def test_windows_falls_back_to_media_key_when_no_install_found(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch("clSpotify.time.sleep")
        mocker.patch.object(spotify_manager, "_wake_up_spotify_windows", return_value=False)
        mocker.patch.object(spotify_manager, "_launch_spotify_app", return_value=False)
        mock_pyautogui = mocker.patch("pyautogui.press")

        spotify_manager._wake_up_spotify()

        mock_pyautogui.assert_called_once_with('playpause')

    def test_windows_skips_launch_when_session_found_immediately(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "win32")
        mocker.patch("clSpotify.time.sleep")
        mocker.patch.object(spotify_manager, "_wake_up_spotify_windows", return_value=True)
        mock_launch = mocker.patch.object(spotify_manager, "_launch_spotify_app")

        spotify_manager._wake_up_spotify()

        mock_launch.assert_not_called()

    def test_linux_launches_app_when_dbus_service_missing(self, spotify_manager, mocker):
        mocker.patch("clSpotify.sys.platform", "linux")
        mocker.patch("clSpotify.time.sleep")
        mock_run = mocker.patch(
            "subprocess.run",
            side_effect=[MagicMock(returncode=1), MagicMock(returncode=0)]
        )
        mock_launch = mocker.patch.object(spotify_manager, "_launch_spotify_app", return_value=True)

        spotify_manager._wake_up_spotify()

        mock_launch.assert_called_once()
        assert mock_run.call_count == 2
