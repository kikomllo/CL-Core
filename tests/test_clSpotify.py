import pytest, os, sys, json, types, time, asyncio
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def mock_redis(mocker):
    return mocker.MagicMock()

@pytest.fixture(autouse=True)
def local_media_button(mocker):
    """Default: no local Spotify client to press buttons on -- a test run must
    never toggle the real Spotify on the dev machine. Tests of the local path
    set its return_value to True."""
    return mocker.patch("clSpotify.send_spotify_command", return_value=False)

@pytest.fixture(autouse=True)
def isolated_lyrics_cache(mocker, tmp_path):
    """Lyrics lookups are cached under data/lyrics_cache/ -- tests must never
    read or write the real per-user cache."""
    mocker.patch("clSpotify.LYRICS_CACHE_DIR", str(tmp_path / "lyrics_cache"))

@pytest.fixture(autouse=True)
def local_controls(mocker):
    """Same guard for shuffle/repeat/seek: default to "no local client" and
    never touch the real Spotify from a test run."""
    return types.SimpleNamespace(
        shuffle=mocker.patch("clSpotify.set_spotify_shuffle", return_value=False),
        repeat=mocker.patch("clSpotify.set_spotify_repeat", return_value=False),
        seek=mocker.patch("clSpotify.seek_spotify", return_value=False),
    )

@pytest.fixture
def spotify_manager(mock_redis):
    with patch.dict(os.environ, {"SPOTIPY_CLIENT_ID": "dummy", "SPOTIPY_CLIENT_SECRET": "dummy", "SPOTIPY_REDIRECT_URI": "dummy", "LYRICS_YTMUSIC_ENABLED": "0"}):
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


class TestPlayPlaylist:
    """Live failure: 'play my playlist cartooning' (Whisper's hearing of the
    user's 'CarTuning') matched no personal playlist, then Spotify's global
    search returned 'items': [None] and items[0]['uri'] raised
    "'NoneType' object is not subscriptable" -> spoken as an internal error."""

    @pytest.fixture
    def mgr(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, '_ensure_active_device', return_value="device")
        spotify_manager.start = mocker.patch.object(spotify_manager.sp, 'start_playback')
        return spotify_manager

    def _own(self, mocker, mgr, names):
        mocker.patch.object(mgr.sp, 'current_user_playlists', return_value={
            "items": [{"name": n, "uri": f"spotify:playlist:{i}"} for i, n in enumerate(names)]
        })

    def test_misheard_name_finds_the_personal_playlist_by_sound(self, mgr, mocker):
        self._own(mocker, mgr, ["RAPing", "CarTuning", "Jazzyvibing"])
        search = mocker.patch.object(mgr.sp, 'search')

        success, msg = mgr.execute_command("play", playlist_name="cartooning")

        assert success is True and "CarTuning" in msg
        mgr.start.assert_called_once_with(device_id="device", context_uri="spotify:playlist:1")
        search.assert_not_called()

    def test_unrelated_name_is_not_fuzzy_matched(self, mgr, mocker):
        self._own(mocker, mgr, ["RAPing", "Jazzyvibing"])
        mocker.patch.object(mgr.sp, 'search', return_value={"playlists": {"items": []}})

        success, msg = mgr.execute_command("play", playlist_name="cartooning")

        assert success is False and "not found" in msg
        mgr.start.assert_not_called()

    def test_null_entry_in_global_search_is_not_an_internal_error(self, mgr, mocker):
        self._own(mocker, mgr, ["RAPing"])
        mocker.patch.object(mgr.sp, 'search', return_value={"playlists": {"items": [None]}})

        success, msg = mgr.execute_command("play", playlist_name="zzzz unknown mix")

        assert success is False and "Internal Error" not in msg and "not found" in msg

    def test_global_search_skips_null_entries_and_plays_the_first_real_one(self, mgr, mocker):
        self._own(mocker, mgr, ["RAPing"])
        mocker.patch.object(mgr.sp, 'search', return_value={"playlists": {"items": [None, {"uri": "spotify:playlist:g"}]}})

        success, msg = mgr.execute_command("play", playlist_name="zzzz unknown mix")

        assert success is True
        mgr.start.assert_called_once_with(device_id="device", context_uri="spotify:playlist:g")


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

    def test_duck_on_cold_start_trusts_a_low_volume_reading(self, spotify_manager, mocker):
        """Live bug: on ecosystem startup, the 'System Online' TTS line
        duck/unducks before last_known_normal_volume has ever been set. The
        old code only trusted a fresh API reading above 50%, so a real
        volume at or below 50% fell through to 'last_known_normal_volume or
        80' -- None or 80 -- and every startup unduck landed on a hardcoded
        80% regardless of the user's actual volume. With no prior baseline
        to protect against self-contamination, the live reading must always
        be trusted on a cold start."""
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        assert spotify_manager.last_known_normal_volume is None
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 30}, "is_playing": True})
        spotify_manager.execute_command("duck")

        assert spotify_manager.last_known_normal_volume == 30
        assert spotify_manager.pre_duck_volume == 30
        mock_vol.assert_called_with(24, device_id="device")  # 30 * 0.8

        spotify_manager.execute_command("unduck")
        mock_vol.assert_called_with(30, device_id="device")

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

    def test_duck_picks_up_an_external_change_made_outside_the_lag_window(self, spotify_manager, mocker):
        """Live bug: the user lowers volume directly in the Spotify app (not
        through our system), then triggers a wake word ~10s later. The old
        30s self-trust window still covered that gap, so the fresh, correct
        API reading got silently discarded in favor of our stale cached
        baseline -- ducking (and the following unduck) moved volume toward
        the stale value instead of the user's real one. Shrunk to a window
        that only covers genuine Spotify Connect read lag (which resolves
        within a second or two), not several seconds of real elapsed time."""
        mocker.patch.object(spotify_manager, '_get_active_device', return_value="device")
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 82}, "is_playing": True})
        spotify_manager.execute_command("duck")
        spotify_manager.execute_command("unduck")

        # 10s later -- past any real API lag, but still inside the old 30s window.
        spotify_manager._last_duck_cycle_time = time.time() - 10.0
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={"device": {"volume_percent": 60}, "is_playing": True})
        spotify_manager.execute_command("duck")

        assert spotify_manager.pre_duck_volume == 60
        assert spotify_manager.last_known_normal_volume == 60
        mock_vol.assert_called_with(48, device_id="device")  # 60 * 0.8

    def test_explicit_volume_change_survives_the_next_duck_cycle(self, spotify_manager, mocker):
        """Reproduces a live failure: user says 'set volume to 100%' right
        after a duck/unduck cycle. The 'volume' action never updated
        last_known_normal_volume, so the next duck (still inside the
        self-trust window) used the stale pre-change baseline and the
        following unduck silently reverted the user's explicit change."""
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

    def test_volume_change_does_not_wake_or_start_playback(self, spotify_manager, mocker):
        """Live bug: dragging the media widget's volume slider was starting
        the music. The 'volume' action used _ensure_active_device(), which
        calls _wake_up_spotify() (and can issue a real Play) whenever
        nothing is currently active -- exactly the paused/idle state a user
        is likely to be adjusting volume from. It must use a device lookup
        that never wakes playback."""
        mocker.patch.object(spotify_manager, '_get_active_device', return_value=None)
        mock_wake = mocker.patch.object(spotify_manager, '_wake_up_spotify')
        mocker.patch.object(spotify_manager.sp, 'devices', return_value={"devices": [{"id": "device_x", "is_active": False}]})
        mock_vol = mocker.patch.object(spotify_manager.sp, 'volume')

        success, _ = spotify_manager.execute_command("volume", volume=50)

        assert success is True
        mock_wake.assert_not_called()
        mock_vol.assert_called_once_with(50, device_id="device_x")


