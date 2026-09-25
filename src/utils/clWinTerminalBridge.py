"""
Windows-only ConPTY-backed plain shell session for the Claude widget's "/terminal"
mode -- a real shell (not the claude CLI), toggled to via a "/terminal" message and
back via "/claude" (see clClaudeBridge.py), sharing the widget's terminal view and
input box. Windows counterpart to PlainTerminalBridge (clPlainTerminalBridge.py,
tmux-backed on Linux) -- same public interface and same reason for pyte: ConPTY
(via pywinpty) hands back raw ANSI bytes, not a resolved screen, same as
ClaudePtyBridge already needs it for the claude session backend.

Deliberately much simpler than ClaudePtyBridge: no onboarding auto-answers, sign-in
URL detection, or stall recovery apply to a bare shell prompt, so this doesn't
subclass ClaudeSessionBase either -- same relationship PlainTerminalBridge has to
ClaudeTmuxBridge on Linux.
"""
import logging
import os
import select
import threading
import time
from typing import Callable, Optional

import pyte


class WinTerminalBridge:
    COLS = 120
    ROWS = 40
    DEBOUNCE_S = 0.15  # coalesce streamed output into one screen update
    POLL_S = 0.2

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None],
                 cols: Optional[int] = None, rows: Optional[int] = None):
        self._cwd = cwd
        self._on_screen_update = on_screen_update
        if cols:
            self.COLS = cols
        if rows:
            self.ROWS = rows
        self._screen = pyte.Screen(self.COLS, self.ROWS)
        self._stream = pyte.Stream(self._screen)
        self._pty = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        import winpty
        shell = os.environ.get("COMSPEC") or "cmd.exe"
        with self._lock:
            self._pty = winpty.PtyProcess.spawn(
                [shell],
                cwd=self._cwd,
                dimensions=(self.ROWS, self.COLS),
                # Same ConPTY backend ClaudePtyBridge uses -- the default silently
                # hangs on reads on this stack.
                backend=1,
            )
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        logging.info("[WIN TERMINAL] shell session started.")

    def is_alive(self) -> bool:
        return bool(self._pty and self._pty.isalive())

    def write(self, text: str):
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("terminal session is not alive")
            # Two writes with a gap, not "text\r": matches ClaudePtyBridge.write()'s
            # rationale -- a burst can drop a keystroke in whatever's reading it.
            self._pty.write(text)
            time.sleep(0.05)
            self._pty.write("\r")

    def send_keys(self, raw: str):
        """Raw bytes, no appended CR -- write() types a line and submits it."""
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("terminal session is not alive")
            self._pty.write(raw)

    # Maps the widget's backend-neutral control-key tokens (shared with
    # ClaudeSessionBase's subclasses) onto raw ConPTY escape bytes. "ESCAPE" becomes
    # Ctrl-C here, same as PlainTerminalBridge's own mapping -- a shell has no TUI to
    # escape out of but does have a running command to interrupt.
    _CONTROL_KEY_MAP = {
        "ESCAPE": "\x03",
        "SHIFT_TAB": "\x1b[Z",
        "UP": "\x1bOA",
        "DOWN": "\x1bOB",
        "ENTER": "\r",
    }

    def send_control_key(self, token: str):
        self.send_keys(self._CONTROL_KEY_MAP.get(token, "\r"))

    def refresh_screen(self):
        """On-demand capture, so switching modes shows the current screen
        immediately instead of waiting for the shell to next print something."""
        self._emit_screen()

    def resize(self, cols: int, rows: int):
        self.COLS, self.ROWS = cols, rows
        with self._lock:
            if self.is_alive():
                self._pty.setwinsize(rows, cols)
            self._screen.resize(lines=rows, columns=cols)

    def _read_loop(self):
        pending = False
        last_data_at = time.time()
        while self._running:
            try:
                ready, _, _ = select.select([self._pty.fileobj], [], [], self.POLL_S)
            except (OSError, ValueError):
                break
            if ready:
                try:
                    data = self._pty.read(65536)
                except EOFError:
                    logging.warning("[WIN TERMINAL] shell session ended (EOF).")
                    break
                if data:
                    self._stream.feed(data)
                    last_data_at = time.time()
                    pending = True
            now = time.time()
            if pending and (now - last_data_at) >= self.DEBOUNCE_S:
                pending = False
                self._emit_screen()
            if not self.is_alive():
                break
        self._running = False

    def _emit_screen(self):
        try:
            self._on_screen_update("\n".join(self._screen.display))
        except Exception as e:
            logging.error(f"[WIN TERMINAL] on_screen_update callback failed: {e}")

    def stop(self):
        self._running = False
        if self._pty and self.is_alive():
            try:
                self._pty.terminate(force=True)
            except Exception:
                pass
