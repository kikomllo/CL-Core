import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from utils import clAutostart


@pytest.fixture
def startup_dir(tmp_path):
    return str(tmp_path / "startup")


def test_disabled_until_enabled(startup_dir):
    assert clAutostart.is_enabled(startup_dir) is False


def test_disable_when_never_enabled_is_a_noop(startup_dir):
    assert clAutostart.set_enabled(False, startup_dir) is False


def test_enable_then_disable_round_trip(startup_dir):
    assert clAutostart.set_enabled(True, startup_dir) is True
    assert clAutostart.is_enabled(startup_dir) is True
    assert clAutostart.set_enabled(False, startup_dir) is False
    assert clAutostart.is_enabled(startup_dir) is False


def test_enable_twice_is_idempotent(startup_dir):
    clAutostart.set_enabled(True, startup_dir)
    clAutostart.set_enabled(True, startup_dir)
    assert clAutostart.is_enabled(startup_dir) is True
    assert len(os.listdir(startup_dir)) == 1


def test_entry_name_matches_the_os():
    name = os.path.basename(clAutostart.entry_path("x"))
    with patch.object(clAutostart, "CURRENT_OS", "Windows"):
        assert os.path.basename(clAutostart.entry_path("x")) == "JARVIS.lnk"
    with patch.object(clAutostart, "CURRENT_OS", "Linux"):
        assert os.path.basename(clAutostart.entry_path("x")) == "jarvis.desktop"
    assert name in ("JARVIS.lnk", "jarvis.desktop")


class TestLinuxDesktopEntry:
    def test_runs_boot_py_from_the_repo(self):
        text = clAutostart.desktop_entry("/home/me/CL-Core", "/home/me/CL-Core/.venv/bin/python")
        assert 'Exec="/home/me/CL-Core/.venv/bin/python" "/home/me/CL-Core/boot.py"' in text
        assert "Path=/home/me/CL-Core\n" in text
        assert text.startswith("[Desktop Entry]\nType=Application\n")

    def test_paths_with_spaces_stay_one_argument(self):
        text = clAutostart.desktop_entry("/home/me/My Stuff/CL-Core", "/usr/bin/python3")
        assert 'Exec="/usr/bin/python3" "/home/me/My Stuff/CL-Core/boot.py"' in text

    def test_special_characters_are_escaped(self):
        assert clAutostart._desktop_quote('a"b$c') == '"a\\"b\\$c"'

    def test_written_file_on_linux(self, startup_dir, tmp_path):
        with patch.object(clAutostart, "CURRENT_OS", "Linux"):
            clAutostart.set_enabled(True, startup_dir, root=str(tmp_path))
            with open(clAutostart.entry_path(startup_dir), encoding="utf-8") as f:
                content = f.read()
        assert "boot.py" in content and f"Path={tmp_path}" in content


class TestPythonChoice:
    def test_prefers_the_project_venv(self, tmp_path):
        sub = ("Scripts", "python.exe") if clAutostart.CURRENT_OS == "Windows" else ("bin", "python")
        exe = tmp_path / ".venv" / sub[0] / sub[1]
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        assert clAutostart._venv_python(str(tmp_path)) == str(exe)

    def test_falls_back_to_the_running_interpreter(self, tmp_path):
        assert clAutostart._venv_python(str(tmp_path)) == sys.executable


class TestWindowsShortcut:
    def test_shortcut_script_sets_target_cwd_and_minimized(self, startup_dir, tmp_path):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            os.makedirs(startup_dir, exist_ok=True)
            open(os.path.join(startup_dir, "JARVIS.lnk"), "w").close()

            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()

        with patch.object(clAutostart, "CURRENT_OS", "Windows"), \
                patch.object(clAutostart.subprocess, "run", fake_run):
            assert clAutostart.set_enabled(True, startup_dir, root=r"C:\Users\O'Neil\CL-Core") is True

        script = calls[0][-1]
        assert "boot.py" in script
        assert "WindowStyle = 7" in script
        assert "WorkingDirectory = 'C:\\Users\\O''Neil\\CL-Core'" in script  # quote doubled for PowerShell

    def test_failure_raises_instead_of_pretending(self, startup_dir, tmp_path):
        class R:
            returncode, stdout, stderr = 1, "", "boom"

        with patch.object(clAutostart, "CURRENT_OS", "Windows"), \
                patch.object(clAutostart.subprocess, "run", lambda *a, **k: R()):
            with pytest.raises(OSError):
                clAutostart.set_enabled(True, startup_dir, root=str(tmp_path))
        assert clAutostart.is_enabled(startup_dir) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="needs PowerShell and a real .lnk")
    def test_real_shortcut_is_created_in_a_temp_folder(self, startup_dir):
        assert clAutostart.set_enabled(True, startup_dir) is True
        assert os.path.getsize(clAutostart.entry_path(startup_dir)) > 0
        assert clAutostart.set_enabled(False, startup_dir) is False


class TestWindowsStartupInterpreter:
    """The login shortcut runs pythonw.exe so no console flashes at login; boot.py then hides the rest."""

    def _venv(self, tmp_path, with_pythonw=True):
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_text("")
        if with_pythonw:
            (scripts / "pythonw.exe").write_text("")
        return scripts

    def test_prefers_pythonw_next_to_the_venv_python(self, tmp_path):
        scripts = self._venv(tmp_path)
        assert clAutostart._startup_python(str(tmp_path)) == str(scripts / "pythonw.exe")

    def test_falls_back_to_python_when_there_is_no_pythonw(self, tmp_path):
        scripts = self._venv(tmp_path, with_pythonw=False)
        assert clAutostart._startup_python(str(tmp_path)) == str(scripts / "python.exe")

    def test_the_shortcut_targets_it(self, startup_dir, tmp_path):
        scripts = self._venv(tmp_path)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            os.makedirs(startup_dir, exist_ok=True)
            open(os.path.join(startup_dir, "JARVIS.lnk"), "w").close()

            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()

        with patch.object(clAutostart, "CURRENT_OS", "Windows"), patch.object(clAutostart.subprocess, "run", fake_run):
            clAutostart.set_enabled(True, startup_dir, root=str(tmp_path))
        assert f"TargetPath = '{scripts / 'pythonw.exe'}'" in calls[0][-1]
