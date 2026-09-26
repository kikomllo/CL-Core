"""
Presence monitor: is *my* paired device (a beacon) nearby?

One device, paired once, recognised by a rotating token in its BLE advertisement (see
utils/clBeacon.py) rather than by a MAC address or by "any device in range". The monitor only
publishes what it sees -- other modules decide what to do with it:

  jarvis/sys/presence          (retained) {present, rssi, last_seen, since, paired, scanning, available, reason}
  jarvis/sys/presence/event    {event: "arrived" | "left", rssi, ts}
  jarvis/monitor/pairing       {state: waiting|paired|expired|cancelled|failed, code, uri, expires_at, reason}
  jarvis/monitor/control   <-  {action: pair_start | pair_cancel | unpair | status}

Scanning only runs while a device is paired or a pairing is open, so an unpaired install never
touches Bluetooth. Pair from the dashboard, by voice, or `python src/clMonitor.py --pair`.
"""
import asyncio
import json
import logging
import os
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('MONITOR')

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt
from utils import clBeacon

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STORE_PATH = os.path.join(REPO_ROOT, "data", "monitor", "paired_device.json")
CORE_JSON = os.path.join(REPO_ROOT, "config", "core.json")

CONTROL_TOPIC = "jarvis/monitor/control"
PAIRING_TOPIC = "jarvis/monitor/pairing"
PRESENCE_TOPIC = "jarvis/sys/presence"
EVENT_TOPIC = "jarvis/sys/presence/event"
SPEAK_TOPIC = "jarvis/sys/speak"

DEFAULT_SETTINGS = {
    "enabled": True,
    "enter_rssi": -75,        # smoothed signal needed to count as arrived
    "exit_rssi": -88,         # smoothed signal below which you count as gone
    "away_timeout_s": 45,     # no valid beacon for this long means gone
    "smoothing": 0.4,         # weight of the newest RSSI sample
    "pairing_timeout_s": 300,
    "evaluate_every_s": 2,
    "scanner_stall_s": 90,    # no advertisements of any kind for this long means restart the scan
    "unavailable_retry_s": 30,
}


