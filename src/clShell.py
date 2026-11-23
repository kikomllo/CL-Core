"""The supervisor's handle on the desktop shell (shell/main.js, Electron): starts it, relays its log lines, reports module_ready once its
window is up, and exits when it does (so the supervisor's resurrect/crash-loop logic applies). The shell quits by itself if this
process is killed (it polls JARVIS_SHELL_PARENT), so a hard terminate by the supervisor cannot leave a window behind."""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "utils"))
from clShellSupport import SHELL_DIR, build_env, electron_path, needs_no_sandbox  # noqa: E402

READY_TIMEOUT_S = 30


def _announce_ready():
    try:
        import paho.mqtt.publish as publish
        publish.single("jarvis/sys/module_ready", json.dumps({"module": "desktop shell"}), qos=1, hostname="localhost")
    except Exception as e:
        print(f"[SHELL] could not announce ready: {e}", flush=True)


def main():
    exe = electron_path()
    if exe is None:
        print("[SHELL] Electron is not installed: run `npm install` in shell/ (needs Node).", flush=True)
        return 1
    cmd = [exe, SHELL_DIR] + (["--no-sandbox"] if needs_no_sandbox(exe) else [])
    env = build_env(os.environ, os.getpid(), os.environ.get("JARVIS_REBOOT"))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    announced = threading.Event()

    def announce_once():
        if not announced.is_set():
            announced.set()
            _announce_ready()

    fallback = threading.Timer(READY_TIMEOUT_S, announce_once)        # a slow first load must not hold back "all systems go"
    fallback.daemon = True
    fallback.start()
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
        if "[SHELL] ready" in line:
            announce_once()
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
