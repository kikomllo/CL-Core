"""The console service: one shell shared by every terminal window, driven by topics (src/clConsole.py)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import clConsole as C


class FakeSession:
    def __init__(self, on_output, on_exit, cols, rows):
        self.on_output, self.on_exit, self.cols, self.rows = on_output, on_exit, cols, rows
        self.alive, self.started, self.written, self.sizes, self.closed = False, False, [], [], False

    def start(self): self.alive = self.started = True
    def write(self, data): self.written.append(data)
    def resize(self, cols, rows): self.sizes.append((cols, rows))
    def replay(self): return "earlier output"
    def close(self): self.alive, self.closed = False, True


@pytest.fixture
def svc():
    sent, made = [], []

    def publish(topic, payload, retain=False):
        sent.append((topic, json.loads(payload), retain))

    def factory(on_output, on_exit, cols, rows):
        s = FakeSession(on_output, on_exit, cols, rows)
        made.append(s)
        return s

    service = C.ConsoleService(publish, factory)
    service.sent, service.made = sent, made
    return service


class TestConsoleService:
    def test_the_first_keystroke_starts_the_shell(self, svc):
        svc.handle(C.TOPIC_IN, "ls\r")
        assert len(svc.made) == 1 and svc.made[0].written == ["ls\r"]
        assert (C.TOPIC_STATE, {"alive": True}, True) in svc.sent

    def test_the_shell_is_shared_not_started_again(self, svc):
        svc.handle(C.TOPIC_IN, "a")
        svc.handle(C.TOPIC_IN, "b")
        svc.handle(C.TOPIC_ATTACH, "1")
        assert len(svc.made) == 1 and svc.made[0].written == ["a", "b"]

    def test_attaching_replays_the_recent_output_to_the_new_window(self, svc):
        svc.handle(C.TOPIC_ATTACH, "1")
        assert (C.TOPIC_OUT, {"d": "earlier output", "reset": True}, False) in svc.sent

    def test_output_is_published_as_it_comes(self, svc):
        svc.handle(C.TOPIC_ATTACH, "1")
        svc.made[0].on_output("héllo\r\n")
        assert (C.TOPIC_OUT, {"d": "héllo\r\n"}, False) in svc.sent

    def test_the_size_is_applied_to_the_running_shell_and_remembered_for_the_next(self, svc):
        svc.handle(C.TOPIC_RESIZE, json.dumps({"cols": 100, "rows": 40}))
        assert svc.made == []                                      # nothing starts for a size alone
        svc.handle(C.TOPIC_IN, "x")
        assert (svc.made[0].cols, svc.made[0].rows) == (100, 40)
        svc.handle(C.TOPIC_RESIZE, json.dumps({"cols": 90, "rows": 20}))
        assert svc.made[0].sizes == [(90, 20)]

    def test_a_shell_that_ended_is_reported_and_the_next_key_starts_a_new_one(self, svc):
        svc.handle(C.TOPIC_IN, "exit\r")
        svc.made[0].alive = False
        svc.made[0].on_exit(0)
        assert (C.TOPIC_OUT, {"exit": 0}, False) in svc.sent and (C.TOPIC_STATE, {"alive": False}, True) in svc.sent
        svc.handle(C.TOPIC_IN, "x")
        assert len(svc.made) == 2

    def test_close_ends_the_shell(self, svc):
        svc.handle(C.TOPIC_IN, "x")
        svc.handle(C.TOPIC_CLOSE, "1")
        assert svc.made[0].closed and (C.TOPIC_STATE, {"alive": False}, True) in svc.sent

    def test_bad_messages_never_stop_the_service(self, svc):
        svc.handle(C.TOPIC_RESIZE, "not json")
        svc.handle(C.TOPIC_RESIZE, json.dumps({"cols": "x"}))
        svc.handle(C.TOPIC_IN, "still works")
        assert svc.made[0].written == ["still works"]
