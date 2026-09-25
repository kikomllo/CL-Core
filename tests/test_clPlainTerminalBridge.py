"""Tests for the /terminal mode's plain-shell tmux backend. All tmux/subprocess
calls are mocked -- no real tmux server needed."""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clPlainTerminalBridge import PlainTerminalBridge


def _bridge(cwd="."):
    return PlainTerminalBridge(cwd=cwd, on_screen_update=MagicMock())


class TestStartAndAlive:
    def test_missing_tmux_raises_clear_error(self, mocker):
        mocker.patch("shutil.which", return_value=None)
        with pytest.raises(FileNotFoundError, match="tmux not found"):
            _bridge().start()

    def test_reuses_existing_session_instead_of_spawning_a_new_one(self, mocker):
        mocker.patch.object(PlainTerminalBridge, "_has_session", return_value=True)
        mocker.patch.object(PlainTerminalBridge, "_start_pipe_reader")
        run = mocker.patch("subprocess.run")

        _bridge().start()

        run.assert_not_called()

    def test_is_alive_reflects_has_session(self, mocker):
        bridge = _bridge()
        mocker.patch.object(PlainTerminalBridge, "_has_session", return_value=True)
        assert bridge.is_alive() is True


class TestWriteAndSendKeys:
    def test_write_sends_literal_text_then_enter(self, mocker):
        bridge = _bridge()
        mocker.patch.object(PlainTerminalBridge, "is_alive", return_value=True)
        run = mocker.patch("subprocess.run")

        bridge.write("ls -la")

        first_call = run.call_args_list[0][0][0]
        second_call = run.call_args_list[1][0][0]
        assert first_call == ["tmux", "send-keys", "-t", bridge.SESSION_NAME, "-l", "ls -la"]
        assert second_call == ["tmux", "send-keys", "-t", bridge.SESSION_NAME, "Enter"]

    def test_write_raises_if_session_not_alive(self, mocker):
        bridge = _bridge()
        mocker.patch.object(PlainTerminalBridge, "is_alive", return_value=False)
        with pytest.raises(RuntimeError, match="not alive"):
            bridge.write("hello")

    def test_send_keys_maps_to_tmux_key_names(self, mocker):
        bridge = _bridge()
        mocker.patch.object(PlainTerminalBridge, "is_alive", return_value=True)
        run = mocker.patch("subprocess.run")

        bridge.send_keys("Up")

        run.assert_called_once_with(["tmux", "send-keys", "-t", bridge.SESSION_NAME, "Up"], check=True)


class TestSendControlKey:
    def test_escape_token_becomes_ctrl_c_interrupt(self, mocker):
        """A shell has no TUI to escape out of, but does have a running
        command to interrupt -- 'ESCAPE' maps to Ctrl-C here, not to tmux's
        own 'Escape' key."""
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("ESCAPE")

        send_keys.assert_called_once_with("C-c")

    def test_shift_tab_token_uses_tmux_btab_override(self, mocker):
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("SHIFT_TAB")

        send_keys.assert_called_once_with("BTab")

    def test_unmapped_token_falls_back_to_title_case(self, mocker):
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("UP")

        send_keys.assert_called_once_with("Up")


class TestStop:
    def test_stop_kills_the_tmux_session(self, mocker):
        bridge = _bridge()
        run = mocker.patch("subprocess.run")

        bridge.stop()

        run.assert_called_once_with(
            ["tmux", "kill-session", "-t", bridge.SESSION_NAME], capture_output=True
        )
