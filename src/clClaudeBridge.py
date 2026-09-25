import asyncio
import json
import logging
import os
import sys
import platform
import re
import time

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('CLAUDE_BRIDGE')

if sys.platform == 'win32':
    # ProactorEventLoop (Windows asyncio default) doesn't support the
    # add_reader/add_writer calls aiomqtt needs -- same fix every other
    # async service in this codebase already applies.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt

CURRENT_OS = platform.system()
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

MAX_SPOKEN_CHARS = 800


def speakable_text(raw: str) -> str:
    """Turns a markdown answer into something worth reading aloud: code, tables
    and URLs stay on screen, formatting marks are dropped, length is capped."""
    text = re.sub(r"```.*?```", " ", raw, flags=re.S)
    had_code = text != raw
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("|"))
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "The details are on screen." if had_code else ""
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[:end + 1] if end > MAX_SPOKEN_CHARS // 2 else cut[:cut.rfind(" ")]
    return text


def _backend_class():
    """Windows uses ConPTY via pyte (ClaudePtyBridge); Linux uses tmux, which
    resolves the ANSI screen itself and needs no terminal-emulation library."""
    if CURRENT_OS == "Windows":
        from utils.clPtyBridge import ClaudePtyBridge
        return ClaudePtyBridge
    from utils.clClaudeTmuxBridge import ClaudeTmuxBridge
    return ClaudeTmuxBridge


def _terminal_backend_class():
    """Backs the widget's "/terminal" mode. Linux-only for now (tmux running a
    plain shell) -- same Windows/Linux split as _backend_class(), just not yet
    built for Windows (ConPTY running cmd.exe/powershell.exe would be the
    equivalent there)."""
    if CURRENT_OS != "Linux":
        raise NotImplementedError(
            "/terminal isn't implemented on this OS yet (tmux-backed, Linux-only for now)."
        )
    from utils.clPlainTerminalBridge import PlainTerminalBridge
    return PlainTerminalBridge


