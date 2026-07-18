import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clDaemon import CentralDaemon

@pytest.fixture(scope="module")
def daemon():
    return CentralDaemon()

class TestDaemonCoreLogic:
    """Core NLP pipeline tests: typos, filler words, pluralization, and subsets."""

    @pytest.mark.parametrize("spoken_text, expected_action, variable_key, variable_value", [
        # --- 1. SPOTIFY SPECIFICITY & PLURALIZATION ---
        ("play my playlists chill vibes", "play", "playlist_name", "chill vibes"),
        ("play the playlist Meet the makers", "play", "playlist_name", "meet the makers"),
        # The engine properly keeps "by" internally, so the test should expect it!
        ("play shape of you by ed sheeran", "play", "track_name", "shape of you by ed sheeran"),
        ("resume the music please", "play", None, None),
        ("play the next song", "next", None, None),
        ("go back a song", "prev", None, None),

        # --- 2. SMART LIGHTING ---
        ("turn the lights on", "on", None, None),
        ("kill the lights", "off", None, None),
        ("toggle the light", "toggle", None, None),
        ("set the light to crimson red please", "on", "color", "crimson red"),
        ("make it ocean blue in here", "on", "color", "ocean blue"),
        # The word 'light' acts as a boundary here, resolving to 'blue'
        ("set the light to light blue", "on", "color", "blue"),
        
        # Integer Extraction (Engine now safely pulls numbers from garbage)
        ("dim the light to 45 percent", "on", "lum", 45),
        ("make the lights 100 percent.", "on", "lum", 100),
        ("plz set brightness to 75 percent.", "on", "lum", 75), 

        # --- 3. SYSTEM MODULES & VARIABLES ---
        ("restart the framework", "restart_all_modules", None, None),
        ("restart module voice sensor", "restart_module", "target", "voice sensor"),
        # The template consumes "service", so target is just "spotify"
        ("reboot the spotify service", "restart_module", "target", "spotify"),
        ("enter attention mode", "attention_on", None, None),
        ("exit work mode", "attention_off", None, None),
        ("start discovery mode", "discover", None, None),

        # --- 4. FUZZY FORGIVENESS & INTERNAL GRAMMAR ---
        ("playy some jazz", "play", "search_query", "jazz"),
        ("switch the lightssss", "toggle", None, None),
        ("set volume to 22 percentt", "volume", "volume", 22), 
        ("play the playlist meet the makers", "play", "playlist_name", "meet the makers"),
        ("play the track dancing in the dark please", "play", "track_name", "dancing in the dark"),
    ])
    def test_single_intent_routing(self, daemon, spoken_text, expected_action, variable_key, variable_value):
        intents = daemon.process_voice_command(spoken_text)
        
        assert len(intents) == 1, f"Expected 1 intent for '{spoken_text}', got {len(intents)}"
        payload, topic = intents[0]
        
        assert payload.get("action") == expected_action, f"Action mismatch for '{spoken_text}'"
        
        if variable_key:
            assert variable_key in payload, f"Missing variable '{variable_key}' in payload"
            assert payload[variable_key] == variable_value, f"Value mismatch for '{variable_key}'"