import os
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import boot


class TestShouldRunHidden:
    """boot.py used to run inside the console it was started from, and closing that console killed the whole ecosystem."""

    def test_windows_runs_hidden_by_default(self):
        assert boot.should_run_hidden([], {}, True) is True

    def test_console_flag_keeps_it_in_the_window(self):
        assert boot.should_run_hidden(["--console"], {}, True) is False

    def test_the_detached_process_and_its_children_do_not_detach_again(self):
        assert boot.should_run_hidden([], {boot.HIDDEN_ENV: "1"}, True) is False

    def test_linux_is_untouched(self):
        assert boot.should_run_hidden([], {}, False) is False


class TestRelaunchHidden:
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows process-creation flags")
    def test_new_hidden_console_and_own_process_group_marked_as_detached(self):
        with patch.object(boot.subprocess, "Popen") as popen:
            boot.relaunch_hidden("C:/x/boot.py", ["--foo"], env={"A": "1"})
        cmd, kw = popen.call_args.args[0], popen.call_args.kwargs
        assert cmd[1:] == ["C:/x/boot.py", "--foo"]
        assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert kw["env"][boot.HIDDEN_ENV] == "1" and kw["env"]["A"] == "1"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows process-creation flags")
    def test_a_pythonw_start_relaunches_with_python_exe(self, tmp_path):
        (tmp_path / "pythonw.exe").write_text("")
        (tmp_path / "python.exe").write_text("")
        with patch.object(boot.sys, "executable", str(tmp_path / "pythonw.exe")), patch.object(boot.subprocess, "Popen") as popen:
            boot.relaunch_hidden("boot.py", [])
        assert popen.call_args.args[0][0] == str(tmp_path / "python.exe")