class ClaudeBridgeService:
    """Routes 'ask claude ...' voice commands (already resolved to plain
    text on jarvis/claude/question by clDaemon.py) into a real, persistent
    `claude` terminal session, and mirrors that session's screen back out
    over MQTT for a dashboard widget to display.
    """

    def __init__(self):
        self.bridge = None
        self.terminal_bridge = None
        self.mode = "claude"  # or "terminal" -- see "/terminal" and "/claude" in run()'s message loop
        self.loop = None
        self.mqtt_client = None
        self._last_claude_screen = ""
        self._last_terminal_screen = ""
        self._last_published_screen = None
        self._last_question = None
        self._recovering = False
        self._answered = False

    @staticmethod
    def _trim_screen(screen_text: str) -> str:
        """Drops the fixed-grid padding: trailing spaces per row and blank rows at the bottom."""
        return "\n".join(row.rstrip() for row in screen_text.split("\n")).rstrip("\n")

    def _on_claude_screen_update(self, screen_text: str):
        # Called from the bridge's background reader thread -- hop onto the
        # asyncio loop rather than touching the MQTT client from off-thread.
        # Cached even while not the active mode, so switching back to "claude"
        # can republish its latest screen without waiting for new output.
        screen_text = self._trim_screen(screen_text)
        self._last_claude_screen = screen_text
        if self.mode == "claude":
            self._publish_if_active(screen_text)

    def _on_terminal_screen_update(self, screen_text: str):
        screen_text = self._trim_screen(screen_text)
        self._last_terminal_screen = screen_text
        if self.mode == "terminal":
            self._publish_if_active(screen_text)

    def _publish_if_active(self, screen_text: str):
        if screen_text == self._last_published_screen:
            return
        self._last_published_screen = screen_text
        if self.loop and self.mqtt_client:
            asyncio.run_coroutine_threadsafe(self._publish_screen(screen_text), self.loop)

    async def _publish_screen(self, screen_text: str):
        try:
            # Retained so a widget (or anything else) that subscribes after
            # the last change still sees the current screen immediately,
            # instead of a blank view until the next update happens to fire.
            await self.mqtt_client.publish(
                "jarvis/claude/screen", json.dumps({"text": screen_text}, ensure_ascii=False), retain=True
            )
        except Exception as e:
            logging.error(f"[CLAUDE BRIDGE] Failed to publish screen update: {e}")

    def _ensure_pty_started(self, force_fresh: bool = False) -> bool:
        """Returns True only if a new session was just started. force_fresh kills any
        existing session first -- used only for the very first start of a not-yet-signed-in
        service, so a previous aborted attempt's stale session (e.g. stuck on an expired
        OAuth prompt) never gets silently reattached to instead of starting clean."""
        if self.bridge and self.bridge.is_alive():
            return False
        backend_cls = _backend_class()
        self.bridge = backend_cls(
            cwd=REPO_ROOT, on_screen_update=self._on_claude_screen_update, on_stall=self._on_stall
        )
        if force_fresh:
            self.bridge.stop()
        self.bridge.start()
        return True

    def _ensure_terminal_started(self) -> bool:
        """Returns True only if a new terminal session was just started."""
        if self.terminal_bridge and self.terminal_bridge.is_alive():
            return False
        backend_cls = _terminal_backend_class()
        self.terminal_bridge = backend_cls(cwd=REPO_ROOT, on_screen_update=self._on_terminal_screen_update)
        self.terminal_bridge.start()
        return True

    async def _wait_until_ready(self, timeout: float = 30.0):
        # Input written while the TUI is still starting up gets dropped.
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.bridge.is_ready():
                return
            await asyncio.sleep(0.25)
        logging.warning("[CLAUDE BRIDGE] Session never reached an idle prompt; sending anyway.")

    def _on_stall(self, screen_tail: str):
        # Runs on the PTY reader thread.
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._recover_from_stall(screen_tail), self.loop)

    @staticmethod
    def _dump_stall(screen_tail: str) -> str:
        """Latest stall's screen goes to a file, not the log -- it's 25+ lines of TUI art."""
        path = os.path.join(REPO_ROOT, "logs", "claude_stall.txt")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{screen_tail}\n")
        except OSError as e:
            logging.error(f"[CLAUDE BRIDGE] Could not save stall screen: {e}")
        return path

    async def _recover_from_stall(self, screen_tail: str):
        if self._recovering:
            return
        self._recovering = True
        try:
            dump_path = self._dump_stall(screen_tail)
            logging.error(
                f"[CLAUDE BRIDGE] Session stalled, restarting (last question: {self._last_question!r}; "
                f"screen saved to {dump_path})"
            )
            if self.bridge:
                self.bridge.stop()
            question, self._last_question = self._last_question, None  # retried at most once
            if self._answered:
                question = None  # the reply already went out; re-sending would answer twice
            if not self._ensure_pty_started():
                return
            await self._wait_until_ready()
            if question:
                self.bridge.write(question)
                logging.info(f"[CLAUDE BRIDGE] Re-sent after restart: '{question}'")
        except Exception as e:
            logging.error(f"[CLAUDE BRIDGE] Stall recovery failed: {e}")
        finally:
            self._recovering = False

    async def _speak_reply(self, raw_text: str):
        self._answered = True
        spoken = speakable_text(raw_text)
        if not spoken:
            return
        # A trailing question re-opens the mic (daemon's claude_reply context) so the
        # user can just answer. Deliberately not ignore_silent: silent mode mutes this too.
        payload = {"text": spoken, "claude_session": True, "request_reply": spoken.endswith("?")}
        try:
            await self.mqtt_client.publish("jarvis/sys/speak", json.dumps(payload))
        except Exception as e:
            logging.error(f"[CLAUDE BRIDGE] Failed to publish speech: {e}")

    async def run(self) -> None:
        # A saved sign-in isn't a hard prerequisite anymore: the dashboard's Claude widget
        # mirrors whatever screen the session is on (including the trust/theme/login/sign-in
        # screens) and relays typed input back in, so a not-yet-signed-in bridge still starts
        # up -- the browser opens automatically and the resulting code gets pasted into the
        # widget instead of a terminal. force_fresh on the first start only avoids silently
        # reattaching to a stale session a previous aborted attempt left behind.
        creds_path = os.path.join(REPO_ROOT, "data", "claude_bridge_config", ".credentials.json")
        needs_sign_in = not os.path.exists(creds_path)
        if needs_sign_in:
            logging.info(
                "[CLAUDE BRIDGE] No saved login yet -- starting a fresh sign-in session. "
                "A browser tab will open automatically; paste the resulting code into the "
                "Claude dashboard widget."
            )

        logging.info("[CLAUDE BRIDGE] Online. Connecting to MQTT broker...")
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    self.mqtt_client = client
                    self.loop = asyncio.get_running_loop()
                    self._ensure_pty_started(force_fresh=needs_sign_in)
                    needs_sign_in = False  # only force a fresh session on the very first start

                    await client.subscribe("jarvis/claude/question")
                    await client.subscribe("jarvis/claude/reply")
                    # NATIVE_SERVICES lists this module as "Claude Bridge" (clJarvis.py), which is
                    # what EXPECTED_MODULES matches against -- without this, the supervisor's
                    # "ALL SYSTEMS GO" announcement (and jarvis/sys/ecosystem_online, which
                    # clReminderTrigger.py waits on) never fires, stuck one module short forever.
                    await client.publish("jarvis/sys/module_ready", json.dumps({"module": "claude bridge"}))
                    logging.info("[CLAUDE BRIDGE] Subscribed. Ready.")

                    async for message in client.messages:
                        topic = message.topic.value
                        if topic == "jarvis/claude/reply":
                            try:
                                await self._speak_reply(str(json.loads(message.payload.decode()).get("text", "")))
                            except json.JSONDecodeError:
                                pass
                            continue
                        if topic == "jarvis/claude/question":
                            try:
                                payload = json.loads(message.payload.decode())
                            except json.JSONDecodeError:
                                continue
                            keys = payload.get("keys")
                            text = str(payload.get("text", "")).strip()
                            if not keys and not text:
                                continue

                            # "/terminal" and "/claude" are local mode switches, handled here rather
                            # than ever being typed into either session -- the widget's input doubles
                            # as a plain shell whenever "/terminal" is the active mode.
                            if not keys and text.lower() == "/terminal":
                                try:
                                    self.mode = "terminal"
                                    self._ensure_terminal_started()
                                    self.terminal_bridge.refresh_screen()
                                    logging.info("[CLAUDE BRIDGE] Switched to terminal mode.")
                                except Exception as e:
                                    logging.error(f"[CLAUDE BRIDGE] Could not start terminal mode: {e}")
                                    self.mode = "claude"
                                continue
                            if not keys and text.lower() == "/claude":
                                self.mode = "claude"
                                try:
                                    if self._ensure_pty_started():
                                        await self._wait_until_ready()
                                    else:
                                        self.bridge.refresh_screen()
                                except Exception as e:
                                    logging.error(f"[CLAUDE BRIDGE] Could not resume claude mode: {e}")
                                logging.info("[CLAUDE BRIDGE] Switched to claude mode.")
                                continue

                            try:
                                if self.mode == "terminal":
                                    self._ensure_terminal_started()
                                    active_bridge = self.terminal_bridge
                                else:
                                    if self._ensure_pty_started():
                                        await self._wait_until_ready()
                                    active_bridge = self.bridge

                                if keys:
                                    active_bridge.send_keys(keys)
                                    logging.info(f"[CLAUDE BRIDGE] Sent raw keys: {keys!r}")
                                else:
                                    if self.mode == "claude":
                                        self._last_question = text
                                        self._answered = False
                                    active_bridge.write(text)
                                    logging.info(f"[CLAUDE BRIDGE] Injected ({self.mode}): '{text}'")
                            except Exception as e:
                                logging.error(f"[CLAUDE BRIDGE] Failed to inject input: {e}")

            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"MQTT Connection Error: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"Unexpected bridge error: {type(e).__name__}: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)


