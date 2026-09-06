import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'utils')))

from clNetworkUtils import get_current_wifi_ssid

# Real `netsh wlan show interfaces` output shape (captured live) -- SSID and
# the unrelated "AP BSSID" line must not be confused with each other.
NETSH_OUTPUT = """
    Name                   : Wi-Fi
    Description            : Intel(R) Dual Band Wireless-AC 7260
    GUID                   : 6a49d08a-6287-4cbc-a285-cde6cb546453
    Physical address       : f0:42:1c:27:a1:50
    Interface type         : Primary
    State                  : connected
    SSID                   : MEO-76C2D0
    AP BSSID               : 00:06:91:76:c2:d1
    Band                   : 5 GHz
    Signal                 : 71%
    Profile                : MEO-76C2D0
"""


def _check_output_router(responses):
    """Builds a subprocess.check_output side_effect that dispatches on the
    command name, since get_current_wifi_ssid tries iwgetid, nmcli, then
    netsh in sequence and each needs a distinct canned response/failure."""
    def _side_effect(cmd, **kwargs):
        tool = cmd[0]
        result = responses.get(tool)
        if result is None:
            raise FileNotFoundError(tool)
        if isinstance(result, Exception):
            raise result
        return result
    return _side_effect


class TestLinuxDetection:
    def test_iwgetid_result_used_directly(self, mocker):
        mocker.patch(
            "clNetworkUtils.subprocess.check_output",
            side_effect=_check_output_router({"iwgetid": "MyHomeNetwork\n"})
        )
        assert get_current_wifi_ssid() == "MyHomeNetwork"

    def test_falls_back_to_nmcli_when_iwgetid_unavailable(self, mocker):
        nmcli_output = "no:OtherNetwork\nyes:MyOfficeNetwork\n"
        mocker.patch(
            "clNetworkUtils.subprocess.check_output",
            side_effect=_check_output_router({"nmcli": nmcli_output})
        )
        assert get_current_wifi_ssid() == "MyOfficeNetwork"


class TestWindowsDetection:
    def test_uses_literal_ssid_from_netsh_not_profile_name(self, mocker):
        """netsh reports the actual broadcast SSID, matching iwgetid/nmcli --
        the previous Get-NetConnectionProfile approach returned a renamable
        profile *name* instead, which could silently diverge from the SSID."""
        mocker.patch("platform.system", return_value="Windows")
        mocker.patch(
            "clNetworkUtils.subprocess.check_output",
            side_effect=_check_output_router({"netsh": NETSH_OUTPUT})
        )
        assert get_current_wifi_ssid() == "MEO-76C2D0"

    def test_netsh_not_attempted_on_non_windows(self, mocker):
        mocker.patch("platform.system", return_value="Linux")
        mock_check_output = mocker.patch(
            "clNetworkUtils.subprocess.check_output",
            side_effect=_check_output_router({})
        )
        result = get_current_wifi_ssid(default_fallback="Fallback Net")

        assert result == "Fallback Net"
        called_tools = [c.args[0][0] for c in mock_check_output.call_args_list]
        assert "netsh" not in called_tools


class TestFallback:
    def test_returns_custom_default_when_everything_fails(self, mocker):
        mocker.patch("platform.system", return_value="Linux")
        mocker.patch(
            "clNetworkUtils.subprocess.check_output",
            side_effect=_check_output_router({})
        )
        assert get_current_wifi_ssid(default_fallback="Guest Network") == "Guest Network"