class TestLightweightStatusPrefersLocalSession:
    """status(lightweight=True) should skip the real Spotify Web API
    entirely when clTerminal.py's free, local Spotify-filtered OS media
    session (jarvis/sys/spotify_local_status) is fresh -- this is the fast
    path for the media widget's regular polling, meant to cut down on API
    request volume."""

    def test_uses_local_session_instead_of_the_api_when_fresh(self, spotify_manager, mocker):
        mock_playback = mocker.patch.object(spotify_manager.sp, 'current_playback')
        spotify_manager.last_known_normal_volume = 70
        spotify_manager._local_status = {
            "found": True, "title": "Song Title", "artist": "Some Artist",
            "status": "Playing", "position": 12.5, "duration": 200.0,
            "shuffle": True, "repeat": "context",
        }
        spotify_manager._local_status_time = time.time()

        result = spotify_manager.status(lightweight=True)

        mock_playback.assert_not_called()
        assert result == {
            "status": "success", "is_playing": True, "volume": 70,
            "track": "Song Title", "artist": "Some Artist", "context": "Unknown",
            "next_in_queue": "Unknown", "progress_ms": 12500, "duration_ms": 200000,
            "shuffle": True, "repeat": "context",
        }

    def test_falls_back_to_the_api_when_local_session_is_stale(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={
            "item": {"name": "API Song", "artists": [{"name": "API Artist"}], "duration_ms": 100000},
            "device": {"volume_percent": 55}, "is_playing": True, "progress_ms": 5000,
        })
        spotify_manager._local_status = {"found": True, "title": "Old Song", "artist": "Old Artist", "status": "Playing", "position": 1.0, "duration": 1.0}
        spotify_manager._local_status_time = time.time() - 20.0  # older than LOCAL_STATUS_FRESHNESS_SECONDS

        result = spotify_manager.status(lightweight=True)

        assert result["track"] == "API Song"

    def test_falls_back_to_the_api_when_local_session_found_nothing(self, spotify_manager, mocker):
        """found=False means clTerminal.py checked and there's no local
        Spotify session (e.g. playback is on a different Connect device) --
        must not be treated as 'nothing is playing'."""
        mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={
            "item": {"name": "API Song", "artists": [{"name": "API Artist"}], "duration_ms": 100000},
            "device": {"volume_percent": 55}, "is_playing": True, "progress_ms": 5000,
        })
        spotify_manager._local_status = {"found": False}
        spotify_manager._local_status_time = time.time()

        result = spotify_manager.status(lightweight=True)

        assert result["track"] == "API Song"

    def test_force_refresh_always_bypasses_the_local_session(self, spotify_manager, mocker):
        mock_playback = mocker.patch.object(spotify_manager.sp, 'current_playback', return_value={
            "item": {"name": "API Song", "artists": [{"name": "API Artist"}], "duration_ms": 100000},
            "device": {"volume_percent": 55}, "is_playing": True, "progress_ms": 5000,
        })
        spotify_manager._local_status = {"found": True, "title": "Local Song", "artist": "Local Artist", "status": "Playing", "position": 1.0, "duration": 1.0}
        spotify_manager._local_status_time = time.time()

        result = spotify_manager.status(lightweight=True, force_refresh=True)

        mock_playback.assert_called_once()
        assert result["track"] == "API Song"


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


class _FakeAiohttpResponse:
    def __init__(self, status, json_data=None):
        self.status = status
        self._json_data = json_data or {}

    async def json(self):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeAiohttpSession:
    """Mimics aiohttp.ClientSession's async-context-manager shape enough
    for fetch_lyrics' single GET call -- both the session itself and the
    object session.get(...) returns are used as `async with` blocks."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc

    def get(self, url, params=None):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class TestParseLrc:
    """LRC parsing for lrclib.net's syncedLyrics field: "[mm:ss.xx]text"
    per line."""

    def test_parses_basic_synced_lines_in_order(self):
        from clSpotify import parse_lrc
        lrc = "[00:12.50]First line\n[00:18.00]Second line"

        result = parse_lrc(lrc)

        assert result == [
            {"time": 12.5, "text": "First line"},
            {"time": 18.0, "text": "Second line"},
        ]

    def test_sorts_out_of_order_lines_by_time(self):
        from clSpotify import parse_lrc
        lrc = "[01:00.00]Later\n[00:05.00]Earlier"

        result = parse_lrc(lrc)

        assert [e["text"] for e in result] == ["Earlier", "Later"]

    def test_skips_metadata_header_lines(self):
        from clSpotify import parse_lrc
        lrc = "[ar:Some Artist]\n[ti:Some Title]\n[00:10.00]Actual lyric"

        result = parse_lrc(lrc)

        assert result == [{"time": 10.0, "text": "Actual lyric"}]

    def test_skips_lines_with_no_text_after_the_timestamp(self):
        from clSpotify import parse_lrc
        lrc = "[00:10.00]\n[00:15.00]Real line"

        result = parse_lrc(lrc)

        assert result == [{"time": 15.0, "text": "Real line"}]

    def test_empty_string_returns_no_entries(self):
        from clSpotify import parse_lrc
        assert parse_lrc("") == []


class _RoutedAiohttpSession:
    """A fake aiohttp session answering by endpoint: handler(endpoint, params)
    -> (status, json). Records every call so tests can count requests."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, params=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params or {})))
        result = self._handler(endpoint, params or {})
        if isinstance(result, Exception):
            raise result
        return _FakeAiohttpResponse(*result)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


