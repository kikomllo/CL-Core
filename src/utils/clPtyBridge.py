"""
Windows-only pseudo-console bridge for the Claude terminal voice feature.

ConPTY (via pywinpty) gives raw ANSI byte output, not a resolved screen --
unlike tmux's `capture-pane` on Linux, which does that resolution for free.
pyte (a small terminal emulator library) fills that gap here.

Linux uses tmux shell-outs instead (see clClaudeBridge.py); this module is
Windows-only, imported lazily so it never loads on Linux.
"""
import json
import os
import re
import sys
import shutil
import select
import threading
import time
import logging
import webbrowser
from typing import Callable, Optional

import pyte


class ClaudePtyBridge:
    COLS = 120
    ROWS = 40
    DEBOUNCE_S = 0.15  # coalesce streamed output into one screen update
    POLL_S = 0.2
    STALL_S = 60.0  # a working session redraws its spinner constantly, so this much silence while busy means hung
    READY_QUIET_S = 1.0

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None],
                 on_stall: Optional[Callable[[str], None]] = None):
        self._cwd = cwd
        self._on_screen_update = on_screen_update
        self._on_stall = on_stall
        self._busy = False
        self._prompt_visible = False
        self._stall_reported = False
        self._last_data_at = time.time()
        self._screen = pyte.Screen(self.COLS, self.ROWS)
        self._stream = pyte.Stream(self._screen)
        self._pty = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_opened_url: Optional[str] = None
        self._answered_screen: Optional[str] = None
        self._answer_keys = ""
        self._answer_tries = 0
        self._answered_at = 0.0

    @staticmethod
    def _extract_wrapped_url(screen_text: str, cols: int) -> Optional[str]:
        """The CLI wraps a long URL across rows with no separator: a row filling the full width continues on the next."""
        lines = screen_text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            match = re.search(r"https?://\S*", stripped)
            if not match:
                continue
            url = match.group(0)
            j = i
            while len(lines[j].rstrip()) >= cols and j + 1 < len(lines):
                cont = lines[j + 1].rstrip()
                if not cont:
                    break
                url += cont
                j += 1
            return url
        return None

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

    SESSION_PROMPT = (
        "This session is voice-driven: the host application reads every final answer aloud "
        "automatically. Do NOT run the speak/TTS command from the project's standing instruction. "
        "Just answer in plain, short, speakable sentences and put anything long in the terminal."
    )

    def _write_session_files(self, config_dir: str):
        """Per-launch settings (Stop hook -> MQTT) and prompt, passed via CLI flags so they
        only ever apply to this bridged session, never a developer's own claude sessions."""
        hook_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clClaudeStopHook.py")
        python = sys.executable.replace("\\", "/")
        script = hook_script.replace("\\", "/")
        settings = {"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": python, "args": [script]}]}],
            # Deterministic backstop for SESSION_PROMPT: the model sometimes still runs the
            # project's speak command itself, which would double every answer.
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": python, "args": [script, "--block-speak"]}
            ]}],
        }}
        settings_path = os.path.join(config_dir, "bridge_settings.json")
        prompt_path = os.path.join(config_dir, "bridge_prompt.txt")
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(self.SESSION_PROMPT)
        return settings_path, prompt_path

    def start(self):
        import winpty
        binary = self._resolve_claude_binary()
        # Inherited CLAUDE_CODE_* markers make this look like a nested session and disable transcript saving.
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
        # Own config dir: sharing ~/.claude with a live session hangs login (claude-code #91987, #63007).
        config_dir = os.path.join(self._cwd, "data", "claude_bridge_config")
        os.makedirs(config_dir, exist_ok=True)
        child_env["CLAUDE_CONFIG_DIR"] = config_dir
        # --continue quits immediately ("No conversation found") until projects/ exists.
        args = [binary]
        if os.path.isdir(os.path.join(config_dir, "projects")):
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

    def write(self, text: str):
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("PTY session is not alive")
            # Two writes with a gap, not "text\r": the Ink TUI reads discrete keypresses and can drop a burst.
            self._pty.write(text)
            time.sleep(0.08)
            self._pty.write("\r")

    def is_ready(self) -> bool:
        """At the idle input box (not a menu, not mid-turn) and quiet."""
        return (self._prompt_visible and not self._busy
                and time.time() - self._last_data_at >= self.READY_QUIET_S)

    @staticmethod
    def _prompt_row_visible(rows) -> bool:
        # The chat input box is a ❯ row between two horizontal rules; menus also use ❯ but have no rules.
        for i in range(1, len(rows) - 1):
            if (rows[i].lstrip().startswith("❯")
                    and rows[i - 1].startswith("─") and rows[i + 1].startswith("─")):
                return True
        return False

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
            if (self._busy and not self._stall_reported and self._on_stall
                    and now - self._last_data_at >= self.STALL_S):
                self._stall_reported = True
                self._report_stall()
            if not self.is_alive():
                break
        self._running = False

    def _report_stall(self):
        tail = "\n".join(r.rstrip() for r in self._screen.display if r.strip())
        logging.warning(f"[PTY BRIDGE] No output for {self.STALL_S:.0f}s while busy -- reporting stall.")
        try:
            self._on_stall(tail)
        except Exception as e:
            logging.error(f"[PTY BRIDGE] on_stall callback failed: {e}")

    def _emit_screen(self):
        rows = self._screen.display
        screen_text = "\n".join(rows)
        self._busy = "esc to interrupt" in screen_text
        self._prompt_visible = self._prompt_row_visible(rows)
        self._maybe_auto_answer(screen_text)
        self._maybe_open_signin_url(screen_text)
        try:
            self._on_screen_update(screen_text)
        except Exception as e:
            logging.error(f"[PTY BRIDGE] on_screen_update callback failed: {e}")

    # First-run screens that are just keypresses -- everything except the browser sign-in itself.
    # The trust menu defaults to "No, exit", so it needs down-arrow (application mode) + Enter;
    # the theme and login-method menus already highlight the right option (Dark / subscription).
    AUTO_ANSWERS = [
        ("Is this a project you created or one you trust?", "\x1bOB\r"),
        ("Choose the text style", "\r"),
        ("Select login method:", "\r"),
        ("Press Enter to continue", "\r"),
    ]
    ANSWER_RETRY_S = 3.0
    ANSWER_MAX_TRIES = 3

    def _maybe_auto_answer(self, screen_text: str):
        for marker, keys in self.AUTO_ANSWERS:
            if marker not in screen_text:
                continue
            if screen_text == self._answered_screen:
                return
            self._answered_screen = screen_text
            self._answer_keys = keys
            self._answer_tries = 1
            self._answered_at = time.time()
            self._send_auto_answer(marker)
            return
        self._answered_screen = None

    def _send_auto_answer(self, marker: str):
        try:
            self.send_keys(self._answer_keys)
            logging.info(f"[PTY BRIDGE] Auto-answered first-run screen: '{marker}'")
        except Exception as e:
            logging.error(f"[PTY BRIDGE] Failed to answer '{marker}': {e}")

    def _retry_dropped_answer(self, now: float):
        # An Enter sent while the TUI was still redrawing can be dropped: same screen, no new output.
        if (self._answered_screen is None or self._answer_tries >= self.ANSWER_MAX_TRIES
                or now - self._answered_at < self.ANSWER_RETRY_S
                or self._last_data_at > self._answered_at):
            return
        self._answer_tries += 1
        self._answered_at = now
        self._send_auto_answer("(retry)")

    def _maybe_open_signin_url(self, screen_text: str):
        # Only the CLI's own fallback phrasing -- never open arbitrary URLs a real answer might print.
        if "Browser didn't open" not in screen_text:
            return
        # One tab per session: the CLI regenerates its URL mid sign-in, which used to open a second tab.
        if self._last_opened_url is not None:
            return
        url = self._extract_wrapped_url(screen_text, self.COLS)
        if url:
            self._last_opened_url = url
            try:
                webbrowser.open(url, new=2)
                logging.info(f"[PTY BRIDGE] Opened sign-in URL in the default browser: {url}")
            except Exception as e:
                logging.error(f"[PTY BRIDGE] Failed to open sign-in URL: {e}")

    def stop(self):
        self._running = False
        if self._pty and self.is_alive():
            try:
                self._pty.terminate(force=True)
            except Exception:
                pass
