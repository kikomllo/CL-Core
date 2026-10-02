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


import subprocess
from clNetworkUtils import ArpUnavailable, local_network_macs

IP_NEIGH_OUTPUT = """192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff STALE
192.168.1.50 dev eth0  FAILED
192.168.1.99 dev eth0 lladdr 11:22:33:44:55:66 REACHABLE
"""

PROC_NET_ARP_OUTPUT = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         AA:BB:CC:DD:EE:FF     *        eth0
192.168.1.50     0x1         0x0         00:00:00:00:00:00     *        eth0
"""

ARP_A_LINUX = """? (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0
? (192.168.1.50) at <incomplete> on eth0
"""

ARP_A_WINDOWS = """Interface: 192.168.1.100 --- 0xb
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic
  192.168.1.254         ff-ff-ff-ff-ff-ff     static
"""


class TestLocalNetworkMacs:
    def test_ip_neigh_skips_failed_entries(self, mocker):
        mocker.patch("clNetworkUtils.subprocess.check_output", return_value=IP_NEIGH_OUTPUT)
        assert local_network_macs() == {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"}

    def test_falls_back_to_proc_net_arp_when_ip_is_missing(self, mocker):
        mocker.patch("clNetworkUtils.subprocess.check_output", side_effect=FileNotFoundError)
        mocker.patch("clNetworkUtils.open", mocker.mock_open(read_data=PROC_NET_ARP_OUTPUT))
        assert local_network_macs() == {"aa:bb:cc:dd:ee:ff"}   # the all-zero row is skipped

    def test_falls_back_to_arp_a_linux_when_neither_is_available(self, mocker):
        mocker.patch("clNetworkUtils.subprocess.check_output",
                    side_effect=[FileNotFoundError, "no such thing", ARP_A_LINUX])

        def fake_check_output(cmd, **kw):
            if cmd[0] == "ip":
                raise FileNotFoundError
            if cmd[0] == "arp":
                return ARP_A_LINUX
            raise FileNotFoundError
        mocker.patch("clNetworkUtils.subprocess.check_output", side_effect=fake_check_output)
        mocker.patch("clNetworkUtils.open", side_effect=OSError)
        assert local_network_macs() == {"aa:bb:cc:dd:ee:ff"}

    def test_arp_a_windows_dashes_are_normalized_to_colons(self, mocker):
        def fake_check_output(cmd, **kw):
            if cmd[0] == "arp":
                return ARP_A_WINDOWS
            raise FileNotFoundError
        mocker.patch("clNetworkUtils.subprocess.check_output", side_effect=fake_check_output)
        mocker.patch("clNetworkUtils.open", side_effect=OSError)
        assert local_network_macs() == {"aa:bb:cc:dd:ee:ff", "ff:ff:ff:ff:ff:ff"}

    def test_raises_when_nothing_at_all_is_available(self, mocker):
        mocker.patch("clNetworkUtils.subprocess.check_output", side_effect=FileNotFoundError)
        mocker.patch("clNetworkUtils.open", side_effect=OSError)
        with pytest.raises(ArpUnavailable):
            local_network_macs()

    def test_an_empty_but_working_table_is_not_unavailable(self, mocker):
        mocker.patch("clNetworkUtils.subprocess.check_output", return_value="")
        assert local_network_macs() == set()
