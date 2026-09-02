import json
import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from nlp.clIntentEngine import IntentEngine

@pytest.fixture(scope="module")
def real_engine():
    """IntentEngine built from the real config/intents.json -- needed for
    tests that check behavior across the actual priority-word vocabulary,
    not the minimal mock intent used by the other fixture below."""
    intents_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'intents.json')
    with open(intents_path, 'r', encoding='utf-8') as f:
        intents = json.load(f)
    return IntentEngine(intents, {}, ["abort"], {}, ["no thanks"])

@pytest.fixture(scope="module")
def engine():
    mock_intents = {
        "light_control": {
            "target_topic": "home/room/desk_light/set",
            "action_override": "on",
            "priority_words": ["light", "desk"],
            "templates": [
                "turn on the {light}",
                "set the light to {color}"
            ]
        }
    }
    word_map = {"one": "1", "two": "2", "three": "3"}
    abort_words = ["abort", "cancel", "stop"]
    return IntentEngine(mock_intents, word_map, abort_words)

class TestNLPEngineLogic:
    """Isolated NLP normalization and logic checks."""

    @pytest.mark.parametrize("input_text, expected", [
        ("Turn ON the lights, please!", "turn on the lights please"),
        ("Select option two", "select option 2"),
        ("What about option three?", "what about option 3"),
        ("Play some AC/DC...", "play some acdc")
    ])
    def test_text_normalization(self, engine, input_text, expected):
        assert engine.normalize_text(input_text) == expected

    @pytest.mark.parametrize("input_text, expected", [
        ("wait cancel that command", True),
        ("abort the mission", True),
        ("please stop doing that", True),
        ("turn on the light", False),
        ("play spotify", False)
    ])
    def test_abort_command(self, engine, input_text, expected):
        assert engine.is_abort_command(input_text) == expected

    @pytest.mark.parametrize("chunk, expected_color", [
        ("set the light to crimson red", "crimson red"),
        ("set the light to ocean blue please", "ocean blue"), # 'please' should be stripped
        ("set the light to light green", "green") # boundary stripping
    ])
    def test_variable_extraction(self, engine, chunk, expected_color):
        intent_match = {
            "action_override": "on",
            "original_template": "set the light to {color}"
        }
        result = engine.extract_variables(chunk, intent_match)
        assert result["color"] == expected_color

    def test_numeric_field_without_a_digit_is_left_unset(self, engine):
        """A numeric slot (lum/volume/choice_index/index) whose captured span has
        no digit must not fall back to storing the raw captured text -- that text
        would later be forwarded to a real actuator expecting an integer."""
        intent_match = {
            "action_override": "on",
            "original_template": "set brightness to {lum}"
        }
        chunk = "set brightness to yes please lowering the brightness of the living room light"
        result = engine.extract_variables(chunk, intent_match)
        assert "lum" not in result

    def test_numeric_field_with_a_digit_still_extracts(self, engine):
        intent_match = {
            "action_override": "on",
            "original_template": "set brightness to {lum}"
        }
        result = engine.extract_variables("set brightness to 40 percent", intent_match)
        assert result["lum"] == 40


class TestSmartPathVocabularyGate:
    """has_recognizable_content() gates whether a garbled/noise transcription
    ever reaches the Smart-Path SLM -- without it, dialogue history in the
    prompt makes the model tend to just repeat the last real action instead
    of recognizing there's nothing to do."""

    @pytest.mark.parametrize("text", [
        "nothing things",
        "ajervus in a bold living room life",
        "",
        "um yeah so",
    ])
    def test_noise_has_no_recognizable_content(self, real_engine, text):
        assert real_engine.has_recognizable_content(text) is False

    @pytest.mark.parametrize("text", [
        "could you turn on the living room why please",
        "yes please lowering the brightness of the living room light",
        "turn on the lights and play some jazz",
        "lower the brightness",
    ])
    def test_real_commands_have_recognizable_content(self, real_engine, text):
        assert real_engine.has_recognizable_content(text) is True