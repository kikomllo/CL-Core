"""The web dashboard's Live Logs endpoints: a stateless tail of logs/latest_run.log built on utils/clLogTail.py."""
import os
import sys

import pytest
import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from utils import clLogTail  # noqa: E402

NOISE = "[12:00:01] [DEBUG-MQTT] [CH: jarvis/sys/audio_vol] {\"rms\": 12}\n"      # a heartbeat topic the condensed view drops
REAL = "[12:00:02] [DAEMON] Fuzzy route matched: lights_on\n"


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "latest_run.log"
    path.write_text(NOISE + REAL, encoding="utf-8")
    monkeypatch.setattr(clLogTail, "LOG_PATH", str(path))
    return path


class TestReadLog:
    def test_the_first_read_is_the_condensed_tail(self, log):
        import clWebUI
        d = clWebUI.read_log(None, raw=False)
        assert "Fuzzy route matched" in d["text"] and "audio_vol" not in d["text"]
        assert d["pos"] == os.path.getsize(log) and d["restarted"] is False

    def test_the_raw_view_keeps_the_noise(self, log):
        import clWebUI
        assert "audio_vol" in clWebUI.read_log(None, raw=True)["text"]

    def test_a_later_read_from_the_offset_returns_only_new_lines(self, log):
        import clWebUI
        pos = clWebUI.read_log(None, False)["pos"]
        with open(log, "a", encoding="utf-8") as f:
            f.write("[12:00:03] [DAEMON] second\n")
        d = clWebUI.read_log(pos, False)
        assert "second" in d["text"] and "Fuzzy" not in d["text"] and d["pos"] > pos
        assert clWebUI.read_log(d["pos"], False)["text"] == ""                 # nothing new: an empty answer, same offset

    def test_a_line_still_being_written_waits_for_its_end(self, log):
        import clWebUI
        pos = clWebUI.read_log(None, False)["pos"]
        with open(log, "a", encoding="utf-8") as f:
            f.write("[12:00:03] [DAEMON] half a li")
        assert clWebUI.read_log(pos, False) == {"text": "", "pos": pos, "restarted": False}

    def test_a_rewritten_log_says_so_and_starts_over(self, log):
        import clWebUI
        pos = clWebUI.read_log(None, False)["pos"]
        log.write_text("[12:05:00] [BOOT] new run\n", encoding="utf-8")        # shorter than the offset: a new run
        d = clWebUI.read_log(pos, False)
        assert d["restarted"] is True and "new run" in d["text"]

    def test_bearer_tokens_are_redacted_in_both_views(self, log):
        import clWebUI
        with open(log, "a", encoding="utf-8") as f:
            f.write("[12:00:04] [SPOTIFY] auth header Bearer abc123.secret-token\n")
        for raw in (False, True):
            text = clWebUI.read_log(None, raw)["text"]
            assert "abc123" not in text and "Bearer" in text

    def test_a_missing_log_is_an_empty_answer(self, tmp_path, monkeypatch):
        import clWebUI
        monkeypatch.setattr(clLogTail, "LOG_PATH", str(tmp_path / "nope.log"))
        assert clWebUI.read_log(None, False) == {"text": "", "pos": None, "restarted": False}


