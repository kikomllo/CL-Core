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
