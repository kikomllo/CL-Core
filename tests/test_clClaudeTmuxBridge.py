"""Tests for the Linux tmux backend. All tmux/subprocess calls are mocked --
no real tmux server or claude CLI needed. Shared logic (auto-answers, stall
detection, prompt/busy detection) is covered in test_clClaudeSession.py."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clClaudeTmuxBridge import ClaudeTmuxBridge


def _bridge(cwd="."):
    return ClaudeTmuxBridge(cwd=cwd, on_screen_update=MagicMock())


class TestBinaryAndTmuxPreflight:
    def test_missing_claude_cli_raises_clear_error(self, mocker):
        mocker.patch("shutil.which", return_value=None)
        import pytest
        with pytest.raises(FileNotFoundError, match="claude CLI not found"):
            _bridge().start()

    def test_missing_tmux_raises_clear_error(self, mocker):
        mocker.patch("shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None)
        import pytest
        with pytest.raises(FileNotFoundError, match="tmux not found"):
            _bridge().start()


class TestSessionSpawnArgs:
    """`claude --continue` must only be requested once a prior conversation
    exists, and CLAUDE_CODE_* env markers must be stripped from the pane's
    process env so the spawned session doesn't see itself as a nested child."""

    def _spawn(self, mocker, tmp_path, monkeypatch=None, existing_session=False):
        mocker.patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}")
        run = mocker.patch("subprocess.run")
        has_session_result = MagicMock(returncode=0 if existing_session else 1)
        new_session_result = MagicMock(returncode=0)
        run.side_effect = [has_session_result] if existing_session else [has_session_result, new_session_result]
        mocker.patch.object(ClaudeTmuxBridge, "_start_pipe_reader")
        bridge = ClaudeTmuxBridge(cwd=str(tmp_path), on_screen_update=MagicMock())
        bridge.start()
        return run

    def test_continue_omitted_with_no_prior_session(self, mocker, tmp_path):
        run = self._spawn(mocker, tmp_path)
        new_session_args = run.call_args_list[1][0][0]
        assert "--continue" not in new_session_args

    def test_continue_included_once_a_session_exists(self, mocker, tmp_path):
        (tmp_path / "data" / "claude_bridge_config" / "projects").mkdir(parents=True)
        run = self._spawn(mocker, tmp_path)
        new_session_args = run.call_args_list[1][0][0]
        assert "--continue" in new_session_args

    def test_claude_code_env_vars_are_stripped_via_env_dash_u(self, mocker, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
        run = self._spawn(mocker, tmp_path)
        new_session_args = run.call_args_list[1][0][0]
        assert new_session_args[new_session_args.index("-u") + 1] == "CLAUDE_CODE_CHILD_SESSION"

    def test_reuses_existing_session_instead_of_spawning(self, mocker, tmp_path):
        run = self._spawn(mocker, tmp_path, existing_session=True)
        assert run.call_count == 1  # only has-session, no new-session

    def test_new_session_passes_config_dir_and_dimensions(self, mocker, tmp_path):
        run = self._spawn(mocker, tmp_path)
        new_session_args = run.call_args_list[1][0][0]
        assert "-x" in new_session_args and "120" in new_session_args
        assert "-y" in new_session_args and "40" in new_session_args
        assert any(a.startswith("CLAUDE_CONFIG_DIR=") for a in new_session_args)


class TestWriteAndSendKeys:
    def test_write_sends_literal_text_then_enter(self, mocker):
        bridge = _bridge()
        mocker.patch.object(ClaudeTmuxBridge, "is_alive", return_value=True)
        run = mocker.patch("subprocess.run")

        bridge.write("hello world")

        first_call = run.call_args_list[0][0][0]
        second_call = run.call_args_list[1][0][0]
        assert first_call == ["tmux", "send-keys", "-t", bridge.SESSION_NAME, "-l", "hello world"]
        assert second_call == ["tmux", "send-keys", "-t", bridge.SESSION_NAME, "Enter"]

    def test_write_raises_if_session_not_alive(self, mocker):
        bridge = _bridge()
        mocker.patch.object(ClaudeTmuxBridge, "is_alive", return_value=False)
        import pytest
        with pytest.raises(RuntimeError, match="not alive"):
            bridge.write("hello")

    def test_send_keys_maps_to_tmux_key_names_not_escape_bytes(self, mocker):
        bridge = _bridge()
        mocker.patch.object(ClaudeTmuxBridge, "is_alive", return_value=True)
        run = mocker.patch("subprocess.run")

        bridge.send_keys("Down Enter")

        run.assert_called_once_with(["tmux", "send-keys", "-t", bridge.SESSION_NAME, "Down", "Enter"], check=True)

    def test_control_keys_token_is_title_cased_into_tmux_key_names(self, mocker):
        """Base class stores AUTO_ANSWERS tokens like 'DOWN ENTER'; tmux key
        names are 'Down'/'Enter', so a simple .title() bridges the two."""
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge._send_control_keys("DOWN ENTER")

        send_keys.assert_called_once_with("Down Enter")


class TestAliveAndStop:
    def test_is_alive_reflects_has_session(self, mocker):
        bridge = _bridge()
        run = mocker.patch("subprocess.run", return_value=MagicMock(returncode=0))
        assert bridge.is_alive() is True
        run.assert_called_once_with(["tmux", "has-session", "-t", bridge.SESSION_NAME], capture_output=True)

    def test_is_alive_false_when_session_missing(self, mocker):
        bridge = _bridge()
        mocker.patch("subprocess.run", return_value=MagicMock(returncode=1))
        assert bridge.is_alive() is False

    def test_stop_kills_the_tmux_session(self, mocker):
        bridge = _bridge()
        run = mocker.patch("subprocess.run")
        bridge.stop()
        run.assert_called_once_with(["tmux", "kill-session", "-t", bridge.SESSION_NAME], capture_output=True)
        assert bridge._running is False


class TestReadLoopFifoRace:
    """stop() deletes the FIFO file while killing the session; the reader
    thread's EOF-reopen can race that exact moment. Found via a live smoke
    test against a real tmux server: an unhandled FileNotFoundError used to
    crash the reader thread with an ugly traceback instead of exiting quietly."""

    def _bridge_ready_to_loop(self, mocker):
        bridge = _bridge()
        bridge._fifo_path = "/fake/fifo"
        bridge._running = True
        mocker.patch("os.open", return_value=99)
        mocker.patch("os.close")
        mocker.patch.object(ClaudeTmuxBridge, "is_alive", return_value=True)
        return bridge

    def test_missing_fifo_on_reopen_exits_the_loop_instead_of_raising(self, mocker):
        bridge = self._bridge_ready_to_loop(mocker)
        mocker.patch("select.select", return_value=([99], [], []))
        mocker.patch("os.read", return_value=b"")
        # First os.open (in _read_loop's setup) succeeds via the fixture's
        # return_value=99; the reopen-after-EOF attempt must raise instead.
        mocker.patch("os.open", side_effect=[99, FileNotFoundError()])

        bridge._read_loop()  # should not raise

        assert bridge._running is False

    def test_stop_flipping_running_during_eof_skips_the_reopen_attempt(self, mocker):
        bridge = self._bridge_ready_to_loop(mocker)
        mocker.patch("select.select", return_value=([99], [], []))

        def fake_read(fd, n):
            bridge._running = False  # simulates stop() racing in right at EOF
            return b""
        mocker.patch("os.read", side_effect=fake_read)
        reopen = mocker.patch("os.open", side_effect=[99, RuntimeError("must not be called")])

        bridge._read_loop()  # should not raise

        assert reopen.call_count == 1  # only the initial open, no reopen attempt


class TestScreenCapture:
    def test_emit_screen_captures_pane_and_feeds_process_screen(self, mocker):
        bridge = _bridge()
        mocker.patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="line one\nline two\n"))
        process_screen = mocker.patch.object(bridge, "process_screen")

        bridge._emit_screen()

        process_screen.assert_called_once_with("line one\nline two")

    def test_capture_uses_join_wrapped_lines_flag(self, mocker):
        bridge = _bridge()
        run = mocker.patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=""))

        bridge._emit_screen()

        args = run.call_args[0][0]
        assert args[:4] == ["tmux", "capture-pane", "-p", "-J"]
