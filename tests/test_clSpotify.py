import pytest, os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from unittest.mock import patch

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
