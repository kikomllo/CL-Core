import pytest
import os
import sys
import json
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clDaemon import CentralDaemon

@pytest.fixture
def daemon():
    d = CentralDaemon()
    # Several settings-toggle code paths (silent_mode, enable_followup, ecosystem_state)
    # persist to the real config/core.json via this call -- stub it out so exercising
    # those paths in a test can't leave the real config mutated on disk.
    d.loader.update_json_atomic = MagicMock()
    return d

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

        # Relative brightness (must not collide with the absolute {lum} templates above)
        ("lower the brightness", "brightness_down", None, None),
        ("increase the brightness", "brightness_up", None, None),
        ("make it dimmer", "brightness_down", None, None),
        ("make it brighter", "brightness_up", None, None),

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

    @pytest.mark.asyncio
    async def test_noise_after_a_real_command_does_not_repeat_it(self, daemon):
        """A garbled/noise follow-up transcription must not get handed to
        Smart-Path with dialogue history in its prompt -- that combination
        tends to just repeat the prior action instead of recognizing there's
        nothing to do (see: a real 'lower the brightness' followed by a
        misheard 'nothing things' re-triggering the same brightness_down)."""
        first_intents, _ = await daemon.route_voice_command("lower the brightness")
        assert len(first_intents) == 1

        noise_intents, _ = await daemon.route_voice_command("nothing things")
        assert noise_intents == []

    @pytest.mark.asyncio
    async def test_smart_path_timeout_falls_back_instead_of_hanging(self, daemon, mocker):
        """An unbounded await on the Smart-Path call would strand the whole
        voice command with no response at all (e.g. if a model is still
        loading/downloading) -- it must time out and fall back instead."""
        mocker.patch.object(daemon.slm, "parse_intent_async", new=AsyncMock(side_effect=asyncio.TimeoutError()))

        intents, _ = await daemon.route_voice_command("turn on the kitchen light and play some jazz")

        assert isinstance(intents, list)

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
    async def test_reminder_creation_does_not_trigger_optimistic_reply(self, daemon, mock_mqtt, message_stream, mocker):
        """clUtilities.py speaks its own 'Scheduling...' + final result for
        alarm/reminder/calendar creation asynchronously (real time parsing
        happens in a background thread) -- the daemon's optimistic reply-SLM
        path must not also fire and talk over it."""
        mock_speak = mocker.patch.object(daemon, "_speak_natural_reply", new=AsyncMock())
        mock_mqtt.messages = message_stream([
            ("jarvis/sensor/voice", "remind me to call mom in 30 minutes")
        ])

        await daemon.run()

        publish_calls = mock_mqtt.publish.call_args_list
        reminder_calls = [c for c in publish_calls if c[0][0] == "jarvis/sys/reminder/create"]
        assert len(reminder_calls) == 1
        mock_speak.assert_not_called()

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