SYNCED = "[00:05.00]Hello there"
PLAIN = "First line\nSecond line\n[Hook]\nThird line"


def _lyrics_manager(spotify_manager, mocker, handler):
    session = _RoutedAiohttpSession(handler)
    mocker.patch("clSpotify.aiohttp.ClientSession", return_value=session)
    return session


class TestFetchLyrics:
    """lrclib first (exact lookups, then fuzzy search), plain lyrics with
    estimated times as the fallback, everything cached on disk."""

    @pytest.mark.asyncio
    async def test_returns_synced_lines_on_success(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"syncedLyrics": SYNCED}))

        result = await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert result == {"found": True, "synced": True, "lines": [{"time": 5.0, "text": "Hello there"}]}
        assert len(session.calls) == 1  # stops at the first synced hit

    @pytest.mark.asyncio
    async def test_plain_only_lyrics_are_shown_with_estimated_times(self, spotify_manager, mocker):
        _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))

        result = await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert result["found"] is True and result["synced"] is False
        assert [l["text"] for l in result["lines"]] == ["First line", "Second line", "Third line"]  # [Hook] marker dropped
        times = [l["time"] for l in result["lines"]]
        assert times == sorted(times) and 0 < times[0] and times[-1] < 180

    @pytest.mark.asyncio
    async def test_plain_only_lyrics_can_be_switched_off(self, spotify_manager, mocker):
        spotify_manager.estimate_unsynced_lyrics = False
        _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))

        assert await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0) == {"found": False}

    @pytest.mark.asyncio
    async def test_a_title_with_credits_is_retried_without_them(self, spotify_manager, mocker):
        def handler(endpoint, params):
            if endpoint == "get" and params["track_name"] == "Ric Flair Drip":
                return 200, {"syncedLyrics": SYNCED}
            return 404, {}
        session = _lyrics_manager(spotify_manager, mocker, handler)

        result = await spotify_manager.fetch_lyrics("Ric Flair Drip (with Metro Boomin)", "Offset", 172.0)

        assert result["found"] is True and result["synced"] is True
        assert [c[1]["track_name"] for c in session.calls] == ["Ric Flair Drip (with Metro Boomin)", "Ric Flair Drip"]

    @pytest.mark.asyncio
    async def test_falls_back_to_the_fuzzy_search_when_the_exact_lookups_miss(self, spotify_manager, mocker):
        def handler(endpoint, params):
            if endpoint == "search":
                return 200, [{"duration": 181, "syncedLyrics": SYNCED}, {"duration": 400, "syncedLyrics": "[00:01.00]Wrong version"}]
            return 404, {}
        _lyrics_manager(spotify_manager, mocker, handler)

        result = await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert result == {"found": True, "synced": True, "lines": [{"time": 5.0, "text": "Hello there"}]}

    @pytest.mark.asyncio
    async def test_the_number_of_requests_per_new_track_is_bounded(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (404, {}))

        await spotify_manager.fetch_lyrics("Some Track (feat. X)", "Some Artist, Other", 180.0)

        assert len(session.calls) <= spotify_manager.LRCLIB_MAX_GETS + 1

    @pytest.mark.asyncio
    async def test_returns_found_false_when_nothing_exists(self, spotify_manager, mocker):
        _lyrics_manager(spotify_manager, mocker, lambda e, p: (404, {}))

        assert await spotify_manager.fetch_lyrics("Unknown Track", "Unknown Artist", 180.0) == {"found": False}

    @pytest.mark.asyncio
    async def test_returns_found_false_on_network_error_without_raising(self, spotify_manager, mocker):
        mocker.patch("clSpotify.aiohttp.ClientSession", return_value=_FakeAiohttpSession(raise_exc=ConnectionError("boom")))

        assert await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0) == {"found": False}


class TestLyricsCaching:
    """A track is looked up once, not on every play."""

    @pytest.mark.asyncio
    async def test_a_second_play_makes_no_requests(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"syncedLyrics": SYNCED}))
        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)
        first_run = len(session.calls)

        again = await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert len(session.calls) == first_run
        assert again["found"] is True

    @pytest.mark.asyncio
    async def test_a_clean_miss_is_cached_too(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (404, {}))
        await spotify_manager.fetch_lyrics("Nope", "Nobody", 100.0)
        first_run = len(session.calls)

        await spotify_manager.fetch_lyrics("Nope", "Nobody", 100.0)

        assert len(session.calls) == first_run

    @pytest.mark.asyncio
    async def test_a_network_failure_is_never_cached_as_a_miss(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: ConnectionError("down"))
        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)
        first_run = len(session.calls)

        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert len(session.calls) > first_run  # asked again

    @pytest.mark.asyncio
    async def test_a_rate_limit_is_not_cached_as_a_miss_either(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (429, {}))
        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)
        first_run = len(session.calls)

        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert len(session.calls) > first_run

    @pytest.mark.asyncio
    async def test_the_estimate_setting_applies_to_cached_plain_lyrics_too(self, spotify_manager, mocker):
        _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))
        assert (await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0))["found"] is True

        spotify_manager.estimate_unsynced_lyrics = False

        assert await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0) == {"found": False}

    @pytest.mark.asyncio
    async def test_a_new_provider_gets_a_look_at_tracks_that_had_no_synced_lyrics(self, spotify_manager, mocker):
        session = _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))
        await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        async def better(title, artist, duration):
            return "[00:01.00]Synced by the new provider", None, False
        spotify_manager.lyrics_providers.append(("better", better))
        result = await spotify_manager.fetch_lyrics("Some Track", "Some Artist", 180.0)

        assert result == {"found": True, "synced": True, "lines": [{"time": 1.0, "text": "Synced by the new provider"}]}


