"""
Shared logic for the bridged Claude terminal session, independent of how a
backend actually talks to the child process (Windows ConPTY vs. Linux tmux).

Subclasses (ClaudePtyBridge, ClaudeTmuxBridge) own only: spawn, send text
(write), send raw keys (send_keys), obtaining/resolving screen text, alive
check, and stop. Everything else -- busy/prompt detection, first-run
auto-answers and their retry, stall detection, sign-in URL opening, session
settings files, and config-dir/--continue logic -- lives here.
"""
import json
import logging
import os
import re
import sys
import time
import webbrowser
from typing import Callable, Optional


class ClaudeSessionBase:
    COLS = 120
    ROWS = 40
    STALL_S = 60.0  # a working session redraws its spinner constantly, so this much silence while busy means hung
    READY_QUIET_S = 1.0
    LOG_PREFIX = "CLAUDE SESSION"

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None],
                 on_stall: Optional[Callable[[str], None]] = None):
        self._cwd = cwd
        self._on_screen_update = on_screen_update
        self._on_stall = on_stall
        self._busy = False
        self._prompt_visible = False
        self._stall_reported = False
        self._last_data_at = time.time()
        self._last_screen_text = ""
        self._last_opened_url: Optional[str] = None
        self._answered_screen: Optional[str] = None
        self._answer_keys = ""
        self._answer_tries = 0
        self._answered_at = 0.0

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
    def _strip_claude_code_env(env: dict) -> dict:
        # Inherited CLAUDE_CODE_* markers make this look like a nested session and disable transcript saving.
        return {k: v for k, v in env.items() if not k.startswith("CLAUDE_CODE_")}

    def _resolve_config_dir(self) -> str:
        # Own config dir: sharing ~/.claude with a live session hangs login (claude-code #91987, #63007).
        config_dir = os.path.join(self._cwd, "data", "claude_bridge_config")
        os.makedirs(config_dir, exist_ok=True)
        return config_dir

    @staticmethod
    def _should_continue(config_dir: str) -> bool:
        # --continue quits immediately ("No conversation found") until projects/ exists.
        return os.path.isdir(os.path.join(config_dir, "projects"))

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

    def process_screen(self, screen_text: str):
        """Feed a fully-resolved screen (backend already turned raw bytes/pane
        content into plain rows) through the shared busy/prompt/auto-answer/
        sign-in logic, then hand it to the on_screen_update callback."""
        rows = screen_text.split("\n")
        self._busy = "esc to interrupt" in screen_text
        self._prompt_visible = self._prompt_row_visible(rows)
        self._last_screen_text = screen_text
        self._maybe_auto_answer(screen_text)
        self._maybe_open_signin_url(screen_text)
        try:
            self._on_screen_update(screen_text)
        except Exception as e:
            logging.error(f"[{self.LOG_PREFIX}] on_screen_update callback failed: {e}")

    # First-run screens that are just keypresses -- everything except the browser sign-in itself.
    # The trust menu defaults to "No, exit", so it needs down-arrow + enter; the theme and
    # login-method menus already highlight the right option (Dark / subscription). Tokens are
    # backend-neutral; each backend's _send_control_keys() maps them to its own key encoding
    # (Windows: escape bytes for application-cursor-mode; tmux: native key names).
    AUTO_ANSWERS = [
        ("Is this a project you created or one you trust?", "DOWN ENTER"),
        ("Choose the text style", "ENTER"),
        ("Select login method:", "ENTER"),
        ("Press Enter to continue", "ENTER"),
    ]
    ANSWER_RETRY_S = 3.0
    ANSWER_MAX_TRIES = 3

    def _maybe_auto_answer(self, screen_text: str):
        for marker, token in self.AUTO_ANSWERS:
            if marker not in screen_text:
                continue
            if screen_text == self._answered_screen:
                return
            self._answered_screen = screen_text
            self._answer_keys = token
            self._answer_tries = 1
            self._answered_at = time.time()
            self._send_auto_answer(marker)
            return
        self._answered_screen = None

    def _send_auto_answer(self, marker: str):
        try:
            self._send_control_keys(self._answer_keys)
            logging.info(f"[{self.LOG_PREFIX}] Auto-answered first-run screen: '{marker}'")
        except Exception as e:
            logging.error(f"[{self.LOG_PREFIX}] Failed to answer '{marker}': {e}")

    def _send_control_keys(self, token: str):
        """Translate a backend-neutral token ('DOWN ENTER', 'ENTER', 'ESCAPE',
        'SHIFT_TAB', 'UP', 'DOWN') into this backend's own key encoding and
        send it. Backend-specific."""
        raise NotImplementedError

    def send_control_key(self, token: str):
        """Public entry point for a widget/UI-driven key press (Esc, Shift+Tab,
        arrow keys) -- goes through the same backend-neutral token mapping as
        the internal AUTO_ANSWERS handling."""
        self._send_control_keys(token)

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
                logging.info(f"[{self.LOG_PREFIX}] Opened sign-in URL in the default browser: {url}")
            except Exception as e:
                logging.error(f"[{self.LOG_PREFIX}] Failed to open sign-in URL: {e}")

    def _check_stall(self, now: float):
        if (self._busy and not self._stall_reported and self._on_stall
                and now - self._last_data_at >= self.STALL_S):
            self._stall_reported = True
            self._report_stall()

    def _report_stall(self):
        tail = "\n".join(r.rstrip() for r in self._last_screen_text.split("\n") if r.strip())
        logging.warning(f"[{self.LOG_PREFIX}] No output for {self.STALL_S:.0f}s while busy -- reporting stall.")
        try:
            self._on_stall(tail)
        except Exception as e:
            logging.error(f"[{self.LOG_PREFIX}] on_stall callback failed: {e}")
