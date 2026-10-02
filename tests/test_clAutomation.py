"""Tests for the automation engine (presence lights) and the sun helper it uses."""
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import clAutomation as a

class TestSettings:
    def write(self, tmp_path, block, followup=None):
        settings = {"automation_settings": block}
        if followup is not None:
            settings["followup_settings"] = followup
        path = tmp_path / "core.json"
        path.write_text(json.dumps({"settings": settings}))
        return str(path)

    def test_defaults_when_missing(self, tmp_path):
        s = a.load_settings(str(tmp_path / "nope.json"))
        assert s["presence_lights"]["enabled"] is False and s["presence_lights"]["lights"] == []

    def test_overrides_and_junk_are_handled(self, tmp_path):
        path = self.write(tmp_path, {
            "enabled": "yes",
            "presence_lights": {"enabled": True, "lights": ["bedroom", 3, ""], "leave_delay_s": -5}})
        s = a.load_settings(path)
        assert s["enabled"] is True
        assert s["presence_lights"] == {
            "enabled": True, "lights": ["bedroom"], "off_on_leave": True, "leave_delay_s": 0.0}

    def test_evening_hours_come_from_followup_settings_by_default(self, tmp_path):
        s = a.load_settings(self.write(tmp_path, {}, {"evening_start_hour": 20, "evening_end_hour": 6}))
        assert (s["evening_start_hour"], s["evening_end_hour"]) == (20, 6)


class TestWindow:
    def test_window_is_the_evening_hours_and_wraps_midnight(self):
        settings = dict(a.DEFAULT_SETTINGS)
        at = lambda h: datetime(2026, 3, 3, h).timestamp()
        assert a.in_dark_window(at(22), settings) and a.in_dark_window(at(3), settings)
        assert a.in_dark_window(at(19), settings) and not a.in_dark_window(at(7), settings)
        assert not a.in_dark_window(at(12), settings)

    def test_a_same_day_range_does_not_wrap(self):
        settings = dict(a.DEFAULT_SETTINGS, evening_start_hour=18, evening_end_hour=23)
        at = lambda h: datetime(2026, 3, 3, h).timestamp()
        assert a.in_dark_window(at(20), settings) and not a.in_dark_window(at(2), settings)


def engine(lights=("bedroom",), *, enabled=True, off_on_leave=True, dark=True, delay=20):
    settings = json.loads(json.dumps(a.DEFAULT_SETTINGS))
    settings["presence_lights"].update(enabled=enabled, lights=list(lights),
                                       off_on_leave=off_on_leave, leave_delay_s=delay)
    e = a.AutomationEngine(lambda: settings, clock=lambda: 1000.0)
    a.in_dark_window = (lambda now, s: dark)
    return e


@pytest.fixture(autouse=True)
def restore_window():
    original = a.in_dark_window
    yield
    a.in_dark_window = original


ON = ("light.set", {"action": "on", "light_target": "bedroom", "silent": True})
OFF = ("light.set", {"action": "off", "light_target": "bedroom", "silent": True})


class TestPresenceLights:
    def test_arriving_after_dark_turns_the_lights_on(self):
        e = engine()
        e.on_presence_event("arrived", 1000)
        assert e.drain() == [ON]

    def test_arriving_in_daylight_does_nothing(self):
        e = engine(dark=False)
        e.on_presence_event("arrived", 1000)
        assert e.drain() == []

    def test_each_chosen_light_gets_its_own_action(self):
        e = engine(("bedroom", "kitchen"))
        e.on_presence_event("arrived", 1000)
        assert [kw["light_target"] for _, kw in e.drain()] == ["bedroom", "kitchen"]

    def test_leaving_turns_off_only_after_the_delay(self):
        e = engine(delay=20)
        e.on_presence_event("arrived", 1000)
        e.drain()
        e.on_presence_event("left", 2000)
        e.tick(2010)
        assert e.drain() == []
        e.tick(2020)
        assert e.drain() == [OFF]
        e.tick(2100)
        assert e.drain() == []

    def test_coming_back_within_the_delay_cancels_the_off(self):
        e = engine(delay=20, dark=False)
        e.on_presence_event("arrived", 1000)
        e.on_presence_event("left", 2000)
        e.on_presence_event("arrived", 2010)
        e.tick(2100)
        assert e.drain() == []

    def test_leaving_in_daylight_still_turns_the_lights_off(self):
        e = engine(dark=False)
        e.on_presence_event("arrived", 1000)
        e.on_presence_event("left", 2000)
        e.tick(2020)
        assert e.drain() == [OFF]

    def test_off_on_leave_can_be_disabled(self):
        e = engine(off_on_leave=False)
        e.on_presence_event("left", 2000)
        e.tick(3000)
        assert e.drain() == []

    def test_disabled_or_empty_rules_do_nothing(self):
        for kwargs in ({"enabled": False}, {"lights": ()}):
            e = engine(**kwargs)
            e.on_presence_event("arrived", 1000)
            e.on_presence_event("left", 2000)
            e.tick(3000)
            assert e.drain() == []

    def test_master_switch_off_does_nothing(self):
        e = engine()
        e.load_settings = lambda: dict(a.DEFAULT_SETTINGS, enabled=False,
                                       presence_lights={"enabled": True, "lights": ["bedroom"],
                                                        "off_on_leave": True, "leave_delay_s": 0})
        e.on_presence_event("arrived", 1000)
        assert e.drain() == []

    def test_a_second_arrival_while_home_is_ignored(self):
        e = engine()
        e.on_presence_event("arrived", 1000)
        e.drain()
        e.on_presence_event("arrived", 1500)  # monitor restarted
        assert e.drain() == []

    def test_retained_state_at_startup_prevents_a_false_arrival(self):
        e = engine()
        e.on_presence_state({"present": True})
        e.on_presence_event("arrived", 1000)
        assert e.drain() == []

    def test_retained_state_is_used_only_once(self):
        e = engine()
        e.on_presence_state({"present": True})
        e.on_presence_event("left", 1000)
        e.on_presence_state({"present": True})
        assert e.home is False

    def test_unknown_events_are_ignored(self):
        e = engine()
        e.on_presence_event("wandered", 1000)
        assert e.drain() == [] and e.home is None


