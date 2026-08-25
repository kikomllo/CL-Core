import subprocess
import logging

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
        # Windows fallback using PowerShell
        import platform, re
        if platform.system() == "Windows":
            res = subprocess.check_output(["powershell", "-Command", "(Get-NetConnectionProfile -InterfaceAlias Wi-Fi*).Name"], text=True, stderr=subprocess.DEVNULL).strip()
            if res:
                # Windows sometimes appends ' 2', ' 3' to the profile name. Strip it to match the actual SSID.
                res = re.sub(r'\s\d+$', '', res)
                return res
    except Exception:
        pass

    return default_fallback