class TestLyricsProviderChain:
    @pytest.mark.asyncio
    async def test_the_next_provider_runs_only_when_synced_lyrics_are_missing(self, spotify_manager, mocker):
        calls = []

        async def first(title, artist, duration):
            calls.append("first"); return None, "plain from first", False

        async def second(title, artist, duration):
            calls.append("second"); return "[00:02.00]From second", "plain from second", False
        spotify_manager.lyrics_providers = [("first", first), ("second", second)]

        synced, plain, source, errored = await spotify_manager._lookup_lyrics("T", "A", 100.0)

        assert calls == ["first", "second"]
        assert synced == "[00:02.00]From second" and source == "second"

    @pytest.mark.asyncio
    async def test_a_provider_that_finds_synced_lyrics_ends_the_chain(self, spotify_manager, mocker):
        calls = []

        async def first(title, artist, duration):
            calls.append("first"); return "[00:02.00]Done", None, False

        async def second(title, artist, duration):
            calls.append("second"); return None, None, False
        spotify_manager.lyrics_providers = [("first", first), ("second", second)]

        await spotify_manager._lookup_lyrics("T", "A", 100.0)

        assert calls == ["first"]

    @pytest.mark.asyncio
    async def test_the_first_plain_text_found_is_kept_as_the_fallback(self, spotify_manager, mocker):
        async def first(title, artist, duration):
            return None, "plain from first", False

        async def second(title, artist, duration):
            return None, "plain from second", False
        spotify_manager.lyrics_providers = [("first", first), ("second", second)]

        synced, plain, source, errored = await spotify_manager._lookup_lyrics("T", "A", 100.0)

        assert (synced, plain, source) == (None, "plain from first", "first")

    @pytest.mark.asyncio
    async def test_a_provider_that_raises_is_skipped_and_marks_the_lookup_errored(self, spotify_manager, mocker):
        async def broken(title, artist, duration):
            raise RuntimeError("provider down")

        async def working(title, artist, duration):
            return None, "plain", False
        spotify_manager.lyrics_providers = [("broken", broken), ("working", working)]

        synced, plain, source, errored = await spotify_manager._lookup_lyrics("T", "A", 100.0)

        assert plain == "plain" and errored is True


class TestMaybePublishLyrics:
    """Called on every status poll, but should only actually hit the
    network when the track changes -- the full synced lyric sheet is
    published once per track, and the UI tracks position locally (the
    same way its progress bar already advances between the infrequent
    status polls) to pick the current/next/previous line by index,
    rather than needing this resolved and re-sent on every poll."""

    def _lines(self):
        return [
            {"time": 0.0, "text": "First line"},
            {"time": 10.0, "text": "Second line"},
            {"time": 20.0, "text": "Third line"},
        ]

    @pytest.mark.asyncio
    async def test_fetches_and_publishes_the_full_lyric_sheet_on_first_call(self, spotify_manager, mocker):
        mock_fetch = mocker.patch.object(spotify_manager, "fetch_lyrics", return_value={"found": True, "lines": self._lines()})
        mqtt_client = AsyncMock()

        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track A", "Artist A", 200.0)

        mock_fetch.assert_called_once()
        topic, payload = mqtt_client.publish.call_args[0]
        assert topic == "jarvis/sys/spotify_lyrics"
        assert json.loads(payload) == {
            "title": "Track A", "artist": "Artist A", "found": True,
            "lines": self._lines(),
        }

    @pytest.mark.asyncio
    async def test_does_not_refetch_or_republish_the_same_track_twice(self, spotify_manager, mocker):
        mock_fetch = mocker.patch.object(spotify_manager, "fetch_lyrics", return_value={"found": True, "lines": self._lines()})
        mqtt_client = AsyncMock()

        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track A", "Artist A", 200.0)
        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track A", "Artist A", 200.0)

        mock_fetch.assert_called_once()
        mqtt_client.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_refetches_when_the_track_changes(self, spotify_manager, mocker):
        mock_fetch = mocker.patch.object(spotify_manager, "fetch_lyrics", return_value={"found": False})
        mqtt_client = AsyncMock()

        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track A", "Artist A", 200.0)
        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track B", "Artist B", 210.0)

        assert mock_fetch.call_count == 2
        assert mqtt_client.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_publishes_found_false_when_no_lyrics_available(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "fetch_lyrics", return_value={"found": False})
        mqtt_client = AsyncMock()

        await spotify_manager.maybe_publish_lyrics(mqtt_client, "Track A", "Artist A", 200.0)

        payload = json.loads(mqtt_client.publish.call_args[0][1])
        assert payload == {"title": "Track A", "artist": "Artist A", "found": False}


