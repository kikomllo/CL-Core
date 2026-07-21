import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from nlp.clIntentEngine import IntentEngine

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