"""
Minimal tmux-backed plain shell session for the Claude widget's "/terminal"
mode -- a real shell (not the claude CLI), toggled to via a "/terminal" message
and back via "/claude" (see clClaudeBridge.py), sharing the widget's terminal
view and input box. Deliberately much simpler than ClaudeTmuxBridge: no
onboarding auto-answers, sign-in URL detection, or stall recovery apply to a
bare shell prompt.

Linux-only for now (tmux), same as the Claude bridge's own Linux backend --
see clClaudeBridge.py's _terminal_backend_class().
"""
import logging
import os
import select
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional


class PlainTerminalBridge:
    SESSION_NAME = "jarvis-terminal"
    COLS = 120
    ROWS = 40
    DEBOUNCE_S = 0.15  # coalesce streamed output into one screen update
    POLL_S = 0.2

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None]):
        self._cwd = cwd
        self._on_screen_update = on_screen_update
        self._fifo_path: Optional[str] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def _has_session(self) -> bool:
        result = subprocess.run(["tmux", "has-session", "-t", self.SESSION_NAME], capture_output=True)
        return result.returncode == 0

    def start(self):
        if not shutil.which("tmux"):
            raise FileNotFoundError("tmux not found on PATH -- install tmux to use the terminal widget.")
        if self._has_session():
            logging.info(f"[PLAIN TERMINAL] Reusing existing tmux session '{self.SESSION_NAME}'.")
        else:
            shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
            subprocess.run([
                "tmux", "new-session", "-d", "-s", self.SESSION_NAME,
                "-x", str(self.COLS), "-y", str(self.ROWS), "-c", self._cwd, shell,
            ], check=True)
            logging.info("[PLAIN TERMINAL] shell session started in tmux.")
        self._running = True
        self._start_pipe_reader()

    def is_alive(self) -> bool:
        return self._has_session()

    def write(self, text: str):
        if not self.is_alive():
            raise RuntimeError("terminal session is not alive")
        # Two sends with a gap, not "text\n": matches ClaudeTmuxBridge.write()'s
        # rationale -- a burst can drop a keystroke in whatever's reading it.
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME, "-l", text], check=True)
        time.sleep(0.05)
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME, "Enter"], check=True)

    def send_keys(self, keys: str):
        """Space-separated tmux key names (e.g. 'C-c', 'Up'), same convention as ClaudeTmuxBridge."""
        if not self.is_alive():
            raise RuntimeError("terminal session is not alive")
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME] + keys.split(), check=True)

    def refresh_screen(self):
        """On-demand capture, so switching modes shows the current screen
        immediately instead of waiting for the shell to next print something."""
        self._emit_screen()

    def _start_pipe_reader(self):
        fd, self._fifo_path = tempfile.mkstemp(prefix="jarvis_terminal_", suffix=".fifo")
        os.close(fd)
        os.remove(self._fifo_path)
        os.mkfifo(self._fifo_path)
        subprocess.run(
            ["tmux", "pipe-pane", "-O", "-t", self.SESSION_NAME, f"cat >> {self._fifo_path}"],
            check=True,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        try:
            fifo_fd = os.open(self._fifo_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self._running = False
            return
        pending = False
        last_data_at = time.time()
        try:
            while self._running:
                try:
                    ready, _, _ = select.select([fifo_fd], [], [], self.POLL_S)
                except (OSError, ValueError):
                    break
                if ready:
                    data = os.read(fifo_fd, 65536)
                    if data:
                        last_data_at = time.time()
                        pending = True
                    else:
                        # Writer end closed; reopening lets a future writer be seen. A concurrent
                        # stop() deleting the fifo here just means teardown is in progress, not a crash.
                        os.close(fifo_fd)
                        if not self._running:
                            break
                        time.sleep(self.POLL_S)
                        try:
                            fifo_fd = os.open(self._fifo_path, os.O_RDONLY | os.O_NONBLOCK)
                        except OSError:
                            break
                if pending and (time.time() - last_data_at) >= self.DEBOUNCE_S:
                    pending = False
                    self._emit_screen()
                if not self.is_alive():
                    break
        finally:
            try:
                os.close(fifo_fd)
            except OSError:
                pass
        self._running = False

    def _emit_screen(self):
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", self.SESSION_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            try:
                self._on_screen_update(result.stdout.rstrip("\n"))
            except Exception as e:
                logging.error(f"[PLAIN TERMINAL] on_screen_update callback failed: {e}")

    def stop(self):
        self._running = False
        try:
            subprocess.run(["tmux", "kill-session", "-t", self.SESSION_NAME], capture_output=True)
        except Exception:
            pass
        if self._fifo_path and os.path.exists(self._fifo_path):
            try:
                os.remove(self._fifo_path)
            except OSError:
                pass