class TestHandleLocalStatusUpdate:
    """poll_spotify_local_status (clTerminal.py) publishes unconditionally
    on its own ~10s timer, not just on change -- reacting to it here gives
    the UI a steady, push-driven media_status/lyrics refresh instead of
    only ever refreshing on an explicit UI poll or an explicit command
    (which could otherwise go tens of seconds, or longer, without any
    update at all)."""

    def _payload(self, **overrides):
        base = {
            "found": True, "title": "Some Song", "artist": "Some Artist",
            "status": "Playing", "position": 12.5, "duration": 200.0,
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_caches_the_local_status_and_its_timestamp(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch("clSpotify.time.time", return_value=12345.0)
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())

        assert spotify_manager._local_status == self._payload()
        assert spotify_manager._local_status_time == 12345.0

    @pytest.mark.asyncio
    async def test_publishes_a_fresh_media_status_when_found(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        spotify_manager.last_known_normal_volume = 42
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())

        topic, payload = mqtt_client.publish.call_args_list[0].args
        assert topic == "jarvis/sys/media_status"
        assert json.loads(payload) == {
            "title": "Some Song", "artist": "Some Artist",
            "position": 12.5, "duration": 200.0, "status": "Playing", "volume": 42,
        }

    @pytest.mark.asyncio
    async def test_falls_back_to_100_volume_when_none_known_yet(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        spotify_manager.last_known_normal_volume = None
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())

        payload = json.loads(mqtt_client.publish.call_args_list[0].args[1])
        assert payload["volume"] == 100

    @pytest.mark.asyncio
    async def test_also_checks_lyrics_for_the_reported_track(self, spotify_manager, mocker):
        mock_lyrics = mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        spotify_manager.LYRICS_SETTLE_SECONDS = 0
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())
        await asyncio.gather(*spotify_manager._background_tasks)

        mock_lyrics.assert_called_once_with(mqtt_client, "Some Song", "Some Artist", 200.0)

    @pytest.mark.asyncio
    async def test_does_not_publish_anything_when_not_found(self, spotify_manager, mocker):
        mock_lyrics = mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, {"found": False})

        mqtt_client.publish.assert_not_called()
        mock_lyrics.assert_not_called()
        # Still cached, though -- status()'s lightweight fast path needs to
        # know "no local Spotify session" too, not just successful ones.
        assert spotify_manager._local_status == {"found": False}

    @pytest.mark.asyncio
    async def test_heartbeat_with_nothing_changed_is_not_pushed_to_the_ui(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch.object(spotify_manager, "track_queue")
        clock = mocker.patch("clSpotify.time.time", return_value=1000.0)
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(position=10.0))
        clock.return_value = 1010.0  # 10s heartbeat, position advanced in step
        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(position=20.0))

        assert mqtt_client.publish.call_count == 1
        # The heartbeat still refreshes the cache that status() trusts.
        assert spotify_manager._local_status_time == 1010.0
        assert spotify_manager._local_status["position"] == 20.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("change", [
        {"title": "Other Song"}, {"status": "Paused"}, {"shuffle": True}, {"repeat": "track"},
    ])
    async def test_a_changed_field_is_pushed_immediately(self, spotify_manager, mocker, change):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch.object(spotify_manager, "track_queue")
        mocker.patch("clSpotify.time.time", return_value=1000.0)
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())
        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(**change))

        assert mqtt_client.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_a_seek_is_pushed_but_normal_playback_drift_is_not(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch.object(spotify_manager, "track_queue")
        clock = mocker.patch("clSpotify.time.time", return_value=1000.0)
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(position=10.0))
        clock.return_value = 1005.0
        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(position=16.0))  # 1s of drift
        assert mqtt_client.publish.call_count == 1
        clock.return_value = 1006.0
        await spotify_manager.handle_local_status_update(mqtt_client, self._payload(position=120.0))  # seek
        assert mqtt_client.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_reappearing_after_not_found_is_pushed_again(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch.object(spotify_manager, "track_queue")
        mqtt_client = AsyncMock()

        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())
        await spotify_manager.handle_local_status_update(mqtt_client, {"found": False})
        await spotify_manager.handle_local_status_update(mqtt_client, self._payload())

        assert mqtt_client.publish.call_count == 2


