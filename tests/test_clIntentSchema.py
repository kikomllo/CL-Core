import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from nlp.clIntentSchema import IntentSchema

GBNF = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'grammars', 'intent_schema.gbnf'))


@pytest.fixture(scope="module")
def schema():
    s = IntentSchema.from_gbnf(GBNF)
    assert s is not None
    return s


def ok(schema, obj):
    return schema.parse(json.dumps(obj)) is not None


class TestSchemaFromTheRealGrammar:
    def test_reads_the_ids_verbs_and_field_types(self, schema):
        assert {"light.set", "spotify.control", "reminder.create"} <= schema.action_ids
        assert {"on", "off", "pause", "play"} <= schema.action_values
        assert {"color", "light_target", "playlist_name", "time_str"} <= schema.string_fields
        assert schema.number_fields == {"lum", "volume"}

    def test_matches_every_action_id_the_grammar_lists(self, schema):
        text = open(GBNF, encoding="utf-8").read()
        for action_id in schema.action_ids:
            assert f'\\"{action_id}\\"' in text


class TestAcceptsWhatTheGrammarAllows:
    def test_a_simple_action(self, schema):
        assert ok(schema, {"actions": [{"action_id": "spotify.control", "action": "pause"}]})

    def test_no_actions_for_noise(self, schema):
        assert ok(schema, {"actions": []})

    def test_several_actions_with_mixed_fields(self, schema):
        assert ok(schema, {"actions": [
            {"action_id": "light.set", "action": "on", "light_target": "desk", "lum": 50},
            {"action_id": "light.set", "action": "on", "color": "red"},
        ]})

    def test_decimals_and_zero(self, schema):
        assert ok(schema, {"actions": [{"action_id": "light.set", "lum": 12.5}, {"action_id": "light.set", "lum": 0}]})

    def test_key_order_does_not_matter(self, schema):
        assert schema.parse('{"actions": [{"color": "red", "action": "on", "action_id": "light.set"}]}') is not None

    def test_returns_the_parsed_object(self, schema):
        assert schema.parse('{"actions": []}') == {"actions": []}


class TestRejectsAnythingElse:
    @pytest.mark.parametrize("text", [
        "", "not json", "{", '{"actions": [', "Sure! " + '{"actions": []}',
        '{"actions": []} trailing words',
    ])
    def test_text_that_is_not_exactly_json(self, schema, text):
        assert schema.parse(text) is None

    @pytest.mark.parametrize("obj", [
        [], "x", 3, None,
        {},                                                   # no actions key
        {"actions": {}},                                      # not a list
        {"actions": [], "reply": "Done sir"},                 # the grammar allows only "actions"
        {"actions": ["light.set"]},                           # entries must be objects
        {"actions": [{"action": "on"}]},                      # action_id is mandatory
        {"actions": [{"action_id": "made.up"}]},              # unknown intent
        {"actions": [{"action_id": "light.set", "action": "dance"}]},     # unknown verb
        {"actions": [{"action_id": "light.set", "brightness": 5}]},       # unknown field
        {"actions": [{"action_id": "light.set", "color": 5}]},            # string field given a number
        {"actions": [{"action_id": "light.set", "lum": "50"}]},           # number field given a string
        {"actions": [{"action_id": "light.set", "lum": -5}]},             # the grammar has no negatives
        {"actions": [{"action_id": "light.set", "lum": True}]},           # booleans are not numbers
        {"actions": [{"action_id": "light.set", "lum": None}]},
        {"actions": [{"action_id": "light.set", "color": None}]},
        {"actions": [{"action_id": "light.set", "action": None}]},
        {"actions": [{"action_id": "light.set"}, {"action_id": "nope"}]}, # one bad entry spoils it
    ])
    def test_structures_the_grammar_would_not_produce(self, schema, obj):
        assert not schema.valid(obj)


class TestLoading:
    def test_missing_file_gives_no_schema(self, tmp_path):
        assert IntentSchema.from_gbnf(str(tmp_path / "missing.gbnf")) is None

    def test_a_file_without_the_rules_gives_no_schema(self, tmp_path):
        f = tmp_path / "g.gbnf"
        f.write_text('root ::= "x"\n')
        assert IntentSchema.from_gbnf(str(f)) is None

    def test_follows_a_regenerated_grammar(self, tmp_path):
        f = tmp_path / "g.gbnf"
        f.write_text(
            'action-id ::=\n    "\\"new.thing\\"" |\n    "\\"light.set\\""\n\n'
            'action-field ::=\n    "\\"action\\"" ws ":" ws action-value |\n    "\\"mood\\"" ws ":" ws string |\n'
            '    "\\"level\\"" ws ":" ws number\n\n'
            'action-value ::=\n    "\\"go\\"" |\n    "\\"stop\\""\n\n# String\nstring ::= "x"\n')
        s = IntentSchema.from_gbnf(str(f))
        assert s.action_ids == {"new.thing", "light.set"} and s.action_values == {"go", "stop"}
        assert s.string_fields == {"mood"} and s.number_fields == {"level"}
        assert s.parse('{"actions": [{"action_id": "new.thing", "action": "go", "mood": "calm", "level": 3}]}')
        assert s.parse('{"actions": [{"action_id": "spotify.control"}]}') is None