def run_setup(timeout_s: float = 900.0) -> bool:
    """One-time sign-in. Every first-run screen is auto-answered by the backend; the only
    human step is clicking Authorize in the browser (and pasting a code if the page shows one)."""
    backend_cls = _backend_class()
    latest = {"text": ""}
    bridge = backend_cls(cwd=REPO_ROOT, on_screen_update=lambda text: latest.update(text=text))
    creds_path = os.path.join(REPO_ROOT, "data", "claude_bridge_config", ".credentials.json")
    if not os.path.exists(creds_path):
        # Not yet signed in -- force a truly fresh session rather than silently
        # reattaching to whatever a previous ABORTED attempt left running (e.g.
        # one stuck on an expired OAuth prompt, which would never re-trigger
        # the browser/code screen). Once credentials already exist, a live
        # session may hold real conversation history worth keeping, so this
        # only ever resets the pre-sign-in case, where there's nothing to lose.
        bridge.stop()
    bridge.start()
    print("Starting Claude sign-in. When your browser opens, click Authorize -- everything else is automatic.")
    asked_for_code = False
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            if not bridge.is_alive():
                print("Claude exited before setup finished.")
                return False
            if bridge.is_ready():
                print("Setup complete -- the bridge will start on its own next time the ecosystem boots.")
                return True
            if not asked_for_code and "Paste code here" in latest["text"]:
                asked_for_code = True
                try:
                    code = input("Paste the code shown in your browser and press Enter: ").strip()
                except EOFError:
                    print("No interactive input available to paste the code -- run this command directly "
                          "in your own terminal (not piped, backgrounded, or run by an agent), then paste "
                          "the code there when prompted.")
                    return False
                if code:
                    bridge.write(code)
            time.sleep(0.5)
        print("Timed out waiting for sign-in to finish.")
        return False
    finally:
        bridge.stop()


if __name__ == "__main__":
    if "--setup" in sys.argv:
        sys.exit(0 if run_setup() else 1)
    service = ClaudeBridgeService()
    asyncio.run(service.run())