def load_settings(core_path: str = CORE_JSON) -> dict:
    """Defaults overlaid with core.json's optional settings.monitor_settings block."""
    merged = dict(DEFAULT_SETTINGS)
    try:
        with open(core_path, "r", encoding="utf-8") as f:
            overrides = json.load(f).get("settings", {}).get("monitor_settings", {})
    except (OSError, ValueError):
        overrides = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = overrides.get(key, default)
        if isinstance(default, bool):
            merged[key] = bool(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[key] = type(default)(value)
    merged["smoothing"] = min(1.0, max(0.05, merged["smoothing"]))
    return merged


class PresenceTracker:
    """Turns a stream of valid-beacon sightings into present/away with hysteresis: you arrive when
    the smoothed signal reaches enter_rssi, and only leave when it drops below exit_rssi or the
    beacon goes quiet, so hovering at the edge of the room doesn't flap."""

    def __init__(self, enter_rssi: float, exit_rssi: float, away_timeout_s: float, smoothing: float):
        self.enter_rssi, self.exit_rssi = enter_rssi, exit_rssi
        self.away_timeout_s, self.smoothing = away_timeout_s, smoothing
        self.present = False
        self.rssi_avg: Optional[float] = None
        self.last_seen: Optional[float] = None
        self.since: float = 0.0

    def observe(self, rssi: int, now: float):
        self.last_seen = now
        self.rssi_avg = rssi if self.rssi_avg is None else (
            self.smoothing * rssi + (1 - self.smoothing) * self.rssi_avg)

    def update(self, now: float) -> Optional[str]:
        seen_recently = self.last_seen is not None and now - self.last_seen <= self.away_timeout_s
        if not self.present:
            if seen_recently and self.rssi_avg is not None and self.rssi_avg >= self.enter_rssi:
                self.present, self.since = True, now
                return "arrived"
        elif not seen_recently or (self.rssi_avg is not None and self.rssi_avg < self.exit_rssi):
            self.present, self.since = False, now
            self.rssi_avg = None  # start the next arrival from fresh readings
            return "left"
        return None

    def reset(self):
        self.present, self.rssi_avg, self.last_seen = False, None, None


class PairingStore:
    """The paired device's shared secret, in gitignored per-user runtime state (data/)."""

    def __init__(self, path: str = STORE_PATH):
        self.path = path

    def load(self) -> Optional[bytes]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return clBeacon.decode_secret(json.load(f)["secret"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def load_static(self) -> bool:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f).get("static") is True
        except (OSError, ValueError, AttributeError):
            return False

    def save(self, secret: bytes, static: bool = False):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"secret": clBeacon.encode_secret(secret), "paired_at": time.time(),
                       "static": static}, f)
        os.replace(tmp, self.path)

    def clear(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


class ScannerUnavailable(Exception):
    pass


def _reason(error: Exception) -> str:
    """A short human sentence from whatever the Bluetooth stack raised (bleak puts the message
    first in args, followed by an enum)."""
    first = error.args[0] if error.args else ""
    return (first if isinstance(first, str) and first else str(error) or type(error).__name__)[:160]


class BeaconScanner:
    """A thin bleak wrapper: hands every advertisement carrying our service data to the callback."""

    def __init__(self, on_beacon: Callable[[bytes, int], None]):
        self._on_beacon = on_beacon
        self._scanner = None
        self.last_any_advertisement = time.time()

    async def start(self):
        try:
            from bleak import BleakScanner
        except ImportError:
            raise ScannerUnavailable("the bleak package isn't installed") from None
        try:
            self._scanner = BleakScanner(detection_callback=self._callback)
            await self._scanner.start()
        except Exception as e:  # adapter off/missing, permissions, BlueZ not running...
            self._scanner = None
            raise ScannerUnavailable(_reason(e)) from None
        self.last_any_advertisement = time.time()

    async def stop(self):
        scanner, self._scanner = self._scanner, None
        if scanner is not None:
            try:
                await scanner.stop()
            except Exception as e:
                logging.warning(f"[MONITOR] Scanner stop failed: {e}")

    @property
    def running(self) -> bool:
        return self._scanner is not None

    def _callback(self, device, adv):
        self.last_any_advertisement = time.time()
        payload = clBeacon.beacon_payload(adv.service_data or {})
        if payload is not None:
            self._on_beacon(payload, adv.rssi)


class PairingSession:
    def __init__(self, secret: bytes, expires_at: float, static: bool = False):
        self.secret, self.expires_at, self.static = secret, expires_at, static


Outbound = Tuple[str, dict, bool]  # topic, payload, retain


class MonitorService:
    def __init__(self, settings: Optional[dict] = None, store: Optional[PairingStore] = None,
                 scanner_factory: Callable = BeaconScanner, clock: Callable[[], float] = time.time):
        self.settings = settings or load_settings()
        self.store = store or PairingStore()
        self.scanner_factory = scanner_factory
        self.clock = clock
        self.secret: Optional[bytes] = self.store.load()
        self.static: bool = self.secret is not None and self.store.load_static()
        self.tracker = PresenceTracker(
            self.settings["enter_rssi"], self.settings["exit_rssi"],
            self.settings["away_timeout_s"], self.settings["smoothing"])
        self.pairing: Optional[PairingSession] = None
        self.scanner = None
        self.available: Optional[bool] = None
        self.reason: Optional[str] = None
        self._retry_at = 0.0
        self._last_eval = 0.0
        self._last_presence: Optional[tuple] = None
        self._last_presence_at = 0.0
        self._outbox: List[Outbound] = []

    # ---- output -------------------------------------------------------------------------
    def _emit(self, topic: str, payload: dict, retain: bool = False):
        self._outbox.append((topic, payload, retain))

    def _say(self, text: str):
        # Not ignore_silent: silent mode mutes the monitor like everything else.
        self._emit(SPEAK_TOPIC, {"text": text})

    def drain(self) -> List[Outbound]:
        out, self._outbox = self._outbox, []
        return out

    def presence_payload(self) -> dict:
        t = self.tracker
        return {
            "present": t.present,
            "rssi": round(t.rssi_avg) if t.present and t.rssi_avg is not None else None,
            "last_seen": t.last_seen,
            "since": t.since or None,
            "paired": self.secret is not None,
            "test_mode": self.static,
            "scanning": bool(self.scanner and self.scanner.running),
            "available": self.available,
            "reason": self.reason,
        }

    def _publish_presence(self, now: float, force: bool = False):
        payload = self.presence_payload()
        key = (payload["present"], payload["paired"], payload["scanning"], payload["available"],
               payload["reason"], None if payload["rssi"] is None else round(payload["rssi"] / 5))
        heartbeat = payload["present"] and now - self._last_presence_at >= 30
        if force or key != self._last_presence or heartbeat:
            self._last_presence, self._last_presence_at = key, now
            self._emit(PRESENCE_TOPIC, payload, retain=True)

    def _pairing_state(self, state: str, reason: Optional[str] = None):
        payload = {"state": state, "reason": reason}
        if state == "waiting" and self.pairing:
            payload.update(static=self.pairing.static,
                           code=clBeacon.encode_secret(self.pairing.secret),
                           uri=clBeacon.pairing_uri(self.pairing.secret),
                           expires_at=self.pairing.expires_at)
        self._emit(PAIRING_TOPIC, payload)

    # ---- beacon input -------------------------------------------------------------------
    def on_beacon(self, payload: bytes, rssi: int, now: Optional[float] = None):
        now = self.clock() if now is None else now
        if self.pairing and now <= self.pairing.expires_at and self._valid(
                self.pairing.secret, payload, now, self.pairing.static):
            self._complete_pairing(now)
            return
        if self.secret and self._valid(self.secret, payload, now, self.static):
            self.tracker.observe(rssi, now)

    @staticmethod
    def _valid(secret: bytes, payload: bytes, now: float, allow_static: bool) -> bool:
        return clBeacon.verify_payload(secret, payload, now) or (
            allow_static and clBeacon.verify_static(secret, payload))

    def _complete_pairing(self, now: float):
        secret, static = self.pairing.secret, self.pairing.static
        self.pairing = None
        self.store.save(secret, static)
        self.secret, self.static = secret, static
        self.tracker.reset()
        logging.info("[MONITOR] Device paired.")
        self._pairing_state("paired")
        self._say("Device paired. I'll keep an eye out for you.")
        self._publish_presence(now, force=True)

    # ---- periodic work ------------------------------------------------------------------
    def tick(self, now: Optional[float] = None):
        now = self.clock() if now is None else now
        if self.pairing and now > self.pairing.expires_at:
            self.pairing = None
            self._pairing_state("expired")
            self._say("Pairing timed out.")
        event = self.tracker.update(now)
        if event:
            logging.info(f"[MONITOR] {event.capitalize()} (rssi {self.tracker.rssi_avg}).")
            self._emit(EVENT_TOPIC, {"event": event, "ts": now,
                                     "rssi": None if self.tracker.rssi_avg is None else round(self.tracker.rssi_avg)})
        self._publish_presence(now, force=bool(event))

    @property
    def scanning_needed(self) -> bool:
        return self.secret is not None or self.pairing is not None

    async def ensure_scanner(self, now: Optional[float] = None):
        """Start/stop/restart scanning to match what's needed."""
        now = self.clock() if now is None else now
        running = bool(self.scanner and self.scanner.running)
        if not self.scanning_needed:
            if running:
                await self.scanner.stop()
                self.scanner = None
            return
        if running:
            if now - self.scanner.last_any_advertisement > self.settings["scanner_stall_s"]:
                logging.warning("[MONITOR] No advertisements at all for a while; restarting the scan.")
                await self.scanner.stop()
            else:
                return
        if now < self._retry_at:
            return
        self.scanner = self.scanner or self.scanner_factory(self.on_beacon)
        try:
            await self.scanner.start()
        except ScannerUnavailable as e:
            self.available, self.reason = False, str(e)
            self._retry_at = now + self.settings["unavailable_retry_s"]
            logging.warning(f"[MONITOR] Bluetooth scanning unavailable: {e}")
            if self.pairing:
                self.pairing = None
                self._pairing_state("failed", self.reason)
                self._say("I can't reach Bluetooth right now, so I can't pair.")
        else:
            self.available, self.reason = True, None
            logging.info("[MONITOR] Scanning for the paired beacon.")
        self._publish_presence(now, force=True)

    # ---- control ------------------------------------------------------------------------
    def handle_control(self, payload: dict, now: Optional[float] = None):
        now = self.clock() if now is None else now
        action = payload.get("action")
        if action == "pair_start":
            self.pair_start(now, static=payload.get("static") is True)
        elif action == "pair_cancel":
            self.pair_cancel()
        elif action == "unpair":
            self.unpair(now)
        elif action == "status":
            self.status(now)

    def pair_start(self, now: float, static: bool = False):
        if not self.settings["enabled"]:
            self._say("The presence monitor is turned off in settings.")
            return
        if self.pairing is None or now > self.pairing.expires_at or self.pairing.static != static:
            self.pairing = PairingSession(
                clBeacon.generate_secret(), now + self.settings["pairing_timeout_s"], static)
        self._retry_at = 0.0
        self._pairing_state("waiting")
        self._say("Pairing mode is on. Test mode, so the beacon may use the fixed payload." if static else
                  "Pairing mode is on. Enter the code from the dashboard into your app within five minutes.")

    def pair_cancel(self):
        if self.pairing:
            self.pairing = None
            self._pairing_state("cancelled")
            self._say("Pairing cancelled.")

    def unpair(self, now: float):
        if self.secret is None:
            self._say("No device is paired.")
            return
        was_present = self.tracker.present
        self.store.clear()
        self.secret = None
        self.tracker.reset()
        self.static = False
        if was_present:
            self._emit(EVENT_TOPIC, {"event": "left", "ts": now, "rssi": None})
        self._say("Pairing removed.")
        self._publish_presence(now, force=True)

    def status(self, now: float):
        p = self.presence_payload()
        if not p["paired"]:
            self._say("No device is paired. Say pair my phone to set one up.")
        elif p["available"] is False:
            self._say(f"Bluetooth is unavailable: {p['reason']}")
        elif p["present"]:
            self._say("I can see your device.")
        else:
            self._say("I can't see your device right now.")
        self._publish_presence(now, force=True)

    # ---- runtime ------------------------------------------------------------------------
    async def _flush(self, client):
        for topic, payload, retain in self.drain():
            await client.publish(topic, json.dumps(payload), retain=retain)

    async def _ticker(self, client):
        while True:
            now = self.clock()
            if now - self._last_eval >= self.settings["evaluate_every_s"]:
                self._last_eval = now
                self.tick(now)
                await self.ensure_scanner(now)
            await self._flush(client)
            await asyncio.sleep(0.5)

    async def run(self):
        logging.info("[MONITOR] Online. Connecting to MQTT broker...")
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client("localhost") as client:
                    attempt = 0
                    await client.subscribe(CONTROL_TOPIC)
                    self._publish_presence(self.clock(), force=True)
                    await self._flush(client)
                    await client.publish("jarvis/sys/module_ready", json.dumps({"module": "monitor"}))
                    logging.info("[MONITOR] Subscribed. Ready.")
                    ticker = asyncio.create_task(self._ticker(client))
                    try:
                        async for message in client.messages:
                            try:
                                payload = json.loads(message.payload.decode())
                            except ValueError:
                                continue
                            if isinstance(payload, dict):
                                self.handle_control(payload)
                                await self.ensure_scanner()
                                await self._flush(client)
                    finally:
                        ticker.cancel()
                        if self.scanner:
                            await self.scanner.stop()
            except (aiomqtt.MqttError, OSError, ConnectionRefusedError) as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"MQTT Connection Error: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                delay = min(30, 2 ** attempt)
                logging.error(f"Unexpected monitor error: {type(e).__name__}: {e}. Retrying in {delay}s...")
                attempt += 1
                await asyncio.sleep(delay)


def _cli(argv: List[str]) -> int:
    """`--pair` / `--unpair` / `--status` talk to the running monitor over MQTT."""
    import threading
    import paho.mqtt.client as mqtt

    command = {"--pair": "pair_start", "--unpair": "unpair", "--status": "status", "--cancel": "pair_cancel"}.get(
        argv[1] if len(argv) > 1 else "")
    static = "--static" in argv[2:]
    if command is None or (static and command != "pair_start"):
        print("usage: python src/clMonitor.py [--pair [--static] | --cancel | --unpair | --status]")
        return 1

    heard, done, exit_code = threading.Event(), threading.Event(), [0]
    wanted = PRESENCE_TOPIC if command == "status" else PAIRING_TOPIC

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
        except ValueError:
            return
        heard.set()
        if command == "status":
            if not done.is_set():
                print(json.dumps(data, indent=2))
            done.set()
        elif command == "pair_start":
            state = data.get("state")
            if state == "waiting":
                print("Pairing is open. Give your beacon this code:\n")
                print(f"    {data['code']}\n")
                if data.get("static"):
                    print("  TEST MODE: the beacon may advertise the fixed payload from\n"
                          "  `python src/utils/clBeacon.py payload <CODE> --static` (replayable, for testing only).\n")
                print(f"  (or this link: {data['uri']})")
                print("\nWaiting for the beacon... (Ctrl+C to stop waiting)")
            elif state == "paired":
                print("Paired.")
                done.set()
            elif state in ("expired", "cancelled", "failed"):
                print(f"Pairing {state}" + (f": {data.get('reason')}" if data.get("reason") else "."))
                exit_code[0] = 1
                done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    try:
        client.connect("localhost", 1883, 10)
    except OSError:
        print("Can't reach the MQTT broker -- is the ecosystem running?")
        return 1
    if command in ("unpair", "pair_cancel"):
        client.publish(CONTROL_TOPIC, json.dumps({"action": command})).wait_for_publish(timeout=5)
        print("Done.")
        client.disconnect()
        return 0
    client.subscribe(wanted)
    client.loop_start()
    client.publish(CONTROL_TOPIC, json.dumps({"action": command, **({"static": True} if static else {})}))
    try:
        if not heard.wait(timeout=6):
            print("No answer from the monitor -- is the ecosystem running?")
            exit_code[0] = 1
        elif command == "pair_start":
            done.wait(timeout=DEFAULT_SETTINGS["pairing_timeout_s"] + 10)
    except KeyboardInterrupt:
        client.publish(CONTROL_TOPIC, json.dumps({"action": "pair_cancel"}))
        exit_code[0] = 1
    client.loop_stop()
    return exit_code[0]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv))
    service = MonitorService()
    if not service.settings["enabled"]:
        logging.info("[MONITOR] Disabled in settings; idling.")
    asyncio.run(service.run())
