"""The desktop shell's Python side: finding Electron, the environment it gets, which UI the supervisor runs, and the launcher."""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "utils")))
import clShell
import clShellSupport as support


def fake_install(tmp_path, exe_name="electron.exe", with_exe=True):
    base = tmp_path / "node_modules" / "electron"
    (base / "dist").mkdir(parents=True)
    (base / "path.txt").write_text(exe_name, encoding="utf-8")
    if with_exe:
        (base / "dist" / exe_name).write_bytes(b"")
    return str(tmp_path)


class TestElectronPath:
    def test_found_through_the_path_file_npm_writes(self, tmp_path):
        d = fake_install(tmp_path)
        assert support.electron_path(d).endswith(os.path.join("dist", "electron.exe"))
        assert support.shell_available(d)

    def test_not_installed_is_none(self, tmp_path):
        assert support.electron_path(str(tmp_path)) is None
        assert not support.shell_available(str(tmp_path))

    def test_a_path_file_without_the_binary_is_not_installed(self, tmp_path):
        assert support.electron_path(fake_install(tmp_path, with_exe=False)) is None


class TestEnvironment:
    def test_electron_run_as_node_is_removed_or_the_shell_would_be_plain_node_with_no_window(self):
        env = support.build_env({"ELECTRON_RUN_AS_NODE": "1", "PATH": "x"}, 123)
        assert "ELECTRON_RUN_AS_NODE" not in env and env["PATH"] == "x"

    def test_claude_code_variables_are_not_inherited(self):
        assert not any(k.startswith("CLAUDE_CODE_") for k in support.build_env({"CLAUDE_CODE_ENTRYPOINT": "x", "CLAUDE_CODE_CHILD_NOTES_DIR": "y"}, 1))

    def test_it_is_told_its_parent_and_whether_this_is_a_restart(self):
        env = support.build_env({}, 4242, "1")
        assert env["JARVIS_SHELL_PARENT"] == "4242" and env["JARVIS_REBOOT"] == "1"
        assert "JARVIS_REBOOT" not in support.build_env({}, 1)


class TestSandboxFlag:
    def test_never_on_windows(self):
        assert support.needs_no_sandbox("C:/x/electron.exe", platform="win32") is False

    def test_linux_without_a_setuid_root_helper_needs_it(self, tmp_path):
        exe = tmp_path / "electron"
        exe.write_bytes(b"")
        assert support.needs_no_sandbox(str(exe), platform="linux") is True            # no chrome-sandbox next to it
        (tmp_path / "chrome-sandbox").write_bytes(b"")
        assert support.needs_no_sandbox(str(exe), platform="linux") is True            # present, but not root-owned setuid


class TestChooseUi:
    def test_the_shell_runs_when_it_is_installed(self):
        native, note = support.choose_ui({"UI": False, "Desktop Shell": True}, True)
        assert native == {"UI": False, "Desktop Shell": True} and note is None

    def test_the_qt_dashboard_takes_over_when_it_is_not(self):
        native, note = support.choose_ui({"UI": False, "Desktop Shell": True}, False)
        assert native == {"UI": True, "Desktop Shell": False} and "Qt dashboard" in note

    def test_a_shell_switched_off_is_left_alone(self):
        native, note = support.choose_ui({"UI": True, "Desktop Shell": False}, False)
        assert native == {"UI": True, "Desktop Shell": False} and note is None

    def test_the_input_is_not_modified(self):
        original = {"UI": False, "Desktop Shell": True}
        support.choose_ui(original, False)
        assert original == {"UI": False, "Desktop Shell": True}


class TestLauncher:
    def run_main(self, monkeypatch, lines, exit_code=0, exe="C:/x/electron.exe", reboot=None):
        proc = MagicMock()
        proc.stdout = iter(lines)
        proc.wait.return_value = exit_code
        popen = MagicMock(return_value=proc)
        ready = MagicMock()
        monkeypatch.setattr(clShell, "electron_path", lambda: exe)
        monkeypatch.setattr(clShell, "needs_no_sandbox", lambda e: False)
        monkeypatch.setattr(clShell.subprocess, "Popen", popen)
        monkeypatch.setattr(clShell, "_announce_ready", ready)
        monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
        if reboot is None:
            monkeypatch.delenv("JARVIS_REBOOT", raising=False)
        else:
            monkeypatch.setenv("JARVIS_REBOOT", reboot)
        return clShell.main(), popen, ready

    def test_without_electron_it_fails_with_a_clear_message(self, monkeypatch, capsys):
        monkeypatch.setattr(clShell, "electron_path", lambda: None)
        assert clShell.main() == 1
        assert "npm install" in capsys.readouterr().out

    def test_it_starts_the_shell_with_a_clean_environment_and_its_own_pid_as_parent(self, monkeypatch):
        _, popen, _ = self.run_main(monkeypatch, [])
        cmd = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        assert cmd[0] == "C:/x/electron.exe" and cmd[1] == clShell.SHELL_DIR
        assert "ELECTRON_RUN_AS_NODE" not in env and env["JARVIS_SHELL_PARENT"] == str(os.getpid())

    def test_a_restart_is_passed_on_so_the_saved_mode_is_reopened(self, monkeypatch):
        _, popen, _ = self.run_main(monkeypatch, [], reboot="1")
        assert popen.call_args.kwargs["env"]["JARVIS_REBOOT"] == "1"

    def test_module_ready_is_announced_once_when_the_window_is_up(self, monkeypatch, capsys):
        _, _, ready = self.run_main(monkeypatch, ["[SHELL] ready mode=overlay\n", "[SHELL] ready mode=overlay\n"])
        assert ready.call_count == 1
        assert "[SHELL] ready" in capsys.readouterr().out                      # its log lines are relayed

    def test_it_exits_with_the_shells_exit_code(self, monkeypatch):
        code, _, ready = self.run_main(monkeypatch, ["something else\n"], exit_code=3)
        assert code == 3 and ready.call_count == 0
