"""
Windows-only pseudo-console backend for the Claude terminal voice feature.

ConPTY (via pywinpty) gives raw ANSI byte output, not a resolved screen --
unlike tmux's `capture-pane` on Linux, which does that resolution for free.
pyte (a small terminal emulator library) fills that gap here.

Linux uses ClaudeTmuxBridge instead (see clClaudeTmuxBridge.py); this module
is Windows-only, imported lazily so it never loads on Linux. Shared logic
(busy/prompt detection, auto-answers, stall detection, sign-in URL opening,
session settings files) lives in ClaudeSessionBase.
"""
import os
import shutil
import select
import threading
import time
import logging
from typing import Callable, Optional

import pyte

from utils.clClaudeSession import ClaudeSessionBase


class ClaudePtyBridge(ClaudeSessionBase):
    DEBOUNCE_S = 0.15  # coalesce streamed output into one screen update
    POLL_S = 0.2
    LOG_PREFIX = "PTY BRIDGE"

    # Backend-neutral AUTO_ANSWERS tokens -> ConPTY key encoding (down arrow in
    # application-cursor mode, then carriage return; plain carriage return).
    _CONTROL_KEY_MAP = {
        "DOWN ENTER": "\x1bOB\r",
        "ENTER": "\r",
        "ESCAPE": "\x1b",
        "SHIFT_TAB": "\x1b[Z",
        "UP": "\x1bOA",
        "DOWN": "\x1bOB",
    }

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None],
                 on_stall: Optional[Callable[[str], None]] = None,
                 cols: Optional[int] = None, rows: Optional[int] = None):
        super().__init__(cwd, on_screen_update, on_stall, cols, rows)
        self._screen = pyte.Screen(self.COLS, self.ROWS)
        self._stream = pyte.Stream(self._screen)
        self._pty = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    @staticmethod
    def _resolve_claude_binary() -> str:
        """Spawn the native claude.exe, not the .cmd shim: batch-file args (--continue) get dropped under ConPTY."""
        shim = shutil.which("claude.cmd") or shutil.which("claude")
        if not shim:
            raise FileNotFoundError(
                "claude CLI not found on PATH -- install it with "
                "'npm install -g @anthropic-ai/claude-code'"
            )
        shim_dir = os.path.dirname(shim)
        candidate = os.path.join(
            shim_dir, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"
        )
        if os.path.exists(candidate):
            return candidate
        raise FileNotFoundError(
            f"Found claude shim at {shim} but not its native binary at {candidate}"
        )

    def start(self):
        import winpty
        binary = self._resolve_claude_binary()
        child_env = self._strip_claude_code_env(os.environ)
        config_dir = self._resolve_config_dir()
        child_env["CLAUDE_CONFIG_DIR"] = config_dir
        args = [binary]
        if self._should_continue(config_dir):
            args.append("--continue")
        settings_path, prompt_path = self._write_session_files(config_dir)
        args += ["--settings", settings_path, "--append-system-prompt-file", prompt_path]
        with self._lock:
            self._pty = winpty.PtyProcess.spawn(
                args,
                cwd=self._cwd,
                env=child_env,
                dimensions=(self.ROWS, self.COLS),
                # Default backend silently hangs on reads on this stack --
                # verified via spike test; ConPTY (1) is the one that works.
                backend=1,
            )
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        logging.info("[PTY BRIDGE] claude session started.")

    def is_alive(self) -> bool:
        return bool(self._pty and self._pty.isalive())

    def send_keys(self, raw: str):
        """Raw bytes, no appended CR -- for menu navigation; write() types a line and submits it."""
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("PTY session is not alive")
            self._pty.write(raw)

    def _send_control_keys(self, token: str):
        self.send_keys(self._CONTROL_KEY_MAP.get(token, "\r"))

    def write(self, text: str):
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("PTY session is not alive")
            # Two writes with a gap, not "text\r": the Ink TUI reads discrete keypresses and can drop a burst.
            self._pty.write(text)
            time.sleep(0.08)
            self._pty.write("\r")

    def _read_loop(self):
        pending = False
        while self._running:
            try:
                ready, _, _ = select.select([self._pty.fileobj], [], [], self.POLL_S)
            except (OSError, ValueError):
                break
            if ready:
                try:
                    data = self._pty.read(65536)
                except EOFError:
                    logging.warning("[PTY BRIDGE] claude session ended (EOF).")
                    break
                if data:
                    self._stream.feed(data)
                    self._last_data_at = time.time()
                    self._stall_reported = False
                    pending = True
            now = time.time()
            if pending and (now - self._last_data_at) >= self.DEBOUNCE_S:
                pending = False
                self._emit_screen()
            self._retry_dropped_answer(now)
            self._check_stall(now)
            if not self.is_alive():
                break
        self._running = False

    def refresh_screen(self):
        """On-demand capture, so switching back to this session from another
        mode shows its current screen immediately instead of waiting for new output."""
        self._emit_screen()

    def _apply_resize(self, cols: int, rows: int):
        with self._lock:
            if self.is_alive():
                self._pty.setwinsize(rows, cols)
            self._screen.resize(lines=rows, columns=cols)

    def _emit_screen(self):
        self.process_screen("\n".join(self._screen.display))

    def stop(self):
        self._running = False
        if self._pty and self.is_alive():
            try:
                self._pty.terminate(force=True)
            except Exception:
                pass