class TestService:
    def test_actions_are_routed_through_the_action_router(self):
        svc = a.AutomationService(engine())
        svc.handle_message(a.EVENT_TOPIC, {"event": "arrived", "ts": 1000})
        (action_id, kwargs), = svc.engine.drain()
        topic, payload = svc.router.prepare(action_id, **kwargs)
        assert topic == "home/room/all/set"
        assert payload["action"] == "on" and payload["light_target"] == "bedroom"

    def test_presence_topic_seeds_home_state(self):
        svc = a.AutomationService(engine())
        svc.handle_message(a.PRESENCE_TOPIC, {"present": True})
        assert svc.engine.home is True


class TestRetryOnFailure:
    def test_a_failed_command_is_retried_after_a_delay(self):
        e = engine()
        e.on_presence_event("arrived", 1000)
        e.drain()
        e.on_light_feedback("bedroom", "on", "error", 1000)
        e.tick(1004)
        assert e.drain() == []                  # too soon
        e.tick(1005)
        assert e.drain() == [ON]

    def test_a_late_success_clears_the_pending_retry(self):
        e = engine()
        e.on_presence_event("arrived", 1000)
        e.drain()
        e.on_light_feedback("bedroom", "on", "error", 1000)
        e.on_light_feedback("bedroom", "on", "success", 1002)
        e.tick(1010)
        assert e.drain() == []

    def test_it_gives_up_after_the_third_attempt(self):
        e = engine()
        e.on_presence_event("arrived", 1000)     # attempt 1
        e.drain()
        e.on_light_feedback("bedroom", "on", "error", 1000)
        e.tick(1005)                              # attempt 2
        assert e.drain() == [ON]
        e.on_light_feedback("bedroom", "on", "error", 1005)
        e.tick(1010)                              # attempt 3
        assert e.drain() == [ON]
        e.on_light_feedback("bedroom", "on", "error", 1010)
        e.tick(1015)                              # no more retries
        assert e.drain() == []

    def test_feedback_for_a_different_action_or_light_is_ignored(self):
        e = engine()
        e.on_presence_event("arrived", 1000)
        e.drain()
        e.on_light_feedback("bedroom", "off", "error", 1000)   # wrong action
        e.on_light_feedback("kitchen", "on", "error", 1000)    # wrong light
        e.tick(1010)
        assert e.drain() == []

    def test_a_new_command_for_the_same_light_resets_the_attempt_count(self):
        e = engine()
        e.on_presence_event("arrived", 1000)      # attempt 1 of "on"
        e.drain()
        e.on_light_feedback("bedroom", "on", "error", 1000)
        e.on_presence_event("left", 1001)         # a fresh "off" supersedes the pending "on"
        e.tick(1030)
        assert e.drain() == [OFF]                 # not a stale retry of "on"

    def test_the_service_routes_feedback_by_topic(self):
        svc = a.AutomationService(engine())
        svc.engine.on_presence_event("arrived", 1000)
        svc.engine.drain()
        svc.handle_message(a.FEEDBACK_TOPIC, {"device": "smart_lights", "status": "error",
                                              "action_cmd": "on", "light_target": "bedroom"})
        assert "bedroom" in svc.engine._pending

    def test_feedback_from_something_else_is_ignored(self):
        svc = a.AutomationService(engine())
        svc.engine.on_presence_event("arrived", 1000)
        svc.engine.drain()
        svc.handle_message(a.FEEDBACK_TOPIC, {"device": "spotify", "status": "error"})
        assert "bedroom" in svc.engine._pending   # untouched
