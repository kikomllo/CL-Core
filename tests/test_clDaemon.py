import pytest
import os
import sys
import json
import asyncio
import time
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clDaemon import CentralDaemon

@pytest.fixture
def daemon():
    return CentralDaemon()

class TestDaemonCoreLogic:
    """Core NLP pipeline tests: typos, filler words, pluralization, and subsets."""

    @pytest.mark.parametrize("spoken_text, expected_action, variable_key, variable_value", [
        # --- 1. SPOTIFY SPECIFICITY & PLURALIZATION ---
        ("play my playlists chill vibes", "play", "playlist_name", "chill vibes"),
        ("play the playlist Meet the makers", "play", "playlist_name", "meet the makers"),
        ("play shape of you by ed sheeran", "play", "track_name", "shape of you"),
        ("resume the music please", "play", None, None),
        ("play the next song", "next", None, None),
        ("go back a song", "prev", None, None),

        # --- 2. SMART LIGHTING ---
        ("turn the lights on", "on", None, None),
        ("kill the lights", "off", None, None),
        ("toggle the light", "toggle", None, None),
        ("set the light to crimson red please", "on", "color", "crimson red"),
        ("make it ocean blue in here", "on", "color", "ocean blue"),
        ("set the light to light blue", "on", "color", "light blue"),

        # Integer Extraction
        ("dim the light to 45 percent", "on", "lum", 45),
        ("make the lights 100 percent.", "on", "lum", 100),
        ("plz set brightness to 75 percent.", "on", "lum", 75),

        # --- 3. SYSTEM MODULES & VARIABLES ---
        ("enter attention mode", "attention_on", None, None),
        ("exit work mode", "attention_off", None, None),
        ("start discovery mode", "discover", None, None),

        # --- 4. FUZZY FORGIVENESS & INTERNAL GRAMMAR ---
        ("playy some jazz", "play", "search_query", "jazz"),
        ("switch the lightssss", "toggle", None, None),
        ("set volume to 22 percentt", "volume", "volume", 22),
        ("play the track dancing in the dark please", "play", "track_name", "dancing in the dark"),
    ])
    @pytest.mark.asyncio
    async def test_single_intent_routing(self, daemon, spoken_text, expected_action, variable_key, variable_value):
        intents, _ = await daemon.route_voice_command(spoken_text)
        
        assert len(intents) == 1, f"Expected 1 intent for '{spoken_text}', got {len(intents)}"
        payload, topic = intents[0]
        
        assert payload.get("action") == expected_action, f"Action mismatch for '{spoken_text}'"
        
        if variable_key:
            assert variable_key in payload, f"Missing variable '{variable_key}' in payload"
            assert payload[variable_key] == variable_value, f"Value mismatch for '{variable_key}'"

    # system_restart_module/system_restart_all have no action_args in intents.json,
    # so their payloads never carry an "action" key -- checked via action_id instead
    # of the generic action-field pattern test_single_intent_routing above uses.
    @pytest.mark.asyncio
    @pytest.mark.parametrize("spoken_text, expected_target", [
        ("restart module voice sensor", "voice sensor"),
        ("reboot the spotify service", "spotify"),
    ])
    async def test_restart_module_routing(self, daemon, spoken_text, expected_target):
        intents, _ = await daemon.route_voice_command(spoken_text)
        assert len(intents) == 1
        payload, action_id = intents[0]
        assert action_id == "system.restart_module"
        assert payload.get("target") == expected_target

    @pytest.mark.asyncio
    async def test_restart_all_exact_template_match(self, daemon):
        intents, _ = await daemon.route_voice_command("restart the framework")
        assert len(intents) == 1
        assert intents[0][1] == "system.restart_all"

class TestDaemonStateTraps:
    """Tests context-aware locks that override standard NLP routing using the new Unified State Machine."""

    @pytest.mark.asyncio
    async def test_global_abort_trap(self, daemon):
        intents, _ = await daemon.route_voice_command("abort sequence")
        assert len(intents) == 1
        assert intents[0][0]["action"] == "abort"
        assert intents[0][1] == "system.abort"
        assert daemon.active_context["type"] is None

    @pytest.mark.asyncio
    async def test_spotify_choice_trap(self, daemon):
        daemon.active_context = {"type": "spotify_choice", "expires_at": time.time() + 20.0}
        intents, _ = await daemon.route_voice_command("3")

        assert len(intents) == 1
        assert intents[0][1] == "spotify.control"
        assert intents[0][0] == {"action": "play_choice", "choice_index": 3}
        assert daemon.active_context["type"] is None

    @pytest.mark.asyncio
    async def test_discovery_choice_trap(self, daemon):
        daemon.active_context = {"type": "discovery_choice", "expires_at": time.time() + 20.0}
        intents, _ = await daemon.route_voice_command("1")

        assert len(intents) == 1
        assert intents[0][1] == "system.discovery"
        assert intents[0][0] == {"action": "save_discovery", "index": 1}
        assert daemon.active_context["type"] is None


