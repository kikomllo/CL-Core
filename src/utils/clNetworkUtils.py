import re
import subprocess
import logging
from typing import Callable, List, Optional, Set

def get_current_wifi_ssid(default_fallback: str = "Home Network") -> str:
    """Detects the current active WiFi network SSID on the system."""
    try:
        res = subprocess.check_output(["iwgetid", "-r"], text=True, stderr=subprocess.DEVNULL).strip()
        if res:
            return res
    except Exception:
        pass

    try:
        res = subprocess.check_output(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], text=True, stderr=subprocess.DEVNULL)
        for line in res.splitlines():
            if line.startswith("yes:"):
                ssid = line.split(":", 1)[1].strip()
                if ssid:
                    return ssid
    except Exception:
        pass

    try:
        import platform
        if platform.system() == "Windows":
            # Literal broadcast SSID, unlike Get-NetConnectionProfile's renamable profile name.
            res = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True, stderr=subprocess.DEVNULL)
            for line in res.splitlines():
                line = line.strip()
                if line.startswith("SSID") and ":" in line:
                    ssid = line.split(":", 1)[1].strip()
                    if ssid:
                        return ssid
    except Exception:
        pass

    return default_fallback


class ArpUnavailable(Exception):
    """No way to read this machine's ARP/neighbor cache was found."""


_MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")


def _ip_neigh() -> Optional[Set[str]]:
    """Modern Linux: `ip neigh` (iproute2), already-resolved neighbors only."""
    out = subprocess.check_output(["ip", "neigh"], text=True, stderr=subprocess.DEVNULL, timeout=3)
    macs = set()
    for line in out.splitlines():
        parts = line.split()
        if "lladdr" in parts and "FAILED" not in parts:
            macs.add(parts[parts.index("lladdr") + 1].lower())
    return macs


def _proc_net_arp() -> Optional[Set[str]]:
    """Older Linux: the /proc/net/arp pseudo-file, one line per entry after a header row."""
    with open("/proc/net/arp", encoding="utf-8") as f:
        lines = f.read().splitlines()
    macs = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[3] != "00:00:00:00:00:00":
            macs.add(fields[3].lower())
    return macs


def _arp_a() -> Optional[Set[str]]:
    """`arp -a` (net-tools): present on both Linux and Windows, with differently laid out but
    still MAC-shaped output, so a plain pattern match reads either without caring which OS it's on."""
    out = subprocess.check_output(["arp", "-a"], text=True, stderr=subprocess.DEVNULL, timeout=3)
    return {m.group(0).lower().replace("-", ":") for m in _MAC_RE.finditer(out)}


# Tried in order; the first one that runs at all wins (even if it finds nothing on the network).
_ARP_SOURCES: List[Callable[[], Optional[Set[str]]]] = [_ip_neigh, _proc_net_arp, _arp_a]


def local_network_macs() -> Set[str]:
    """Every device address (lower-case, colon-separated) in this machine's ARP/neighbor cache right
    now -- i.e. devices that have recently talked on the local network, wired or WiFi. Raises
    ArpUnavailable if no supported tool exists here at all (never for an empty, working result)."""
    for source in _ARP_SOURCES:
        try:
            macs = source()
        except (OSError, subprocess.SubprocessError):
            continue
        if macs is not None:
            return macs
    raise ArpUnavailable("no ARP/neighbor table tool (ip, /proc/net/arp, arp) is available")