@pytest.mark.asyncio
class TestEndpoints:
    @pytest_asyncio.fixture
    async def client(self, log):
        from aiohttp.test_utils import TestClient, TestServer
        import clWebUI
        async with TestClient(TestServer(clWebUI.build_app(None))) as c:
            c.origin = f"http://127.0.0.1:{clWebUI.PORT}"
            yield c

    async def test_get_returns_text_and_the_next_offset(self, client, log):
        d = await (await client.get("/api/log")).json()
        assert "Fuzzy route matched" in d["text"]
        again = await (await client.get(f"/api/log?pos={d['pos']}")).json()
        assert again["text"] == "" and again["pos"] == d["pos"]
        assert "audio_vol" in (await (await client.get("/api/log?raw=1")).json())["text"]

    async def test_a_junk_offset_is_just_the_tail(self, client):
        assert "Fuzzy" in (await (await client.get("/api/log?pos=abc")).json())["text"]

    async def test_the_terminal_opens_in_the_views_mode(self, client, monkeypatch):
        import clWebUI
        calls = []
        monkeypatch.setattr(clWebUI.clLogTail, "open_terminal", lambda raw=False: calls.append(raw) or True)
        r = await client.post("/api/log/terminal", json={"raw": True}, headers={"Origin": client.origin})
        assert (await r.json()) == {"ok": True} and calls == [True]
        assert (await (await client.post("/api/log/terminal", json={})).json())["ok"] is True and calls == [True, False]

    async def test_no_terminal_is_reported_not_raised(self, client, monkeypatch):
        import clWebUI
        monkeypatch.setattr(clWebUI.clLogTail, "open_terminal", lambda raw=False: False)
        assert (await (await client.post("/api/log/terminal", json={})).json()) == {"ok": False}

    async def test_a_foreign_page_or_a_form_cannot_spawn_a_terminal(self, client, monkeypatch):
        import clWebUI
        calls = []
        monkeypatch.setattr(clWebUI.clLogTail, "open_terminal", lambda raw=False: calls.append(raw) or True)
        assert (await client.post("/api/log/terminal", json={}, headers={"Origin": "http://evil.example"})).status == 403
        assert (await client.post("/api/log/terminal", data={"raw": "1"})).status == 403
        assert (await client.post("/api/log/terminal", data="not json", headers={"Content-Type": "application/json"})).status == 400
        assert calls == []


# ---- the terminal's pure logic (web/js/logfilter.js), under node ----
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

FILTER_JS = os.path.join(ROOT, "web", "js", "logfilter.js").replace("\\", "/")


def run_js(expr):
    script = f'const F = await import("file:///{FILTER_JS}");\nconsole.log(JSON.stringify({expr}));'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
class TestFilter:
    LINE = "[04:51:15] [SPOTIFY] Command Received: {'action': 'status'}"

    def test_words_must_all_match_ignoring_case(self):
        assert run_js(f'F.matchLine({json.dumps(self.LINE)}, F.parseFilter("spotify STATUS"))') is True
        assert run_js(f'F.matchLine({json.dumps(self.LINE)}, F.parseFilter("spotify daemon"))') is False

    def test_a_minus_word_hides_lines_that_have_it(self):
        assert run_js(f'F.matchLine({json.dumps(self.LINE)}, F.parseFilter("spotify -status"))') is False
        assert run_js(f'F.matchLine({json.dumps(self.LINE)}, F.parseFilter("spotify -mqtt"))') is True

    def test_an_empty_filter_shows_everything_and_a_lone_dash_is_just_text(self):
        assert run_js(f'F.matchLine({json.dumps(self.LINE)}, F.parseFilter("   "))') is True
        assert run_js('F.parseFilter("-")') == {"include": ["-"], "exclude": []}
        assert run_js('F.parseFilter("a -b  -c d")') == {"include": ["a", "d"], "exclude": ["b", "c"]}

    def test_every_occurrence_is_highlighted_across_words(self):
        assert run_js('F.highlight("Status of status", ["status"])') == [["Status", True], [" of ", False], ["status", True]]
        assert run_js('F.highlight("abc", [])') == [["abc", False]]
        assert run_js('F.highlight("abcabc", ["ab", "bc"])') == [["abcabc", True]]               # overlapping words merge into one run

    def test_a_line_splits_into_stamp_tag_and_rest(self):
        assert run_js(f'F.splitLine({json.dumps(self.LINE)})') == {
            "stamp": "[04:51:15]", "tag": "SPOTIFY", "rest": "Command Received: {'action': 'status'}", "kind": ""}
        assert run_js('F.splitLine("[12:00:01] [12:00:02] [DAEMON] hi")')["stamp"] == "[12:00:01] [12:00:02]"     # service + supervisor stamps
        assert run_js('F.splitLine("plain text")') == {"stamp": "", "tag": "", "rest": "plain text", "kind": ""}

    def test_errors_and_warnings_are_recognised(self):
        assert run_js('F.splitLine("[12:00:01] [TTS] Traceback (most recent call last):").kind') == "err"
        assert run_js('F.splitLine("[12:00:01] [TTS] WARNING: slow").kind') == "warn"

    def test_a_tag_always_gets_the_same_colour_slot(self):
        slots = run_js('["SPOTIFY", "DAEMON", "MQTT", "TTS", "UI", "SPOTIFY"].map(F.tagSlot)')
        assert slots[0] == slots[5] and all(0 <= s <= 5 for s in slots)