class TestLocalMediaButtons:
    """toggle/next/prev press the media button on the local Spotify client
    (utils/clLocalMedia.py) instead of calling the Web API, so the media
    widget and keyboard-style control cost no API quota and no round trip."""

    def _local_status(self, spotify_manager, status):
        spotify_manager._local_status = {"found": True, "title": "T", "artist": "A", "status": status, "position": 1.0, "duration": 100.0}
        spotify_manager._local_status_time = time.time()

    def test_next_and_prev_use_the_local_button_without_the_api(self, spotify_manager, local_media_button, mocker):
        local_media_button.return_value = True
        next_track = mocker.patch.object(spotify_manager.sp, "next_track")
        previous_track = mocker.patch.object(spotify_manager.sp, "previous_track")

        assert spotify_manager.execute_command("next") == (True, "Skipped to next track.")
        assert spotify_manager.execute_command("prev") == (True, "Returned to previous track.")

        assert [c.args for c in local_media_button.call_args_list] == [("next",), ("prev",)]
        next_track.assert_not_called()
        previous_track.assert_not_called()
        assert spotify_manager.last_command_local is True

    def test_toggle_reports_the_state_it_flipped_to_from_the_local_status(self, spotify_manager, local_media_button):
        local_media_button.return_value = True

        self._local_status(spotify_manager, "Playing")
        assert spotify_manager.execute_command("toggle") == (True, "Music paused.")
        assert spotify_manager._local_playing_state is False

        self._local_status(spotify_manager, "Paused")
        assert spotify_manager.execute_command("toggle") == (True, "Resuming playback.")
        assert spotify_manager._local_playing_state is True

    def test_toggle_without_a_fresh_local_status_does_not_guess(self, spotify_manager, local_media_button):
        local_media_button.return_value = True

        assert spotify_manager.execute_command("toggle") == (True, "Toggled playback.")
        assert spotify_manager._local_playing_state is None

    def test_falls_back_to_the_api_when_there_is_no_local_client(self, spotify_manager, local_media_button, mocker):
        local_media_button.return_value = False
        mocker.patch.object(spotify_manager, "_ensure_active_device", return_value="device")
        next_track = mocker.patch.object(spotify_manager.sp, "next_track")

        assert spotify_manager.execute_command("next") == (True, "Skipped to next track.")

        next_track.assert_called_once_with(device_id="device")
        assert spotify_manager.last_command_local is False

    def test_actions_that_are_not_buttons_never_try_the_local_path(self, spotify_manager, local_media_button, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        mocker.patch.object(spotify_manager.sp, "volume")

        spotify_manager.execute_command("volume", volume=30)

        local_media_button.assert_not_called()


class TestShuffleRepeatSeek:
    """shuffle/repeat/seek try the local client first (confirmed by its own
    state) and use the Web API only when that fails."""

    def test_shuffle_goes_local_without_the_api(self, spotify_manager, local_controls, mocker):
        local_controls.shuffle.return_value = True
        api = mocker.patch.object(spotify_manager.sp, "shuffle")

        assert spotify_manager.execute_command("shuffle", enabled=True) == (True, "Shuffle on.")

        local_controls.shuffle.assert_called_once_with(True)
        api.assert_not_called()
        assert spotify_manager.last_command_local is True

    def test_shuffle_falls_back_to_the_api_when_local_fails(self, spotify_manager, local_controls, mocker):
        local_controls.shuffle.return_value = False
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        api = mocker.patch.object(spotify_manager.sp, "shuffle")

        assert spotify_manager.execute_command("shuffle", enabled=False) == (True, "Shuffle off.")

        api.assert_called_once_with(False, device_id="device")
        assert spotify_manager.last_command_local is False

    @pytest.mark.parametrize("mode", ["off", "track", "context"])
    def test_repeat_goes_local_then_api(self, spotify_manager, local_controls, mocker, mode):
        local_controls.repeat.return_value = True
        api = mocker.patch.object(spotify_manager.sp, "repeat")
        assert spotify_manager.execute_command("repeat", mode=mode) == (True, f"Repeat set to {mode}.")
        local_controls.repeat.assert_called_once_with(mode)
        api.assert_not_called()

        local_controls.repeat.return_value = False
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        spotify_manager.execute_command("repeat", mode=mode)
        api.assert_called_once_with(mode, device_id="device")

    def test_an_unknown_repeat_mode_is_rejected_before_anything_runs(self, spotify_manager, local_controls, mocker):
        api = mocker.patch.object(spotify_manager.sp, "repeat")

        success, msg = spotify_manager.execute_command("repeat", mode="forever")

        assert success is False and "off, track or context" in msg
        local_controls.repeat.assert_not_called()
        api.assert_not_called()

    def test_seek_goes_local_and_reports_the_new_position(self, spotify_manager, local_controls, mocker):
        local_controls.seek.return_value = True
        api = mocker.patch.object(spotify_manager.sp, "seek_track")

        assert spotify_manager.execute_command("seek", position_ms=125_900) == (True, "Moved to 2:05.")

        local_controls.seek.assert_called_once_with(125_900)
        api.assert_not_called()

    def test_seek_falls_back_to_the_api_and_clamps_a_negative_position(self, spotify_manager, local_controls, mocker):
        local_controls.seek.return_value = False
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        api = mocker.patch.object(spotify_manager.sp, "seek_track")

        spotify_manager.execute_command("seek", position_ms=-4000)

        api.assert_called_once_with(0, device_id="device")

    def test_without_a_local_client_or_a_device_it_says_so(self, spotify_manager, local_controls, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value=None)

        assert spotify_manager.execute_command("seek", position_ms=1000) == (False, "Spotify is not active.")

    def test_a_missing_argument_is_not_a_recognized_action(self, spotify_manager, local_controls):
        success, msg = spotify_manager.execute_command("shuffle")

        assert success is False and "not recognized" in msg
        local_controls.shuffle.assert_not_called()


class TestLocalCircuitBreaker:
    """After repeated local failures (Spotify not running...) go straight to
    the API for a while instead of paying a failing local attempt every time."""

    def test_skips_the_local_attempt_after_repeated_failures(self, spotify_manager, local_controls, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        mocker.patch.object(spotify_manager.sp, "shuffle")
        local_controls.shuffle.return_value = False

        for _ in range(spotify_manager.LOCAL_FAILURE_LIMIT):
            spotify_manager.execute_command("shuffle", enabled=True)
        local_controls.shuffle.reset_mock()
        spotify_manager.execute_command("shuffle", enabled=True)

        local_controls.shuffle.assert_not_called()

    def test_retries_the_local_path_once_the_skip_window_passes(self, spotify_manager, local_controls, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        mocker.patch.object(spotify_manager.sp, "shuffle")
        local_controls.shuffle.return_value = False
        clock = mocker.patch("clSpotify.time.time", return_value=1000.0)

        for _ in range(spotify_manager.LOCAL_FAILURE_LIMIT):
            spotify_manager.execute_command("shuffle", enabled=True)
        clock.return_value = 1000.0 + spotify_manager.LOCAL_SKIP_SECONDS + 1
        local_controls.shuffle.reset_mock()
        spotify_manager.execute_command("shuffle", enabled=True)

        local_controls.shuffle.assert_called_once()

    def test_a_success_resets_the_failure_count(self, spotify_manager, local_controls, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        mocker.patch.object(spotify_manager.sp, "shuffle")
        outcomes = [False] * (spotify_manager.LOCAL_FAILURE_LIMIT - 1) + [True] + [False] * (spotify_manager.LOCAL_FAILURE_LIMIT - 1)
        local_controls.shuffle.side_effect = outcomes

        for _ in outcomes:
            spotify_manager.execute_command("shuffle", enabled=True)
        local_controls.shuffle.side_effect = None
        local_controls.shuffle.reset_mock()
        spotify_manager.execute_command("shuffle", enabled=True)

        local_controls.shuffle.assert_called_once()  # never tripped the breaker

    def test_a_failing_kind_does_not_disable_the_others(self, spotify_manager, local_controls, local_media_button, mocker):
        mocker.patch.object(spotify_manager, "_get_any_device", return_value="device")
        mocker.patch.object(spotify_manager.sp, "shuffle")
        local_controls.shuffle.return_value = False
        for _ in range(spotify_manager.LOCAL_FAILURE_LIMIT):
            spotify_manager.execute_command("shuffle", enabled=True)
        local_media_button.return_value = True

        assert spotify_manager.execute_command("next") == (True, "Skipped to next track.")


def _queue_response(*names, current="Now"):
    def track(name):
        return {"name": name, "artists": [{"name": "Artist"}], "duration_ms": 200_000, "uri": f"spotify:track:{name}"}
    return {"currently_playing": track(current), "queue": [track(n) for n in names]}


class TestQueue:
    """The queue costs one API request, then is followed locally."""

    def test_the_first_request_fetches_and_later_ones_are_free(self, spotify_manager, mocker):
        fetch = mocker.patch.object(spotify_manager.sp, "queue", return_value=_queue_response("a", "b", "c"))

        assert spotify_manager.execute_command("queue") == (True, "3 tracks up next.")
        assert spotify_manager.execute_command("queue") == (True, "3 tracks up next.")

        fetch.assert_called_once()

    def test_queue_refresh_always_refetches(self, spotify_manager, mocker):
        fetch = mocker.patch.object(spotify_manager.sp, "queue", return_value=_queue_response("a"))

        spotify_manager.execute_command("queue")
        spotify_manager.execute_command("queue_refresh")

        assert fetch.call_count == 2

    def test_a_failed_fetch_backs_off_instead_of_hammering_the_api(self, spotify_manager, mocker):
        fetch = mocker.patch.object(spotify_manager.sp, "queue", side_effect=RuntimeError("boom"))

        assert spotify_manager.execute_command("queue") == (False, "Could not fetch the queue right now.")
        assert spotify_manager.execute_command("queue") == (False, "Could not fetch the queue right now.")

        fetch.assert_called_once()

    def test_the_payload_lists_what_is_up_next(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager.sp, "queue", return_value=_queue_response("a", "b"))
        spotify_manager.execute_command("queue")

        payload = spotify_manager.queue_payload()

        assert payload["remaining"] == 2
        assert [t["title"] for t in payload["queue"]] == ["a", "b"]


class TestTrackQueue:
    """Following playback through the local queue from the free local media
    session; the API is hit only when playback leaves what was expected."""

    def _status(self, title, **extra):
        return {"found": True, "title": title, "artist": "Artist", "status": "Playing", "position": 1.0, "duration": 200.0, **extra}

    def _loaded(self, spotify_manager, mocker, *names):
        mocker.patch.object(spotify_manager.sp, "queue", return_value=_queue_response(*names))
        spotify_manager.execute_command("queue")
        return mocker.patch.object(spotify_manager, "refresh_queue", return_value=True)

    @pytest.mark.asyncio
    async def test_does_nothing_until_a_queue_was_requested(self, spotify_manager, mocker):
        refresh = mocker.patch.object(spotify_manager, "refresh_queue")
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("Whatever"))

        refresh.assert_not_called()
        mqtt_client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_expected_next_track_advances_the_queue_without_the_api(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c", "d")
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("a"))

        refresh.assert_not_called()
        topic, payload = mqtt_client.publish.call_args.args
        assert topic == "jarvis/sys/spotify_queue"
        assert [t["title"] for t in json.loads(payload)["queue"]] == ["b", "c", "d"]
        assert mqtt_client.publish.call_args.kwargs["retain"] is True

    @pytest.mark.asyncio
    async def test_the_same_track_publishes_nothing(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c", "d")
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("Now"))

        refresh.assert_not_called()
        mqtt_client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_track_outside_the_queue_refetches_once(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c", "d")
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("Picked manually"))

        refresh.assert_called_once()
        mqtt_client.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_nearly_used_up_queue_is_refilled(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c")  # advancing leaves 2 == QUEUE_REFILL_AT
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("a"))

        refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_shuffle_change_refetches_because_spotify_reorders_everything(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c", "d")
        mqtt_client = AsyncMock()

        await spotify_manager.track_queue(mqtt_client, self._status("Now", shuffle=False))
        refresh.assert_not_called()  # the first reading is only a baseline
        await spotify_manager.track_queue(mqtt_client, self._status("Now", shuffle=True))

        refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_status_without_shuffle_info_never_counts_as_a_change(self, spotify_manager, mocker):
        refresh = self._loaded(spotify_manager, mocker, "a", "b", "c", "d")

        await spotify_manager.track_queue(AsyncMock(), self._status("Now", shuffle=True))
        await spotify_manager.track_queue(AsyncMock(), self._status("Now"))

        refresh.assert_not_called()


class TestLocalStatusCarriesPlaybackModes:
    @pytest.mark.asyncio
    async def test_shuffle_repeat_and_controls_reach_the_media_status(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        mocker.patch.object(spotify_manager, "track_queue")
        mqtt_client = AsyncMock()
        payload = {"found": True, "title": "T", "artist": "A", "status": "Playing", "position": 1.0, "duration": 2.0,
                   "shuffle": True, "repeat": "track", "can_seek": True, "can_next": True, "can_prev": False}

        await spotify_manager.handle_local_status_update(mqtt_client, payload)

        published = json.loads(mqtt_client.publish.call_args_list[0].args[1])
        assert (published["shuffle"], published["repeat"]) == (True, "track")
        assert (published["can_seek"], published["can_next"], published["can_prev"]) == (True, True, False)

    @pytest.mark.asyncio
    async def test_every_local_update_also_follows_the_queue(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics")
        track = mocker.patch.object(spotify_manager, "track_queue")
        mqtt_client = AsyncMock()
        payload = {"found": True, "title": "T", "artist": "A", "status": "Playing", "position": 1.0, "duration": 2.0}

        await spotify_manager.handle_local_status_update(mqtt_client, payload)

        track.assert_awaited_once_with(mqtt_client, payload)

    def test_the_api_status_path_reports_them_too(self, spotify_manager, mocker):
        mocker.patch.object(spotify_manager.sp, "current_playback", return_value={
            "is_playing": True, "progress_ms": 1000, "shuffle_state": True, "repeat_state": "context",
            "device": {"volume_percent": 40},
            "item": {"name": "Song", "duration_ms": 5000, "artists": [{"name": "A"}]},
        })

        result = spotify_manager.status(lightweight=True, force_refresh=True)

        assert (result["shuffle"], result["repeat"]) == (True, "context")


class TestLyricsWaitForTheDuration:
    """Live bug: on a track change the OS reports the new title a moment
    before the new length -- GEIZA arrived with the previous track's 226s
    instead of its own 136s. lrclib matches on duration, so that lookup found
    nothing, and the lookup was never repeated for the track."""

    def _local(self, title, duration, artist="Artist"):
        return {"found": True, "title": title, "artist": artist, "status": "Playing", "position": 0.3, "duration": duration}

    @pytest.fixture
    def manager(self, spotify_manager, mocker):
        spotify_manager.LYRICS_SETTLE_SECONDS = 0.05
        mocker.patch.object(spotify_manager, "maybe_publish_lyrics", wraps=spotify_manager.maybe_publish_lyrics)
        mocker.patch.object(spotify_manager, "fetch_lyrics", return_value={"found": False})
        return spotify_manager

    @pytest.mark.asyncio
    async def test_a_new_track_is_not_looked_up_with_its_first_duration(self, manager):
        mqtt_client = AsyncMock()

        await manager.handle_local_status_update(mqtt_client, self._local("GEIZA", 226.6))  # the previous track's length

        manager.fetch_lyrics.assert_not_called()  # still settling
        await asyncio.gather(*manager._background_tasks)

    @pytest.mark.asyncio
    async def test_the_lookup_uses_the_duration_the_status_holds_once_it_settled(self, manager):
        mqtt_client = AsyncMock()
        await manager.handle_local_status_update(mqtt_client, self._local("GEIZA", 226.6))
        await manager.handle_local_status_update(mqtt_client, self._local("GEIZA", 136.0))  # the timeline caught up

        await asyncio.gather(*manager._background_tasks)

        manager.fetch_lyrics.assert_called_once_with("GEIZA", "Artist", 136.0)

    @pytest.mark.asyncio
    async def test_a_track_that_changed_again_while_settling_is_not_looked_up(self, manager):
        mqtt_client = AsyncMock()
        await manager.handle_local_status_update(mqtt_client, self._local("First", 100.0))
        await manager.handle_local_status_update(mqtt_client, self._local("Second", 200.0))

        await asyncio.gather(*manager._background_tasks)

        assert [c.args[0] for c in manager.fetch_lyrics.call_args_list] == ["Second"]

    @pytest.mark.asyncio
    async def test_a_late_duration_correction_retries_a_lookup_that_found_nothing(self, manager):
        mqtt_client = AsyncMock()
        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 226.6)  # wrong length: nothing found
        manager.fetch_lyrics.reset_mock()

        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 136.0)

        manager.fetch_lyrics.assert_called_once_with("Song", "Artist", 136.0)

    @pytest.mark.asyncio
    async def test_a_small_duration_difference_is_not_a_correction(self, manager):
        mqtt_client = AsyncMock()
        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 136.0)
        manager.fetch_lyrics.reset_mock()

        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 136.8)

        manager.fetch_lyrics.assert_not_called()

    @pytest.mark.asyncio
    async def test_lyrics_that_were_found_are_never_looked_up_again(self, manager):
        manager.fetch_lyrics.return_value = {"found": True, "synced": True, "lines": [{"time": 1.0, "text": "x"}]}
        mqtt_client = AsyncMock()
        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 226.6)
        manager.fetch_lyrics.reset_mock()

        await manager.maybe_publish_lyrics(mqtt_client, "Song", "Artist", 136.0)

        manager.fetch_lyrics.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_error_in_the_background_lookup_does_not_escape(self, manager):
        manager.fetch_lyrics.side_effect = RuntimeError("boom")
        mqtt_client = AsyncMock()
        await manager.handle_local_status_update(mqtt_client, self._local("GEIZA", 136.0))

        await asyncio.gather(*manager._background_tasks)  # must not raise

        assert manager._lyrics_settling is None


class TestYtMusicProviderWiring:
    """On by default (no login needed), switchable off, and a missing
    library must never break the service."""

    def _manager(self, env):
        with patch.dict(os.environ, {"SPOTIPY_CLIENT_ID": "dummy", "SPOTIPY_CLIENT_SECRET": "dummy", "SPOTIPY_REDIRECT_URI": "dummy", **env}):
            with patch('spotipy.SpotifyOAuth'), patch('spotipy.Spotify'):
                from clSpotify import SpotifyManager
                return SpotifyManager()

    def test_on_by_default_after_lrclib(self):
        manager = self._manager({"LYRICS_YTMUSIC_ENABLED": "1"})
        assert [name for name, _ in manager.lyrics_providers] == ["lrclib", "ytmusic"]

    def test_can_be_switched_off(self):
        manager = self._manager({"LYRICS_YTMUSIC_ENABLED": "0"})
        assert [name for name, _ in manager.lyrics_providers] == ["lrclib"]

    def test_a_missing_library_leaves_the_rest_working(self, mocker):
        mocker.patch.dict(sys.modules, {"ytmusicapi": None})  # makes `import ytmusicapi` raise ImportError

        manager = self._manager({"LYRICS_YTMUSIC_ENABLED": "1"})

        assert [name for name, _ in manager.lyrics_providers] == ["lrclib"]


class TestYtMusicInTheChain:
    @pytest.fixture
    def manager(self, spotify_manager, mocker):
        spotify_manager._ytmusic = MagicMock()
        spotify_manager._ytmusic.find = MagicMock(return_value=("[00:03.00]From YouTube Music", "plain", False))
        spotify_manager.lyrics_providers.append(("ytmusic", spotify_manager._lookup_ytmusic))
        return spotify_manager

    @pytest.mark.asyncio
    async def test_it_is_asked_only_when_lrclib_has_no_synced_lyrics(self, manager, mocker):
        _lyrics_manager(manager, mocker, lambda e, p: (404, {}))

        result = await manager.fetch_lyrics("GEIZA", "ProfJam", 136.0)

        assert result == {"found": True, "synced": True, "lines": [{"time": 3.0, "text": "From YouTube Music"}]}
        manager._ytmusic.find.assert_called_once_with("GEIZA", "ProfJam", 136.0)

    @pytest.mark.asyncio
    async def test_it_is_never_asked_when_lrclib_already_has_them(self, manager, mocker):
        _lyrics_manager(manager, mocker, lambda e, p: (200, {"syncedLyrics": SYNCED}))

        await manager.fetch_lyrics("GEIZA", "ProfJam", 136.0)

        manager._ytmusic.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_synced_beats_lrclibs_plain_text(self, manager, mocker):
        """The GEIZA case: lrclib only has plain lyrics, YouTube Music has them timed."""
        _lyrics_manager(manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))

        result = await manager.fetch_lyrics("GEIZA", "ProfJam", 136.0)

        assert result["synced"] is True

    @pytest.mark.asyncio
    async def test_when_it_has_nothing_lrclibs_plain_lyrics_still_show(self, manager, mocker):
        manager._ytmusic.find.return_value = (None, None, False)
        _lyrics_manager(manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))

        result = await manager.fetch_lyrics("Zona Mista", "Wet Bed Gang", 207.0)

        assert result["found"] is True and result["synced"] is False

    @pytest.mark.asyncio
    async def test_a_failure_there_is_not_cached_as_having_no_lyrics(self, manager, mocker):
        manager._ytmusic.find.return_value = (None, None, True)  # paused / failed
        session = _lyrics_manager(manager, mocker, lambda e, p: (404, {}))
        await manager.fetch_lyrics("GEIZA", "ProfJam", 136.0)
        first_run = len(session.calls)
        manager._ytmusic.find.return_value = ("[00:03.00]Back", None, False)

        result = await manager.fetch_lyrics("GEIZA", "ProfJam", 136.0)

        assert len(session.calls) > first_run and result["synced"] is True

    @pytest.mark.asyncio
    async def test_a_hung_lookup_times_out_instead_of_stalling(self, manager, mocker):
        manager.YTMUSIC_TIMEOUT_SECONDS = 0.05
        manager._ytmusic.find = MagicMock(side_effect=lambda *a: time.sleep(0.5))

        assert await manager._lookup_ytmusic("GEIZA", "ProfJam", 136.0) == (None, None, True)

    @pytest.mark.asyncio
    async def test_adding_it_reopens_tracks_cached_without_synced_lyrics(self, spotify_manager, mocker):
        _lyrics_manager(spotify_manager, mocker, lambda e, p: (200, {"plainLyrics": PLAIN}) if e == "get" else (404, {}))
        assert (await spotify_manager.fetch_lyrics("GEIZA", "ProfJam", 136.0))["synced"] is False

        spotify_manager._ytmusic = MagicMock()
        spotify_manager._ytmusic.find = MagicMock(return_value=("[00:03.00]Now synced", None, False))
        spotify_manager.lyrics_providers.append(("ytmusic", spotify_manager._lookup_ytmusic))

        assert (await spotify_manager.fetch_lyrics("GEIZA", "ProfJam", 136.0))["synced"] is True
