"""The Claude session as a real terminal: its raw output is teed to the dashboard (jarvis/claude/out), keystrokes go straight back in
(jarvis/claude/input), and a window that opens later is replayed. Windows backend (ConPTY); tmux has no raw stream."""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from clClaudeBridge import ClaudeBridgeService
from utils import clPtyBridge
from utils.clPtyBridge import ClaudePtyBridge


@pytest.fixture
def pty(tmp_path):
    return ClaudePtyBridge(cwd=str(tmp_path), on_screen_update=lambda s: None, cols=100, rows=30)


class TestRawTee:
    def test_the_backend_says_it_can_stream_and_tmux_does_not(self):
        from utils.clClaudeTmuxBridge import ClaudeTmuxBridge
        assert ClaudePtyBridge.RAW_STREAM is True and ClaudeTmuxBridge.RAW_STREAM is False

    def test_output_is_remembered_for_a_replay(self, pty):
        pty._remember_raw("one\n")
        pty._remember_raw("two\n")
        assert pty.raw_replay() == "one\ntwo\n"

    def test_the_replay_is_capped_and_starts_on_a_line(self, pty, monkeypatch):
        monkeypatch.setattr(clPtyBridge, "RAW_SCROLLBACK_CHARS", 20)
        pty._remember_raw("aaaa\nbbbb\ncccc\ndddd\neeee\nffff\n")
        assert len(pty.raw_replay()) <= 20 and pty.raw_replay().endswith("ffff\n")
        assert pty.raw_replay().startswith(("c", "d", "e", "f"))

    def test_a_backend_without_a_raw_stream_replays_nothing(self):
        from utils.clClaudeTmuxBridge import ClaudeTmuxBridge
        assert ClaudeTmuxBridge.raw_replay(object.__new__(ClaudeTmuxBridge)) == ""


class FakeBridge:
    RAW_STREAM = True
    COLS, ROWS = 120, 40

    def __init__(self):
        self.sent, self.replay = [], "earlier"

    def is_alive(self): return True
    def raw_replay(self): return self.replay
    def send_raw_keys(self, raw): self.sent.append(raw)


@pytest.fixture
def svc():
    s = ClaudeBridgeService()
    s.bridge = FakeBridge()
    return s


class TestServiceRawStream:
    def test_snapshots_say_the_session_can_be_drawn_as_a_terminal(self, svc):
        svc.mqtt_client = MagicMock(publish=AsyncMock())
        asyncio.run(svc._publish_screen("hello"))
        payload = json.loads(svc.mqtt_client.publish.call_args.args[1])
        assert payload["raw"] is True

    def test_a_backend_without_a_stream_does_not_say_so(self, svc):
        svc.bridge = MagicMock(spec=["is_alive"])
        svc.mqtt_client = MagicMock(publish=AsyncMock())
        asyncio.run(svc._publish_screen("hello"))
        assert "raw" not in json.loads(svc.mqtt_client.publish.call_args.args[1])

    def test_attach_replays_what_was_printed_at_the_size_it_was_printed(self, svc):
        client = MagicMock(publish=AsyncMock())
        asyncio.run(svc._attach(client))
        topic, body = client.publish.call_args.args[:2]
        assert topic == "jarvis/claude/out"
        assert json.loads(body) == {"d": "earlier", "reset": True, "cols": 120, "rows": 40}

    def test_output_is_published_in_order_a_burst_as_one_message(self, svc):
        async def go():
            svc.loop = asyncio.get_running_loop()
            svc._raw_queue = asyncio.Queue()
            client = MagicMock(publish=AsyncMock())
            for chunk in ("a", "b", "c"):
                svc._on_claude_raw(chunk)
            await asyncio.sleep(0)                       # the queued puts land
            task = asyncio.create_task(svc._raw_pump(client))
            await asyncio.sleep(0.05)
            task.cancel()
            return [json.loads(c.args[1]) for c in client.publish.call_args_list]
        assert asyncio.run(go()) == [{"d": "abc"}]
