"""Helpers for the desktop shell (shell/, an Electron app that shows the web dashboard): finding the binary, the environment it is
started with, and which UI the supervisor runs. Pure (no MQTT, no Qt), shared by src/clShell.py, clJarvis.py and boot.py's checks."""
import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHELL_DIR = os.path.join(ROOT, "shell")


def electron_path(shell_dir=SHELL_DIR):
    """The Electron executable npm installed under shell/node_modules, or None. electron/path.txt names it relative to dist/."""
    base = os.path.join(shell_dir, "node_modules", "electron")
    try:
        with open(os.path.join(base, "path.txt"), "r", encoding="utf-8") as f:
            rel = f.read().strip()
    except OSError:
        return None
    exe = os.path.join(base, "dist", rel)
    return exe if rel and os.path.isfile(exe) else None


def shell_available(shell_dir=SHELL_DIR):
    return electron_path(shell_dir) is not None


def needs_no_sandbox(exe, platform=None):
    """On Linux an npm-installed Electron has a chrome-sandbox helper that is not root-owned setuid, and Chromium then refuses to start
    unless it is run with --no-sandbox. (The shell only ever loads this machine's own dashboard page.) Never on Windows."""
    if (platform or sys.platform) != "linux":
        return False
    helper = os.path.join(os.path.dirname(exe), "chrome-sandbox")
    try:
        st = os.stat(helper)
    except OSError:
        return True
    return not (st.st_uid == 0 and st.st_mode & stat.S_ISUID)


def build_env(base, parent_pid, reboot=None):
    """The environment for the shell process. ELECTRON_RUN_AS_NODE must go: VS Code (and so any terminal or Claude Code session
    started from it) sets it, and Electron then runs as plain Node without a window. CLAUDE_CODE_* never belongs in it either."""
    env = {k: v for k, v in base.items() if k != "ELECTRON_RUN_AS_NODE" and not k.startswith("CLAUDE_CODE_")}
    env["JARVIS_SHELL_PARENT"] = str(parent_pid)
    if reboot is not None:
        env["JARVIS_REBOOT"] = reboot
    return env


def choose_ui(native, available):
    """Which UI the supervisor starts. `native` is modules.json's "native" block. The desktop shell replaces the Qt dashboard; when it is
    switched on but not installed (no Node on this machine, or npm install failed) the Qt dashboard runs instead, so the machine is
    never left without a UI. Returns (native, note): a changed copy and a line to print, or None."""
    native = dict(native)
    if native.get("Desktop Shell", False) and not available:
        native["Desktop Shell"] = False
        native["UI"] = True
        return native, "Desktop shell is not installed (run `npm install` in shell/, needs Node): starting the Qt dashboard instead."
    return native, None