class TestDaemonMQTTIntegration:
    """Tests the decoupled boundaries using pytest-mock async streams."""
    
    @pytest.mark.asyncio
    async def test_intent_shadowing_and_merging(self, daemon, mock_mqtt, message_stream):
        """Tests that multiple conflicting instructions merge into a single payload."""
        
        voice_payload = "set the desk light to 50 percent and actually make it red"
        
        mock_mqtt.messages = message_stream([
            ("jarvis/sensor/voice", voice_payload)
        ])
        
        await daemon.run()
        
        publish_calls = mock_mqtt.publish.call_args_list
        light_calls = [call for call in publish_calls if call[0][0] == "home/room/all/set"]
        
        assert len(light_calls) == 1, "Daemon failed to shadow/merge intents; published multiple times."
        
        sent_payload = json.loads(light_calls[0][0][1])
        assert sent_payload["action"] == "on"
        assert sent_payload["lum"] == 50
        assert sent_payload["color"] == "red"

    @pytest.mark.asyncio
    async def test_network_drop_recovery(self, daemon, mock_mqtt, mocker):
        """Tests that an aiomqtt.MqttError triggers a 5-second sleep and keeps the service alive."""
        import aiomqtt
        
        # 1. Simulate a network drop immediately when listening for messages
        async def failing_stream():
            raise aiomqtt.MqttError("Broker connection lost mid-stream")
            yield
            
        mock_mqtt.messages = failing_stream()
        
        # 2. Patch sleep so we don't actually wait 5 seconds, and use it to break the infinite loop
        mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
        mock_sleep.side_effect = asyncio.CancelledError() 
        
        # 3. Act & Assert
        with pytest.raises(asyncio.CancelledError):
            await daemon.run()
            
        mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_state_ttl_expiration(self, daemon, mock_mqtt, message_stream, mocker):
        """Tests that the unified state machine drops active context after the TTL expires."""
        future_time = time.time() + 25.0
        mocker.patch("time.time", return_value=future_time)
        
        # Simulate a context that was set 25 seconds ago (so it should be expired now)
        daemon.active_context = {"type": "spotify_choice", "expires_at": future_time - 5.0}
        
        mock_mqtt.messages = message_stream([
            ("jarvis/sensor/voice", "1")
        ])
        
        await daemon.run()
        
        assert daemon.active_context["type"] is None
        
        publish_calls = mock_mqtt.publish.call_args_list
        spotify_calls = [c for c in publish_calls if c[0][0] == "pc/spotify/control"]
        assert len(spotify_calls) == 0, "Daemon executed expired state logic."

    @pytest.mark.asyncio
    async def test_silent_mode_suppresses_tts_and_followup(self, daemon, mock_mqtt, message_stream):
        """Tests that enabling silent mode suppresses TTS speak requests and followups."""
        # The daemon publishes state changes to jarvis/sys/daemon_control and relies on
        # the broker looping its own publish back to it (it's subscribed to that topic
        # too); the mock client doesn't do that automatically, so it's simulated here.
        mock_mqtt.messages = message_stream([
            ("jarvis/sensor/voice", "enable silent mode"),
            ("jarvis/sys/daemon_control", json.dumps({"action": "silent_mode_on"})),
            ("jarvis/sensor/voice", "turn on living room light")
        ])
        
        await daemon.run()
        
        assert daemon.silent_mode is True
        
        publish_calls = mock_mqtt.publish.call_args_list
        speak_calls = [c for c in publish_calls if c[0][0] == "jarvis/sys/speak"]
        tts_req_calls = [c for c in publish_calls if c[0][0] == "jarvis/sys/tts_request"]
        
        # When silent mode is enabled, no TTS speech or followup request should be published
        assert len(tts_req_calls) == 0
        assert len(speak_calls) == 0

class TestDaemonEdgeCases:
    @pytest.mark.asyncio
    async def test_malformed_json_payload(self, daemon, mock_mqtt, message_stream):
        """Ensure the daemon gracefully handles broken JSON on MQTT topics."""
        mock_mqtt.messages = message_stream([
            ("jarvis/sys/speak", "{ broken json"),
            ("jarvis/sys/alarm/ring", "not a json"),
            ("jarvis/sys/mic_state", "{}"),
            ("jarvis/sensor/voice", "test command")
        ])
        
        # Should not raise exception
        await daemon.run()
        
        # If it didn't crash, the test passed. No need to assert specific publish logic 
        # since it correctly drops malformed JSON.
        assert True

    @pytest.mark.asyncio
    async def test_missing_payload_fields(self, daemon, mock_mqtt, message_stream):
        """Ensure missing expected keys in JSON don't crash processing."""
        import json
        mock_mqtt.messages = message_stream([
            ("jarvis/sys/alarm/ring", json.dumps({"wrong_key": "value"})),
            ("jarvis/sys/speak", json.dumps({"other_key": True})),
            ("jarvis/sensor/voice", "hello")
        ])
        
        # Should complete successfully
        await daemon.run()
        assert not daemon.pending_mic_request