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


class ClaudeBridgeService:
    """Routes 'ask claude ...' voice commands (already resolved to plain
    text on jarvis/claude/question by clDaemon.py) into a real, persistent
    `claude` terminal session, and mirrors that session's screen back out
    over MQTT for a dashboard widget to display.

    Windows-only for now (ConPTY via clPtyBridge.ClaudePtyBridge). The
    Linux side (tmux send-keys/capture-pane) is a follow-up -- tmux does
    the ANSI-resolution job pyte does here for free, so it doesn't need
    this same wrapper.
    """

    def __init__(self):
        self.bridge = None
        self.loop = None
        self.mqtt_client = None
        self._last_screen = ""
        self._last_question = None
        self._recovering = False
        self._answered = False

    @staticmethod
    def _trim_screen(screen_text: str) -> str:
        """Drops the fixed-grid padding: trailing spaces per row and blank rows at the bottom."""
        return "\n".join(row.rstrip() for row in screen_text.split("\n")).rstrip("\n")

    def _on_screen_update(self, screen_text: str):
        # Called from the PTY reader's background thread -- hop onto the
        # asyncio loop rather than touching the MQTT client from off-thread.
        screen_text = self._trim_screen(screen_text)
        if screen_text == self._last_screen:
            return
        self._last_screen = screen_text
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

    def _ensure_pty_started(self) -> bool:
        """Returns True only if a new session was just started."""
        if self.bridge and self.bridge.is_alive():
            return False
        if CURRENT_OS != "Windows":
            logging.error("[CLAUDE BRIDGE] Linux/tmux backend not implemented yet.")
            return False
        from utils.clPtyBridge import ClaudePtyBridge
        self.bridge = ClaudePtyBridge(
            cwd=REPO_ROOT, on_screen_update=self._on_screen_update, on_stall=self._on_stall
        )
        self.bridge.start()
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
        # The prerequisite is a saved sign-in in the isolated config dir (--setup), not an env var.
        creds_path = os.path.join(REPO_ROOT, "data", "claude_bridge_config", ".credentials.json")
        if CURRENT_OS == "Windows" and not os.path.exists(creds_path):
            # Parks like clSpotify.py so the supervisor doesn't respawn a service that can't work.
            logging.critical(
                "[CLAUDE BRIDGE] No saved login yet -- run 'python src/clClaudeBridge.py --setup' "
                "once (it automates everything except clicking Authorize in the browser). "
                "Bridge will not start until then."
            )
            while True:
                await asyncio.sleep(3600)

        logging.info("[CLAUDE BRIDGE] Online. Connecting to MQTT broker...")
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    self.mqtt_client = client
                    self.loop = asyncio.get_running_loop()
                    self._ensure_pty_started()

                    await client.subscribe("jarvis/claude/question")
                    await client.subscribe("jarvis/claude/reply")
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
                            if self._ensure_pty_started():
                                await self._wait_until_ready()
                            try:
                                if keys:
                                    self.bridge.send_keys(keys)
                                    logging.info(f"[CLAUDE BRIDGE] Sent raw keys: {keys!r}")
                                else:
                                    self._last_question = text
                                    self._answered = False
                                    self.bridge.write(text)
                                    logging.info(f"[CLAUDE BRIDGE] Injected: '{text}'")
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
    """One-time sign-in. Every first-run screen is auto-answered by ClaudePtyBridge; the only
    human step is clicking Authorize in the browser (and pasting a code if the page shows one)."""
    if CURRENT_OS != "Windows":
        print("The Claude bridge is Windows-only for now (Linux/tmux backend not implemented yet).")
        return False
    from utils.clPtyBridge import ClaudePtyBridge
    latest = {"text": ""}
    bridge = ClaudePtyBridge(cwd=REPO_ROOT, on_screen_update=lambda text: latest.update(text=text))
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
                code = input("Paste the code shown in your browser and press Enter: ").strip()
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
