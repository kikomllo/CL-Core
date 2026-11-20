"""Start JARVIS when the user logs in (Settings -> System -> Startup).

The OS entry itself is the source of truth (nothing is mirrored into core.json, which is per-machine
anyway), so the checkbox always shows what will really happen at the next login.

* Windows: a shortcut in the user's Startup folder (no admin rights; it also shows up in Task Manager's
  Startup apps). A shortcut, not a registry Run value, because only it can set the working directory,
  and boot.py resolves .venv/ requirements.txt/config/ relative to it. Started minimized.
* Linux: an XDG autostart entry (~/.config/autostart/jarvis.desktop), honoured by GNOME, KDE, XFCE...
  `Path=` sets the working directory.

Both launch `boot.py`, which builds/updates the venv and execs the supervisor, same as a manual start.
"""
import os
import platform
import posixpath
import subprocess
import sys

APP_NAME = "JARVIS"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

CURRENT_OS = platform.system()


def _venv_python(root: str) -> str:
    """The project venv's interpreter when it exists, else the running one (boot.py creates the venv)."""
    sub = os.path.join("Scripts", "python.exe") if CURRENT_OS == "Windows" else os.path.join("bin", "python")
    candidate = os.path.join(root, ".venv", sub)
    return candidate if os.path.exists(candidate) else sys.executable


def entry_path(startup_dir: str = None) -> str:
    """Where the autostart entry lives on this OS. `startup_dir` overrides the folder (tests)."""
    if CURRENT_OS == "Windows":
        base = startup_dir or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        return os.path.join(base, f"{APP_NAME}.lnk")
    base = startup_dir or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "autostart")
    return os.path.join(base, f"{APP_NAME.lower()}.desktop")


def is_enabled(startup_dir: str = None) -> bool:
    return os.path.isfile(entry_path(startup_dir))


def _desktop_quote(arg: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec (double quotes, escape \\ " ` $)."""
    escaped = "".join("\\" + c if c in '\\"`$' else c for c in arg)
    return f'"{escaped}"'


def desktop_entry(root: str = REPO_ROOT, python: str = None) -> str:
    python = python or _venv_python(root)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=JARVIS Smart Home OS\n"
        f"Exec={_desktop_quote(python)} {_desktop_quote(posixpath.join(root, 'boot.py'))}\n"
        f"Path={root}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _create_windows_shortcut(link: str, root: str, python: str) -> None:
    def q(v):  # PowerShell single-quoted string
        return "'" + v.replace("'", "''") + "'"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(link) + "); "
        "$s.TargetPath = " + q(python) + "; "
        "$s.Arguments = 'boot.py'; "
        "$s.WorkingDirectory = " + q(root) + "; "
        "$s.WindowStyle = 7; "  # minimized: the boot console shouldn't sit on the desktop
        "$s.Description = 'JARVIS Smart Home OS'; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 or not os.path.isfile(link):
        raise OSError(f"could not create the startup shortcut: {(result.stderr or result.stdout).strip()}")


def set_enabled(enabled: bool, startup_dir: str = None, root: str = REPO_ROOT) -> bool:
    """Create or remove the autostart entry. Returns the resulting state; raises OSError on failure."""
    path = entry_path(startup_dir)
    if not enabled:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return False

    os.makedirs(os.path.dirname(path), exist_ok=True)
    python = _venv_python(root)
    if CURRENT_OS == "Windows":
        _create_windows_shortcut(path, root, python)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(desktop_entry(root, python))
    return True
