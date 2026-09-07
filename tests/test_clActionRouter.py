import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clActionRouter import ActionRouter


class TestActionRouterTypeValidation:
    """A malformed slot value (e.g. a garbled fuzzy-match capture landing in a
    numeric field) must be rejected here rather than forwarded to a real
    actuator that expects the declared type."""

    def test_wrong_type_for_declared_schema_field_is_rejected(self):
        router = ActionRouter()
        topic, payload = router.prepare(
            "light.set",
            action="on",
            light_target="living room",
            lum="yes please lowering the brightness of the living room light",
        )
        assert topic is None
        assert payload is None

    def test_correct_type_still_passes(self):
        router = ActionRouter()
        topic, payload = router.prepare(
            "light.set",
            action="on",
            light_target="living room",
            lum=40,
        )
        assert topic == "home/room/all/set"
        assert payload["lum"] == 40

    def test_spotify_choice_index_survives_prepare(self):
        """A field passed to prepare() but not declared in the action's
        schema is silently dropped from the payload (prepare() only copies
        fields it finds in schema.items()) -- choice_index used to be
        missing from spotify.control's schema, so picking an option from a
        CONFIDENCE_LOW list always reached clSpotify.py as {"action":
        "play_choice"} with no index, which it rejects as unrecognized."""
        router = ActionRouter()
        topic, payload = router.prepare(
            "spotify.control",
            action="play_choice",
            choice_index=1,
        )
        assert topic == "pc/spotify/control"
        assert payload["choice_index"] == 1

    def test_mic_device_name_survives_prepare(self):
        router = ActionRouter()
        topic, payload = router.prepare(
            "mic.state",
            action="set_input_device",
            device_name="Microphone (Realtek Audio)",
        )
        assert topic == "jarvis/sys/mic_control"
        assert payload["device_name"] == "Microphone (Realtek Audio)"

    def test_tts_control_device_name_survives_prepare(self):
        router = ActionRouter()
        topic, payload = router.prepare(
            "tts.control",
            action="set_output_device",
            device_name="Speakers (Realtek Audio)",
        )
        assert topic == "jarvis/sys/tts_control"
        assert payload["device_name"] == "Speakers (Realtek Audio)"
