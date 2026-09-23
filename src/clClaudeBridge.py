import asyncio
import json
import logging
import os
import sys
import platform

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

    def _on_screen_update(self, screen_text: str):
        # Called from the PTY reader's background thread -- hop onto the
        # asyncio loop rather than touching the MQTT client from off-thread.
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
                "jarvis/claude/screen", json.dumps({"text": screen_text}), retain=True
            )
        except Exception as e:
            logging.error(f"[CLAUDE BRIDGE] Failed to publish screen update: {e}")

    def _ensure_pty_started(self):
        if self.bridge and self.bridge.is_alive():
            return
        if CURRENT_OS != "Windows":
            logging.error("[CLAUDE BRIDGE] Linux/tmux backend not implemented yet.")
            return
        from utils.clPtyBridge import ClaudePtyBridge
        self.bridge = ClaudePtyBridge(cwd=REPO_ROOT, on_screen_update=self._on_screen_update)
        self.bridge.start()

    async def run(self) -> None:
        # The real prerequisite is a completed one-time interactive login
        # in this isolated config dir (see CLAUDE.md) -- not any env var.
        # CLAUDE_CODE_OAUTH_TOKEN is documented for headless `-p` calls only
        # and is deliberately NOT passed into this interactive spawn (see
        # clPtyBridge.py's start()): its presence was observed forcing a
        # fresh browser re-auth attempt even with valid saved credentials.
        creds_path = os.path.join(REPO_ROOT, "data", "claude_bridge_config", ".credentials.json")
        if CURRENT_OS == "Windows" and not os.path.exists(creds_path):
            # Mirrors clSpotify.py's precedent: an unconfigured integration
            # parks itself instead of letting the supervisor resurrect a
            # process that can't do its one job, over and over.
            logging.critical(
                "[CLAUDE BRIDGE] No credentials at data/claude_bridge_config/.credentials.json -- "
                "complete the one-time login first (see CLAUDE.md's Claude voice bridge section). "
                "Bridge will not start."
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
                    logging.info("[CLAUDE BRIDGE] Subscribed. Ready.")

                    async for message in client.messages:
                        topic = message.topic.value
                        if topic == "jarvis/claude/question":
                            try:
                                payload = json.loads(message.payload.decode())
                            except json.JSONDecodeError:
                                continue
                            keys = payload.get("keys")
                            text = str(payload.get("text", "")).strip()
                            if not keys and not text:
                                continue
                            self._ensure_pty_started()
                            try:
                                if keys:
                                    self.bridge.send_keys(keys)
                                    logging.info(f"[CLAUDE BRIDGE] Sent raw keys: {keys!r}")
                                else:
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


if __name__ == "__main__":
    service = ClaudeBridgeService()
    asyncio.run(service.run())
