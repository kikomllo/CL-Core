"""The web server's MQTT bridge: what it remembers for a page that opens later."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import clWebUI


def message(topic, text):
    m = MagicMock()
    m.topic = topic
    m.payload = text.encode("utf-8")
    return m


def bridge():
    return clWebUI.MqttBridge(MagicMock())


class TestReplay:
    def test_state_topics_are_replayed_to_a_page_that_opens_later(self):
        b = bridge()
        b._on_message(None, None, message("jarvis/sys/volume", "40"))
        assert b.last["jarvis/sys/volume"] == "40"

    def test_a_one_shot_command_is_never_replayed(self):
        # a stale set_overlay handed to a freshly loaded page would flip the desktop shell's mode
        b = bridge()
        b._on_message(None, None, message("jarvis/sys/ui_control", '{"action": "set_overlay"}'))
        assert "jarvis/sys/ui_control" not in b.last

    def test_the_command_is_still_broadcast_to_pages_that_are_open(self):
        b = bridge()
        b._on_message(None, None, message("jarvis/sys/ui_control", '{"action": "set_fullscreen"}'))
        b.loop.call_soon_threadsafe.assert_called_once()


class TestAccessLog:
    """Every request used to print an access-log line into the log file the Live Logs widget polls: each poll created the line the next poll showed."""

    def test_it_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("JARVIS_WEB_ACCESS_LOG", raising=False)
        assert clWebUI.access_log() is None

    def test_it_can_be_turned_on_for_debugging(self, monkeypatch):
        monkeypatch.setenv("JARVIS_WEB_ACCESS_LOG", "1")
        assert clWebUI.access_log() is not None

    def test_the_server_uses_it(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "src", "clWebUI.py"), encoding="utf-8").read()
        assert "web.AppRunner(app, access_log=access_log())" in src
