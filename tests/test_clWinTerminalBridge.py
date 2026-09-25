"""Tests for the /terminal mode's Windows ConPTY backend. All winpty calls are
mocked -- no real ConPTY session needed."""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils.clWinTerminalBridge import WinTerminalBridge


def _bridge(cwd="."):
    return WinTerminalBridge(cwd=cwd, on_screen_update=MagicMock())


class TestStartAndAlive:
    def test_start_spawns_comspec_shell(self, mocker):
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        mocker.patch.dict(os.environ, {"COMSPEC": "C:\\Windows\\system32\\cmd.exe"})
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        spawn.return_value = MagicMock()

        _bridge().start()

        args = spawn.call_args[0][0]
        assert args == ["C:\\Windows\\system32\\cmd.exe"]

    def test_start_falls_back_to_cmd_exe_when_comspec_unset(self, mocker):
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        mocker.patch("os.environ.get", return_value=None)
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        spawn.return_value = MagicMock()

        _bridge().start()

        args = spawn.call_args[0][0]
        assert args == ["cmd.exe"]

    def test_is_alive_reflects_pty_isalive(self, mocker):
        bridge = _bridge()
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        proc = MagicMock()
        proc.isalive.return_value = True
        spawn.return_value = proc

        bridge.start()

        assert bridge.is_alive() is True

    def test_is_alive_false_before_start(self):
        assert _bridge().is_alive() is False


class TestWriteAndSendKeys:
    def _started_bridge(self, mocker):
        bridge = _bridge()
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        proc = MagicMock()
        proc.isalive.return_value = True
        spawn.return_value = proc
        bridge.start()
        return bridge, proc

    def test_write_sends_text_then_carriage_return(self, mocker):
        mocker.patch("time.sleep")
        bridge, proc = self._started_bridge(mocker)

        bridge.write("dir")

        calls = [c.args[0] for c in proc.write.call_args_list]
        assert calls == ["dir", "\r"]

    def test_write_raises_if_session_not_alive(self):
        bridge = _bridge()
        with pytest.raises(RuntimeError, match="not alive"):
            bridge.write("hello")

    def test_send_keys_writes_raw_bytes_with_no_appended_cr(self, mocker):
        bridge, proc = self._started_bridge(mocker)

        bridge.send_keys("\x1bOA")

        proc.write.assert_called_once_with("\x1bOA")


class TestSendControlKey:
    def test_escape_token_becomes_ctrl_c_interrupt(self, mocker):
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("ESCAPE")

        send_keys.assert_called_once_with("\x03")

    def test_shift_tab_token_maps_to_escape_sequence(self, mocker):
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("SHIFT_TAB")

        send_keys.assert_called_once_with("\x1b[Z")

    def test_unmapped_token_falls_back_to_carriage_return(self, mocker):
        bridge = _bridge()
        send_keys = mocker.patch.object(bridge, "send_keys")

        bridge.send_control_key("SOMETHING_UNKNOWN")

        send_keys.assert_called_once_with("\r")


class TestStop:
    def test_stop_terminates_the_pty_process(self, mocker):
        bridge = _bridge()
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        proc = MagicMock()
        proc.isalive.return_value = True
        spawn.return_value = proc
        bridge.start()

        bridge.stop()

        proc.terminate.assert_called_once_with(force=True)

    def test_stop_on_never_started_bridge_does_not_raise(self):
        _bridge().stop()


class TestResize:
    def _started_bridge(self, mocker):
        bridge = _bridge()
        mocker.patch.object(WinTerminalBridge, "_read_loop")
        spawn = mocker.patch("winpty.PtyProcess.spawn")
        proc = MagicMock()
        proc.isalive.return_value = True
        spawn.return_value = proc
        bridge.start()
        return bridge, proc

    def test_resize_sets_conpty_winsize_and_pyte_screen(self, mocker):
        bridge, proc = self._started_bridge(mocker)

        bridge.resize(100, 30)

        proc.setwinsize.assert_called_once_with(30, 100)
        assert bridge._screen.columns == 100
        assert bridge._screen.lines == 30
        assert bridge.COLS == 100
        assert bridge.ROWS == 30

    def test_resize_skips_conpty_call_when_not_alive(self, mocker):
        bridge, proc = self._started_bridge(mocker)
        proc.isalive.return_value = False

        bridge.resize(100, 30)

        proc.setwinsize.assert_not_called()
        assert bridge._screen.columns == 100
