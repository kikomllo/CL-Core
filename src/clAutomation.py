"""
Automation engine: reacts to events from other services and asks actuators to do things. It only
ever talks over MQTT -- it listens to the presence monitor and publishes ActionRouter actions.

Today it has one rule, "presence lights": when the paired device arrives in the evening the chosen
lights come on, and when it leaves they go off after a short delay. Settings live in
config/core.json -> settings.automation_settings (edited in the dashboard's Presence tab).
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('AUTOMATION')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt
from utils.clActionRouter import ActionRouter

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CORE_JSON = os.path.join(REPO_ROOT, "config", "core.json")

PRESENCE_TOPIC = "jarvis/sys/presence"
EVENT_TOPIC = "jarvis/sys/presence/event"

DEFAULT_SETTINGS = {
    "enabled": True,
    "evening_start_hour": 19,   # lights only come on from this hour...
    "evening_end_hour": 7,      # ...until this one
    "presence_lights": {
        "enabled": False,
        "lights": [],          # light names as saved in devices.json
        "off_on_leave": True,
        "leave_delay_s": 5,    # extra wait after the monitor reports you left
    },
}


def load_settings(core_path: str = CORE_JSON) -> dict:
    """Defaults overlaid with core.json's optional settings.automation_settings block."""
    try:
        with open(core_path, "r", encoding="utf-8") as f:
            core = json.load(f)
    except (OSError, ValueError):
        core = {}
    overrides = core.get("settings", {}).get("automation_settings", {})
    if not isinstance(overrides, dict):
        overrides = {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key in ("enabled",):
        if isinstance(overrides.get(key), bool):
            merged[key] = overrides[key]
    followup = core.get("settings", {}).get("followup_settings", {})
    for key in ("evening_start_hour", "evening_end_hour"):
        for source in (overrides, followup):
            if isinstance(source.get(key), int) and not isinstance(source.get(key), bool):
                merged[key] = source[key]
                break
    rule = overrides.get("presence_lights", {})
    if isinstance(rule, dict):
        target = merged["presence_lights"]
        for key in ("enabled", "off_on_leave"):
            if isinstance(rule.get(key), bool):
                target[key] = rule[key]
        if isinstance(rule.get("lights"), list):
            target["lights"] = [str(name) for name in rule["lights"] if isinstance(name, str) and name.strip()]
        delay = rule.get("leave_delay_s")
        if isinstance(delay, (int, float)) and not isinstance(delay, bool):
            target["leave_delay_s"] = max(0, float(delay))
    return merged


def in_dark_window(now: float, settings: dict) -> bool:
    """Whether `now` falls between the evening start and end hours (overnight ranges wrap midnight)."""
    hour = datetime.fromtimestamp(now).hour
    start, end = settings["evening_start_hour"], settings["evening_end_hour"]
    return hour >= start or hour < end if start > end else start <= hour < end


class AutomationEngine:
    """Pure rule logic: feed it events and clock ticks, drain the actions it wants performed."""

    def __init__(self, settings_loader: Callable[[], dict] = load_settings,
                 clock: Callable[[], float] = time.time):
        self.load_settings = settings_loader
        self.clock = clock
        self.home: Optional[bool] = None
        self._leave_at: Optional[float] = None
        self.outbox: List[Tuple[str, dict]] = []

    def drain(self) -> List[Tuple[str, dict]]:
        actions, self.outbox = self.outbox, []
        return actions

    def _lights(self, action: str, settings: dict):
        for name in settings["presence_lights"]["lights"]:
            self.outbox.append(("light.set", {"action": action, "light_target": name, "silent": True}))
        logging.info(f"[AUTOMATION] Lights {action}: {', '.join(settings['presence_lights']['lights'])}.")

    def _active(self, settings: dict) -> bool:
        rule = settings["presence_lights"]
        return settings["enabled"] and rule["enabled"] and bool(rule["lights"])

    def on_presence_state(self, payload: dict):
        """The retained presence state, used only to learn whether you're already home at startup."""
        if self.home is None and isinstance(payload.get("present"), bool):
            self.home = payload["present"]

    def on_presence_event(self, event: str, now: Optional[float] = None):
        now = self.clock() if now is None else now
        settings = self.load_settings()
        if event == "arrived":
            was_home, self.home, self._leave_at = self.home, True, None
            if was_home:
                return  # the monitor restarted while you were home
            if self._active(settings) and in_dark_window(now, settings):
                self._lights("on", settings)
        elif event == "left":
            self.home = False
            if self._active(settings) and settings["presence_lights"]["off_on_leave"]:
                self._leave_at = now + settings["presence_lights"]["leave_delay_s"]

    def tick(self, now: Optional[float] = None):
        now = self.clock() if now is None else now
        if self._leave_at is None or now < self._leave_at:
            return
        self._leave_at = None
        settings = self.load_settings()
        if self._active(settings):
            self._lights("off", settings)


class AutomationService:
    def __init__(self, engine: Optional[AutomationEngine] = None):
        self.engine = engine or AutomationEngine()
        self.router = ActionRouter()

    async def _flush(self, client):
        for action_id, kwargs in self.engine.drain():
            topic, payload = self.router.prepare(action_id, **kwargs)
            if topic:
                await client.publish(topic, json.dumps(payload))

    async def _ticker(self, client):
        while True:
            self.engine.tick()
            await self._flush(client)
            await asyncio.sleep(1)

    def handle_message(self, topic: str, payload: Dict):
        if topic == EVENT_TOPIC:
            self.engine.on_presence_event(str(payload.get("event")), payload.get("ts"))
        elif topic == PRESENCE_TOPIC:
            self.engine.on_presence_state(payload)

    async def run(self):
        logging.info("[AUTOMATION] Online. Connecting to MQTT broker...")
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    await client.subscribe(PRESENCE_TOPIC)
                    await client.subscribe(EVENT_TOPIC)
                    await client.publish("jarvis/sys/module_ready", json.dumps({"module": "automation"}))
                    logging.info("[AUTOMATION] Subscribed. Ready.")
                    ticker = asyncio.create_task(self._ticker(client))
                    try:
                        async for message in client.messages:
                            try:
                                payload = json.loads(message.payload.decode())
                            except ValueError:
                                continue
                            if isinstance(payload, dict):
                                self.handle_message(str(message.topic), payload)
                                await self._flush(client)
                    finally:
                        ticker.cancel()
            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"MQTT Connection Error: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"Unexpected automation error: {type(e).__name__}: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)


if __name__ == "__main__":
    try:
        asyncio.run(AutomationService().run())
    except KeyboardInterrupt:
        pass
