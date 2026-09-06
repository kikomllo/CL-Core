import pytest
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import clHealth
from clHealth import check_ecosystem

EMPTY_DOCKER_PS = MagicMock(stdout="", returncode=0)


def _router(by_tool: dict):
    def _side_effect(cmd, **kwargs):
        result = by_tool.get(cmd[0])
        if result is None:
            raise FileNotFoundError(cmd[0])
        return result
    return _side_effect


class TestNativeUiStatusWindowsWithWmic:
    def test_reports_ok_when_a_process_id_is_found(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "windows")
        mocker.patch("clHealth.shutil.which", return_value=r"C:\Windows\System32\wbem\wmic.exe")
        mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "wmic": MagicMock(stdout="ProcessId  \r\n12345  \r\n"),
        }))

        check_ecosystem()

        assert "Native UI is running." in capsys.readouterr().out

    def test_reports_crashed_when_no_process_id_found(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "windows")
        mocker.patch("clHealth.shutil.which", return_value=r"C:\Windows\System32\wbem\wmic.exe")
        mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "wmic": MagicMock(stdout="No Instance(s) Available.\r\n"),
        }))

        check_ecosystem()

        assert "Native UI is NOT running." in capsys.readouterr().out


class TestNativeUiStatusWindowsWithoutWmic:
    """wmic is deprecated and can be entirely absent -- Get-CimInstance is
    the fallback, and it must actually get used when wmic isn't found."""

    def test_falls_back_to_powershell_get_ciminstance(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "windows")
        mocker.patch("clHealth.shutil.which", return_value=None)
        mock_run = mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "powershell": MagicMock(stdout="6789\r\n"),
        }))

        check_ecosystem()

        assert "Native UI is running." in capsys.readouterr().out
        ps_call = next(c for c in mock_run.call_args_list if c.args[0][0] == "powershell")
        assert "Get-CimInstance" in ps_call.args[0][-1]
        assert "clUI.py" in ps_call.args[0][-1]

    def test_reports_crashed_when_powershell_finds_nothing(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "windows")
        mocker.patch("clHealth.shutil.which", return_value=None)
        mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "powershell": MagicMock(stdout=""),
        }))

        check_ecosystem()

        assert "Native UI is NOT running." in capsys.readouterr().out


class TestNativeUiStatusLinux:
    def test_reports_ok_when_pgrep_finds_process(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "linux")
        mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "pgrep": MagicMock(returncode=0, stdout="4242\n"),
        }))

        check_ecosystem()

        assert "Native UI is running." in capsys.readouterr().out

    def test_reports_crashed_when_pgrep_finds_nothing(self, mocker, capsys):
        mocker.patch("clHealth.CURRENT_OS", "linux")
        mocker.patch("clHealth.subprocess.run", side_effect=_router({
            "docker": EMPTY_DOCKER_PS,
            "pgrep": MagicMock(returncode=1, stdout=""),
        }))

        check_ecosystem()

        assert "Native UI is NOT running." in capsys.readouterr().out
