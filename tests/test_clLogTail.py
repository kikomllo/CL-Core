import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils import clLogTail
from utils.clLogTail import condense, condense_text, LogTailer


def _mqtt(topic, payload="{}"):
    return f"[10:00:00] [DEBUG-MQTT] [CH: {topic}] {payload}"


class TestNoiseIsDropped:
    def test_heartbeat_topics_are_hidden(self):
        for topic in ("jarvis/sys/audio_vol", "jarvis/sys/spotify_local_status", "jarvis/sys/media_status", "jarvis/sys/tts_ref",
                      "jarvis/sys/spotify_lyrics", "jarvis/sys/spotify_queue", "jarvis/sys/todo/request", "jarvis/sys/todo/status"):
            assert condense(_mqtt(topic)) is None, topic

    def test_other_topics_are_kept_in_a_short_form(self):
        assert condense(_mqtt("jarvis/sys/tts_state", '{"state": "active"}')) == '[10:00:00] [MQTT] jarvis/sys/tts_state {"state": "active"}'

    def test_mqtt_client_chatter_is_hidden(self):
        for line in ("[10:00:00] [10:00:00] [UI] Sending CONNECT (u0, p0, wr0, wq0, wf0, c1, k60) client_id=b''",
                     "[10:00:00] [10:00:00] [UI] Received CONNACK (0, 0)",
                     "[10:00:00] [10:00:00] [UI] Sending PUBLISH (d0, q0, r0, m1), 'b'x'', ... (18 bytes)",
                     "[10:00:00] [10:00:00] [UI] Sending DISCONNECT"):
            assert condense(line) is None, line

    def test_other_protocol_and_debug_dumps_are_hidden(self):
        for line in ("[10:00:00] [10:00:00] [TERMINAL] AUMID lookup: OpenProcess failed for pid 3888 (error 0)",
                     "[10:00:00] [10:00:00] [UI] [DEBUG LAYOUT] Window Size: 1080x1920",
                     "[10:00:00] [10:00:00] [UI] [DEBUG KEY] JarvisUI keyPressEvent",
                     '[10:00:00] [10:00:00] [SPOTIFY] https://api.spotify.com:443 "GET /v1/me/player HTTP/1.1" 200 None',
                     "[10:00:00] [10:00:00] [CONTROL] Performing handshake1...",
                     "[10:00:00] [10:00:00] [CONTROL] Handshake2 OK"):
            assert condense(line) is None, line

    def test_blank_lines_are_hidden(self):
        assert condense("") is None and condense("   \r") is None

    def test_a_real_event_is_kept(self):
        assert condense("[10:00:00] [10:00:00] [SPOTIFY] Command Received: {'action': 'toggle'}") == "[10:00:00] [SPOTIFY] Command Received: {'action': 'toggle'}"
        assert condense("[10:00:00] [10:00:00] [CONTROL] Device responded with: TapoResponse { error_code: 1003 }") is not None


class TestLinesAreShort:
    def test_the_duplicated_timestamp_is_collapsed(self):
        assert condense("[16:24:44] [16:24:44] [CONTROL] hello") == "[16:24:44] [CONTROL] hello"

    def test_a_long_line_is_cut(self):
        out = condense("[10:00:00] [CONTROL] " + "x" * 5000)

        assert len(out) < clLogTail.LINE_CHARS + 30 and out.endswith("chars]")

    def test_an_mqtt_payload_is_cut_to_its_own_limit(self):
        out = condense(_mqtt("jarvis/sys/tts", '{"text": "' + "a" * 10000 + '"}'))

        assert len(out) < clLogTail.LINE_CHARS + 30 and "[+" in out

    def test_a_spotify_api_response_shrinks_to_its_size(self):
        out = condense("[10:00:00] [10:00:00] [SPOTIFY] RESULTS: {'currently_playing': " + "z" * 40000 + "}")

        assert len(out) < 140 and "[+" in out

    def test_a_spotify_call_loses_its_headers_and_token(self):
        line = ("[10:00:00] [10:00:00] [SPOTIFY] Sending PUT to https://api.spotify.com/v1/me/player/volume?volume_percent=80&device_id=44af8b "
                "with Params: {} Headers: {'Authorization': 'Bearer BQBCj-w4zOHVtZm'}")

        assert condense(line) == "[10:00:00] [SPOTIFY] PUT /v1/me/player/volume?volume_percent=80"

    def test_a_spotify_call_without_a_query(self):
        line = "[10:00:00] [10:00:00] [SPOTIFY] Sending GET to https://api.spotify.com/v1/me/player with Params: {'market': None} Headers: {}"

        assert condense(line) == "[10:00:00] [SPOTIFY] GET /v1/me/player"


