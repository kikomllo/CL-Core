"""
Windows-only pseudo-console bridge for the Claude terminal voice feature.

ConPTY (via pywinpty) gives raw ANSI byte output, not a resolved screen --
unlike tmux's `capture-pane` on Linux, which does that resolution for free.
pyte (a small terminal emulator library) fills that gap here.

Linux uses tmux shell-outs instead (see clClaudeBridge.py); this module is
Windows-only, imported lazily so it never loads on Linux.
"""
import os
import re
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
    # Coalesce a burst of rapidly-streamed output into one screen update
    # instead of publishing on every few bytes -- see prefer-performance
    # memory: don't do continuous work when nothing has actually settled.
    DEBOUNCE_S = 0.15
    POLL_S = 0.2

    def __init__(self, cwd: str, on_screen_update: Callable[[str], None]):
        self._cwd = cwd
        self._on_screen_update = on_screen_update
        self._screen = pyte.Screen(self.COLS, self.ROWS)
        self._stream = pyte.Stream(self._screen)
        self._pty = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_opened_url: Optional[str] = None
        self._trust_prompt_answered = False

    @staticmethod
    def _extract_wrapped_url(screen_text: str, cols: int) -> Optional[str]:
        """Claude Code's CLI wraps a long sign-in URL across several
        terminal-width rows with no separator -- naively grabbing one
        rendered line truncates it mid-parameter. A row that fills the full
        column width got cut off by wrapping and continues on the next; a
        row that ends short (before its padding) is the real end of the URL."""
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
        """The npm-installed `claude`/`claude.cmd` shim can't be spawned
        directly under ConPTY -- Windows batch-file argument passing doesn't
        survive CreateProcess the way it does through cmd.exe, so args like
        --continue get silently dropped. Go straight to the native binary
        the shim wraps instead (verified via a spike test)."""
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
        # Strip any CLAUDE_CODE_* markers inherited from whatever shell
        # launched the ecosystem (e.g. this repo's own Claude Code dev
        # session) -- otherwise this spawn looks like a nested child
        # session to the CLI and silently disables transcript saving,
        # which --continue itself depends on.
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
        # NOT setting CLAUDE_CODE_OAUTH_TOKEN here -- it's documented for
        # headless `-p` calls only, and its presence appears to make this
        # interactive session always re-attempt browser auth instead of
        # using the saved credential file in CLAUDE_CONFIG_DIR (observed:
        # a fresh launch redid the full sign-in flow despite valid,
        # just-written credentials already sitting in that directory).
        # Isolated from the default ~/.claude/ -- sharing that directory
        # with whatever interactive `claude` session a developer happens to
        # have open (e.g. one editing this very repo) triggers a documented
        # lock-contention hang during login (github.com/anthropics/claude-code
        # issues #91987, #63007). A dedicated config dir means this process
        # never touches the same files at all, not just "usually doesn't."
        config_dir = os.path.join(self._cwd, "data", "claude_bridge_config")
        os.makedirs(config_dir, exist_ok=True)
        child_env["CLAUDE_CONFIG_DIR"] = config_dir
        # --continue errors out and quits immediately if there's no prior
        # conversation recorded yet under this config dir (its `projects/`
        # dir doesn't exist until a first message has actually gone
        # through) -- only request it once one genuinely exists.
        args = [binary]
        if os.path.isdir(os.path.join(config_dir, "projects")):
            args.append("--continue")
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
        """Writes raw bytes with no appended CR -- for arrow-key menu
        navigation (e.g. '\\x1b[B' + '\\r'), unlike write() which always
        types a line of text and submits it."""
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("PTY session is not alive")
            self._pty.write(raw)

    def write(self, text: str):
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("PTY session is not alive")
            # Sent as two distinct writes with a real gap, not one combined
            # "text\r" buffer -- some Node/Ink-based TUIs read input as
            # discrete keypress events and may not reliably parse a burst
            # written in a single OS-level write() the way a human's actual
            # keystroke timing would arrive.
            self._pty.write(text)
            time.sleep(0.08)
            self._pty.write("\r")

    def _read_loop(self):
        last_data_at = 0.0
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
                    last_data_at = time.time()
                    pending = True
            if pending and (time.time() - last_data_at) >= self.DEBOUNCE_S:
                pending = False
                self._emit_screen()
            if not self.is_alive():
                break
        self._running = False

    def _emit_screen(self):
        screen_text = "\n".join(self._screen.display)
        self._maybe_answer_trust_prompt(screen_text)
        self._maybe_open_signin_url(screen_text)
        try:
            self._on_screen_update(screen_text)
        except Exception as e:
            logging.error(f"[PTY BRIDGE] on_screen_update callback failed: {e}")

    def _maybe_answer_trust_prompt(self, screen_text: str):
        # First launch against a fresh CLAUDE_CONFIG_DIR always asks to trust
        # this workspace before anything else -- a menu (down arrow + enter),
        # not text, and the default highlighted option is "No, exit".
        if self._trust_prompt_answered:
            return
        if "Is this a project you created or one you trust?" not in screen_text:
            return
        self._trust_prompt_answered = True
        try:
            self.send_keys("\x1bOB\r")
            logging.info("[PTY BRIDGE] Auto-confirmed workspace trust prompt.")
        except Exception as e:
            logging.error(f"[PTY BRIDGE] Failed to confirm trust prompt: {e}")

    def _maybe_open_signin_url(self, screen_text: str):
        # Scoped specifically to Claude Code's own "browser didn't open"
        # fallback phrasing -- not a generic "open any URL on screen"
        # rule, which would hijack ordinary links a real answer might print.
        if "Browser didn't open" not in screen_text:
            return
        # At most one auto-opened tab per session, not one per distinct URL
        # -- the CLI appears to regenerate its PKCE challenge/URL internally
        # partway through a single sign-in attempt, which previously opened
        # a second tab for what a human would just treat as the same flow.
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