class TestDynamicFollowups:
    """_roll_followup replaces the old always-on 'anything else' with a
    weighted-candidate pool (generic + state-driven suggestions), a
    fire-probability roll, and per-candidate cooldowns."""

    @staticmethod
    def _capture_choices_pool(mocker):
        """Wraps random.choices to record the candidate pool it's called
        with, while still behaving like the real thing."""
        import random as random_module
        real_choices = random_module.choices
        captured = {}

        def wrapper(population, weights=None, k=1):
            captured["population"] = list(population)
            return real_choices(population, weights=weights, k=k)

        mocker.patch("random.choices", side_effect=wrapper)
        return captured

    def test_master_switch_off_disables_everything(self, daemon):
        daemon.followups_enabled = False
        should, text = daemon._roll_followup("light.set", "off")
        assert (should, text) == (False, None)

    def test_discovery_action_suppresses_the_whole_mechanism(self, daemon):
        should, text = daemon._roll_followup("system.discovery", "discover")
        assert (should, text) == (False, None)

    def test_generic_fires_when_rolls_succeed(self, daemon, mocker):
        mocker.patch("random.choices", return_value=["generic"])
        mocker.patch("random.random", return_value=0.0)
        should, text = daemon._roll_followup("light.set", "off")
        assert should is True
        assert text in daemon.responses_data["followup_suggestions"]["generic"]

    def test_fire_roll_failure_yields_no_followup(self, daemon, mocker):
        mocker.patch("random.choices", return_value=["generic"])
        mocker.patch("random.random", return_value=0.99)  # above every configured fire_probability
        should, text = daemon._roll_followup("light.set", "off")
        assert (should, text) == (False, None)

    def test_missing_phrase_pool_fails_gracefully_without_crashing(self, daemon, mocker):
        """Phrase text lives in responses.json now (not hardcoded) -- a
        misconfigured/missing pool must not crash the roll, just skip it."""
        daemon.responses_data["followup_suggestions"] = {}
        mocker.patch("random.choices", return_value=["generic"])
        mocker.patch("random.random", return_value=0.0)
        should, text = daemon._roll_followup("light.set", "off")
        assert (should, text) == (False, None)

    def test_lights_off_evening_ineligible_during_the_day(self, daemon, mocker):
        daemon.any_light_on = False
        daemon.followup_evening_start_hour = 19
        daemon.followup_evening_end_hour = 7
        mocker.patch("clDaemon.datetime.datetime").now.return_value.hour = 12  # midday
        captured = self._capture_choices_pool(mocker)

        daemon._roll_followup("light.set", "off")

        assert "lights_off_evening" not in captured["population"]

    def test_lights_off_evening_ineligible_right_after_turning_a_light_on(self, daemon, mocker):
        """Guards against the jarvis/sys/light_status confirmation lagging the
        dispatch -- any_light_on might still read False from before this
        exact command turned a light on."""
        daemon.any_light_on = False
        daemon.followup_evening_start_hour = 0
        daemon.followup_evening_end_hour = 24  # always "evening" for this test
        captured = self._capture_choices_pool(mocker)

        daemon._roll_followup("light.set", "on")

        assert "lights_off_evening" not in captured["population"]

    def test_lights_off_evening_ineligible_right_after_turning_a_light_off(self, daemon, mocker):
        """Turning a light off was the user's explicit choice -- immediately
        asking 'want it back on?' contradicts what they just asked for, so
        this candidate must not fire for the action that just did that."""
        daemon.any_light_on = False
        daemon.followup_evening_start_hour = 0
        daemon.followup_evening_end_hour = 24  # always "evening" for this test
        captured = self._capture_choices_pool(mocker)

        daemon._roll_followup("light.set", "off")

        assert "lights_off_evening" not in captured["population"]

    def test_music_paused_ineligible_right_after_starting_playback(self, daemon, mocker):
        daemon.is_spotify_playing = False
        captured = self._capture_choices_pool(mocker)

        daemon._roll_followup("spotify.control", "play")

        assert "music_paused" not in captured["population"]

    def test_asked_candidate_is_ineligible_again_until_short_cooldown_elapses(self, daemon, mocker):
        daemon.is_spotify_playing = True  # excludes music_paused so the pool is just ["generic"]
        mocker.patch("random.choices", return_value=["generic"])
        mocker.patch("random.random", return_value=0.0)
        should, _ = daemon._roll_followup("light.set", "off")
        assert should is True

        # Immediately after: with generic in cooldown and nothing else eligible
        # (any_light_on defaults True, spotify is playing), the pool is empty.
        should_again, text_again = daemon._roll_followup("light.set", "off")
        assert (should_again, text_again) == (False, None)

    @pytest.mark.asyncio
    async def test_explicit_decline_escalates_to_the_longer_cooldown(self, daemon):
        daemon.pending_suggestion = "generic"
        daemon.suggestion_cooldowns["generic"] = time.time() + 10  # short cooldown already set

        await daemon.route_voice_command("no thanks")

        assert daemon.pending_suggestion is None
        remaining = daemon.suggestion_cooldowns["generic"] - time.time()
        # Declined cooldown (60 min default) should now dominate the short one.
        assert remaining > 55 * 60

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