"""
Linux tmux backend for the Claude terminal voice feature.

tmux resolves the ANSI screen itself (`capture-pane`), so unlike Windows
this needs no ConPTY/pyte terminal emulation. The session lives inside a
detached tmux session that outlives this process -- if the bridge restarts
(crash or code reload), start() reattaches to the same session instead of
spawning a new one, so an in-progress conversation survives (Phase 3).

Shared logic (busy/prompt detection, auto-answers, stall detection, sign-in
URL opening, session settings files) lives in ClaudeSessionBase.
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

from utils.clClaudeSession import ClaudeSessionBase


class ClaudeTmuxBridge(ClaudeSessionBase):
    SESSION_NAME = "jarvis-claude"
    BINARY_NAME = "claude"
    DEBOUNCE_S = 0.15  # coalesce streamed output into one screen update
    POLL_S = 0.2
    LOG_PREFIX = "TMUX BRIDGE"

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None],
                 on_stall: Optional[Callable[[str], None]] = None):
        super().__init__(cwd, on_screen_update, on_stall)
        self._fifo_path: Optional[str] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

    def _has_session(self) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.SESSION_NAME],
            capture_output=True,
        )
        return result.returncode == 0

    def _build_claude_command(self, claude_bin: str, continue_flag: bool,
                               settings_path: str, prompt_path: str) -> list:
        # `env -u` strips CLAUDE_CODE_* from the pane's process env even against an
        # already-running tmux server whose own environment predates this launch.
        strip_vars = sorted(k for k in os.environ if k.startswith("CLAUDE_CODE_"))
        cmd = ["env"]
        for var in strip_vars:
            cmd += ["-u", var]
        cmd.append(claude_bin)
        if continue_flag:
            cmd.append("--continue")
        cmd += ["--settings", settings_path, "--append-system-prompt-file", prompt_path]
        return cmd

    def start(self):
        claude_bin = shutil.which(self.BINARY_NAME)
        if not claude_bin:
            raise FileNotFoundError(
                "claude CLI not found on PATH -- install it with "
                "'npm install -g @anthropic-ai/claude-code'"
            )
        if not shutil.which("tmux"):
            raise FileNotFoundError(
                "tmux not found on PATH -- install tmux to use the Claude bridge on Linux."
            )
        config_dir = self._resolve_config_dir()
        if self._has_session():
            logging.info(f"[{self.LOG_PREFIX}] Reusing existing tmux session '{self.SESSION_NAME}'.")
        else:
            continue_flag = self._should_continue(config_dir)
            settings_path, prompt_path = self._write_session_files(config_dir)
            cmd = self._build_claude_command(claude_bin, continue_flag, settings_path, prompt_path)
            subprocess.run([
                "tmux", "new-session", "-d", "-s", self.SESSION_NAME,
                "-x", str(self.COLS), "-y", str(self.ROWS),
                "-c", self._cwd, "-e", f"CLAUDE_CONFIG_DIR={config_dir}",
            ] + cmd, check=True)
            logging.info(f"[{self.LOG_PREFIX}] claude session started in tmux.")
        self._running = True
        self._start_pipe_reader()

    def is_alive(self) -> bool:
        return self._has_session()

    def write(self, text: str):
        if not self.is_alive():
            raise RuntimeError("tmux session is not alive")
        # Two sends with a gap, not "text\n": the Ink TUI reads discrete keypresses and can drop a burst.
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME, "-l", text], check=True)
        time.sleep(0.08)
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME, "Enter"], check=True)

    def send_keys(self, keys: str):
        """Space-separated tmux key names (e.g. 'Down Enter', 'Escape') -- tmux
        resolves application-cursor-mode itself, so no raw escape bytes here."""
        if not self.is_alive():
            raise RuntimeError("tmux session is not alive")
        subprocess.run(["tmux", "send-keys", "-t", self.SESSION_NAME] + keys.split(), check=True)

    def _send_control_keys(self, token: str):
        self.send_keys(token.title())

    def _start_pipe_reader(self):
        fd, self._fifo_path = tempfile.mkstemp(prefix="jarvis_claude_", suffix=".fifo")
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
        try:
            while self._running:
                try:
                    ready, _, _ = select.select([fifo_fd], [], [], self.POLL_S)
                except (OSError, ValueError):
                    break
                if ready:
                    data = os.read(fifo_fd, 65536)
                    if data:
                        self._last_data_at = time.time()
                        self._stall_reported = False
                        pending = True
                    else:
                        # Writer end closed (session died); reopening lets a future writer be seen.
                        # stop() deleting the FIFO can race this exact moment, so a missing file
                        # here just means teardown is in progress -- not a crash.
                        os.close(fifo_fd)
                        if not self._running:
                            break
                        time.sleep(self.POLL_S)
                        try:
                            fifo_fd = os.open(self._fifo_path, os.O_RDONLY | os.O_NONBLOCK)
                        except OSError:
                            break
                now = time.time()
                if pending and (now - self._last_data_at) >= self.DEBOUNCE_S:
                    pending = False
                    self._emit_screen()
                self._retry_dropped_answer(now)
                self._check_stall(now)
                if not self.is_alive():
                    break
        finally:
            try:
                os.close(fifo_fd)
            except OSError:
                pass
        self._running = False

    def refresh_screen(self):
        """On-demand capture, so switching back to this session from another
        mode shows its current screen immediately instead of waiting for new output."""
        self._emit_screen()

    def _emit_screen(self):
        # -J joins wrapped lines, which also makes the wrapped sign-in URL trivial to extract.
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", self.SESSION_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            self.process_screen(result.stdout.rstrip("\n"))

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