class TestRawMode:
    def test_it_keeps_what_condensed_hides(self):
        line = _mqtt("jarvis/sys/audio_vol", '{"rms": 0}')

        assert condense(line, condensed=False) == line

    def test_it_still_cuts_absurd_lines_and_strips_colour_codes(self):
        out = condense("\x1b[36m[10:00:00] " + "x" * 40000 + "\x1b[0m", condensed=False)

        assert "\x1b" not in out and len(out) < clLogTail.RAW_LINE_CHARS + 30


class TestSecretsAreRedacted:
    def test_a_bearer_token_never_shows_in_either_mode(self):
        for condensed in (True, False):
            out = condense("[10:00:00] [CONTROL] sent Authorization: Bearer BQBCj-w4zOHVtZm8rql8ZOGW.abc_def", condensed)

            assert "BQBCj" not in out and "Bearer ***" in out


class TestCondenseText:
    def test_it_keeps_newline_terminated_lines_and_drops_the_hidden_ones(self):
        text = _mqtt("jarvis/sys/audio_vol") + "\n[10:00:01] [CONTROL] on\n\n[10:00:02] [CONTROL] off\n"

        assert condense_text(text) == "[10:00:01] [CONTROL] on\n[10:00:02] [CONTROL] off\n"

    def test_everything_hidden_gives_nothing(self):
        assert condense_text(_mqtt("jarvis/sys/audio_vol") + "\n") == ""


def _write(path, text):
    path.write_bytes(text.encode("utf-8"))       # write_text would turn LF into CRLF on Windows


class TestTailer:
    def test_the_first_read_is_the_tail_starting_on_a_whole_line(self, tmp_path):
        log = tmp_path / "run.log"
        _write(log, "".join(f"line {i:05d} END\n" for i in range(5000)))

        text, note, restarted = LogTailer(str(log), initial_bytes=1000).read()

        assert text.startswith("line ") and text.endswith("END\n") and "line 04999" in text and "line 00000" not in text
        assert not restarted

    def test_only_new_whole_lines_come_back(self, tmp_path):
        log = tmp_path / "run.log"
        _write(log, "one\n")
        t = LogTailer(str(log))
        t.read()
        with open(log, "a", encoding="utf-8", newline="\n") as f:
            f.write("two\nthr")

        assert t.read()[0] == "two\n"
        with open(log, "a", encoding="utf-8", newline="\n") as f:
            f.write("ee\n")
        assert t.read()[0] == "three\n"
        assert t.read()[0] == ""

    def test_a_burst_over_the_read_limit_reports_the_skip(self, tmp_path):
        log = tmp_path / "run.log"
        _write(log, "one\n")
        t = LogTailer(str(log), max_read=2000)
        t.read()
        with open(log, "a", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"burst {i:04d}\n" for i in range(1000)))

        text, note, _ = t.read()

        assert "KB of log skipped" in note and "burst 0999" in text and "burst 0000" not in text

    def test_a_rewritten_log_reports_a_restart(self, tmp_path):
        log = tmp_path / "run.log"
        _write(log, "old\n" * 100)
        t = LogTailer(str(log))
        t.read()
        _write(log, "new\n")

        text, _, restarted = t.read()

        assert restarted and text == "new\n"

    def test_a_missing_log_is_fine(self, tmp_path):
        assert LogTailer(str(tmp_path / "nope.log")).read() == ("", "", False)


class TestTerminalLauncher:
    def test_the_command_runs_this_module_and_carries_the_raw_flag(self):
        cmd = clLogTail.terminal_command(raw=True)

        assert cmd[1].endswith("clLogTail.py") and cmd[-1] == "--raw"
        assert clLogTail.terminal_command()[-1] != "--raw"

    def test_pythonw_is_swapped_for_a_console_python(self, tmp_path, monkeypatch):
        (tmp_path / "python.exe").write_text("")
        monkeypatch.setattr(clLogTail.sys, "executable", str(tmp_path / "pythonw.exe"))

        assert os.path.basename(clLogTail.terminal_command()[0]) == "python.exe"

    def test_windows_opens_a_new_console(self, monkeypatch):
        calls = []
        monkeypatch.setattr(clLogTail.sys, "platform", "win32")
        monkeypatch.setattr(clLogTail.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
        monkeypatch.setattr(clLogTail.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

        assert clLogTail.open_terminal()
        assert calls[0][1]["creationflags"] == 16

    def test_linux_uses_the_first_terminal_it_finds(self, monkeypatch):
        calls = []
        monkeypatch.setattr(clLogTail.sys, "platform", "linux")
        monkeypatch.setattr(clLogTail.shutil, "which", lambda name: "/usr/bin/" + name if name == "konsole" else None)
        monkeypatch.setattr(clLogTail.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))

        assert clLogTail.open_terminal()
        assert calls[0][:2] == ["konsole", "-e"]

    def test_no_terminal_installed_returns_false(self, monkeypatch):
        monkeypatch.setattr(clLogTail.sys, "platform", "linux")
        monkeypatch.setattr(clLogTail.shutil, "which", lambda name: None)

        assert clLogTail.open_terminal() is False
