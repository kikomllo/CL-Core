"""Web dashboard server: serves web/ and bridges the browser to the MQTT backbone.

A browser can't speak to Mosquitto's plain-TCP listener, and the system broker's config needs admin rights to change,
so this one small process keeps a single paho connection and fans it out over a WebSocket (/ws). The dashboard is
still "just another MQTT client": it never imports another service, it only asks this bridge to subscribe, publish
or dispatch an `actions.json` action. Bound to 127.0.0.1 and refuses any cross-origin WebSocket, so a web page the
user happens to open can't drive their house.

Run standalone:  python src/clWebUI.py   (then open http://127.0.0.1:8765/)
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time

from aiohttp import web, WSMsgType
from aiohttp.log import access_logger
import paho.mqtt.client as mqtt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "utils"))

from utils.clActionRouter import ActionRouter  # noqa: E402
from utils.clConfigLoader import ConfigLoader  # noqa: E402
from utils import clAutostart, clBeacon, clLogTail, clWebDevices, clWebSettings  # noqa: E402
import clSkin  # noqa: E402
from utils import clWebCss  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_WEB_PORT", "8765"))
WEB_DIR = os.path.join(ROOT, "web")
# Only these asset folders are public; assets/ also holds the TTS cache and voice samples.
ASSET_DIRS = ("fonts", "icons", "themes")
CONFIG_PATH = os.path.join(ROOT, "config", "core.json")
ENV_PATH = os.path.join(ROOT, ".env")


def access_log():
    """aiohttp's per-request access log, off. It prints one line per request into the process's stdout, which the supervisor writes to the log
    file, which the Live Logs widget polls (GET /api/log) and shows: every poll made the line the next poll displayed, an endless stream (and
    every icon and the 3 s settings poll added to it). JARVIS_WEB_ACCESS_LOG=1 turns it back on for debugging."""
    return access_logger if os.environ.get("JARVIS_WEB_ACCESS_LOG") == "1" else None


# one-shot commands: a page that opens later must not be handed an old one (a stale set_overlay would flip the desktop shell's mode)
NO_REPLAY = {"jarvis/sys/ui_control", "jarvis/console/out"}      # the terminal stream: a stale chunk shown to a new tab would be garbage (it asks for a proper replay with jarvis/console/attach)


class MqttBridge:
    """One paho client shared by every browser tab. Keeps the last payload per topic so a tab that opens later
    gets the current state at once, as a retained message would."""

    def __init__(self, loop):
        self.loop = loop
        self.clients = set()                 # connected WebSocketResponse objects
        self.subscribed = set()
        self.last = {}                       # topic -> raw text of the last message
        self.router = ActionRouter()
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message
        self.lock = threading.Lock()

    def start(self):
        self.mqtt.connect_async("localhost", 1883, 60)
        self.mqtt.loop_start()

    def stop(self):
        self.mqtt.loop_stop()
        self.mqtt.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            topics = list(self.subscribed)
        for topic in topics:
            client.subscribe(topic)
        client.publish("jarvis/sys/module_ready", json.dumps({"module": "web_ui"}), retain=False)

    def _on_message(self, client, userdata, msg):
        try:
            text = msg.payload.decode("utf-8", "replace")
        except Exception:
            return
        if msg.topic not in NO_REPLAY:
            self.last[msg.topic] = text
        frame = json.dumps({"t": msg.topic, "p": text})
        self.loop.call_soon_threadsafe(self._broadcast, frame, msg.topic)

    def _broadcast(self, frame, topic):
        for ws in list(self.clients):
            if topic in getattr(ws, "topics", ()) or _matches(getattr(ws, "topics", ()), topic):
                asyncio.ensure_future(_send(ws, frame))

    def subscribe(self, ws, topics):
        """Subscribe for a tab, then replay what the bridge already knows for those topics."""
        ws.topics = getattr(ws, "topics", set())
        for topic in topics:
            if not isinstance(topic, str) or not topic.startswith(("jarvis/", "pc/")):
                continue
            ws.topics.add(topic)
            with self.lock:
                new = topic not in self.subscribed
                self.subscribed.add(topic)
            if new and self.mqtt.is_connected():
                self.mqtt.subscribe(topic)
            for known, text in list(self.last.items()):
                if known == topic or _matches((topic,), known):
                    asyncio.ensure_future(_send(ws, json.dumps({"t": known, "p": text})))

    def publish(self, topic, payload, retain=False):
        if not isinstance(topic, str) or not topic.startswith(("jarvis/", "pc/")):
            return
        body = payload if isinstance(payload, str) else json.dumps(payload)
        self.mqtt.publish(topic, body, retain=bool(retain))

    def dispatch(self, action, kwargs):
        topic, payload = self.router.prepare(action, **(kwargs or {}))
        if topic:
            self.mqtt.publish(topic, json.dumps(payload))
        else:
            logging.warning("[WEBUI] action %r rejected", action)


def _matches(filters, topic):
    for flt in filters:
        if flt.endswith("/#") and topic.startswith(flt[:-1]):
            return True
        if "+" in flt:
            a, b = flt.split("/"), topic.split("/")
            if len(a) == len(b) and all(x in ("+", y) for x, y in zip(a, b)):
                return True
    return False


async def _send(ws, frame):
    try:
        await ws.send_str(frame)
    except Exception:
        pass


def _origin_ok(request):
    origin = request.headers.get("Origin")
    if not origin:                          # a non-browser client (tests, curl) sends none
        return True
    return origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")


async def ws_handler(request):
    if not _origin_ok(request):
        return web.Response(status=403, text="cross-origin websocket refused")
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    bridge = request.app["bridge"]
    ws.topics = set()
    bridge.clients.add(ws)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if "sub" in data:
                bridge.subscribe(ws, data["sub"])
            elif "act" in data:
                bridge.dispatch(data["act"], data.get("k"))
            elif "pub" in data:
                bridge.publish(data["pub"], data.get("p"), data.get("retain", False))
    finally:
        bridge.clients.discard(ws)
    return ws


async def config_handler(request):
    """settings block of core.json, read-only: the page uses it for clock/theme/etc."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f).get("settings", {})
    except Exception:
        settings = {}
    settings["theme"] = clSkin.resolve_skin(settings)      # the skin that is really in force (JARVIS_THEME overrides the setting), as /style.css uses
    return web.json_response(settings)


def _loader():
    return ConfigLoader()


def active_skin():
    """The skin for this request: JARVIS_THEME or settings.theme, read fresh so a switch shows after a page reload."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f).get("settings", {})
    except Exception:
        settings = {}
    return clSkin.resolve_skin(settings)


async def style_handler(request):
    """The stylesheet (web/css/*.css joined, see clWebCss), recoloured by the same mapping as the Qt UI (src/clSkin.py): written in JARVIS orange, so the default
    skin is the file as it is and any other skin is a deterministic recolour of every colour in it. Doing it here means the
    page never flashes the wrong palette and the mapping exists in one place."""
    def build():
        return clSkin.recolor_css(clWebCss.bundle(WEB_DIR), active_skin())
    css = await asyncio.to_thread(build)
    return web.Response(text=css, content_type="text/css", charset="utf-8")


async def settings_get(request):
    """Every setting the Settings window shows, plus the OS start-on-login entry (not a core.json setting)."""
    data = await asyncio.to_thread(clWebSettings.snapshot, _loader())
    try:
        data[clWebSettings.START_ON_LOGIN] = bool(await asyncio.to_thread(clAutostart.is_enabled))
    except Exception:
        data[clWebSettings.START_ON_LOGIN] = False
    return web.json_response(data)


async def settings_post(request):
    """{key, value}: one allow-listed setting. A write endpoint on localhost, so a page on another origin must never reach
    it: the Origin check mirrors the WebSocket's, and a JSON body (not a form) makes a cross-site fetch need a preflight."""
    if not _origin_ok(request) or request.content_type != "application/json":
        return web.json_response({"error": "refused"}, status=403)
    try:
        body = await request.json()
        key, value = body["key"], body["value"]
        value = clWebSettings.check(key, value)
    except (KeyError, TypeError, ValueError) as e:    # BadSetting is a ValueError; bad JSON too
        return web.json_response({"error": str(e) or "bad request"}, status=400)
    if key == clWebSettings.START_ON_LOGIN:
        try:
            stored = bool(await asyncio.to_thread(clAutostart.set_enabled, value))
        except Exception as e:
            logging.warning("[WEBUI] start on login failed: %s", e)
            stored = bool(await asyncio.to_thread(clAutostart.is_enabled))
    else:
        try:
            stored = await asyncio.to_thread(clWebSettings.apply, _loader(), key, value)
        except clWebSettings.BadSetting as e:         # e.g. a light that is not in devices.json
            return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"key": key, "value": stored})


async def keybinds_get(request):
    return web.json_response(await asyncio.to_thread(clWebSettings.keybinds_snapshot, _loader()))


async def keybinds_post(request):
    """{action, field: "key"|"mode", value}: same guard as the settings write (a keyboard shortcut is a system-wide hook)."""
    if not _origin_ok(request) or request.content_type != "application/json":
        return web.json_response({"error": "refused"}, status=403)
    try:
        body = await request.json()
        action, field, value = body["action"], body["field"], body["value"]
        stored = await asyncio.to_thread(clWebSettings.apply_keybind, _loader(), action, field, value)
    except (KeyError, TypeError, ValueError) as e:
        return web.json_response({"error": str(e) or "bad request"}, status=400)
    return web.json_response({"action": action, "field": field, "value": stored})


def _write_guard(request):
    """The refusal for a write that did not come from this page (another origin, or a form instead of JSON), else None."""
    if not _origin_ok(request) or request.content_type != "application/json":
        return web.json_response({"error": "refused"}, status=403)
    return None


async def devices_get(request):
    return web.json_response(await asyncio.to_thread(clWebDevices.devices_snapshot, _loader()))


async def devices_post(request):
    """{op, ...args}: one named, validated change to config/devices.json (see utils/clWebDevices.py). Replies with the new list."""
    refused = _write_guard(request)
    if refused:
        return refused
    try:
        body = await request.json()
        op = body.pop("op")
        snapshot = await asyncio.to_thread(clWebDevices.apply_device_op, _loader(), op, body)
    except (KeyError, TypeError, AttributeError, ValueError) as e:        # BadValue is a ValueError; a body that is not an object too
        return web.json_response({"error": str(e) or "bad request"}, status=400)
    return web.json_response(snapshot)


async def env_get(request):
    """The .env settings the Lights and Spotify tabs edit. Secrets are reported only as set / not set, never sent."""
    return web.json_response(await asyncio.to_thread(clWebDevices.env_snapshot, ENV_PATH))


async def env_post(request):
    refused = _write_guard(request)
    if refused:
        return refused
    try:
        body = await request.json()
        result = await asyncio.to_thread(clWebDevices.apply_env, ENV_PATH, body["key"], body["value"])
    except (KeyError, TypeError, ValueError) as e:
        return web.json_response({"error": str(e) or "bad request"}, status=400)
    return web.json_response(result)


async def pairing_info(request):
    """What the pairing panel shows for an open pairing: the beacon's service UUID and, in test mode, the service data (hex) to give
    a phone advertiser app. Computed here because the secret's encoding lives in utils/clBeacon.py."""
    info = {"uuid": clBeacon.BEACON_SERVICE_UUID, "data": ""}
    if request.query.get("static") == "1":
        try:
            info["data"] = clBeacon.static_payload(clBeacon.decode_secret(request.query.get("code", ""))).hex()
        except ValueError:
            pass
    return web.json_response(info)


LOG_INITIAL_BYTES, LOG_RAW_INITIAL_BYTES, LOG_MAX_READ = 256 * 1024, 96 * 1024, 256 * 1024     # the Qt widget's bounds (never load the log whole)


def read_log(pos, raw):
    """The whole lines written since byte offset `pos` (None = the tail), condensed unless raw. The server keeps no state: the page
    sends the offset it has reached, so any number of tabs can follow the log."""
    tailer = clLogTail.LogTailer(clLogTail.LOG_PATH, LOG_RAW_INITIAL_BYTES if raw else LOG_INITIAL_BYTES, LOG_MAX_READ)
    tailer.pos = pos
    text, note, restarted = tailer.read()
    return {"text": note + clLogTail.condense_text(text, not raw), "pos": tailer.pos, "restarted": restarted}


async def log_get(request):
    pos = request.query.get("pos", "")
    data = await asyncio.to_thread(read_log, int(pos) if pos.isdigit() else None, request.query.get("raw") == "1")
    return web.json_response(data)


async def log_terminal(request):
    """Opens a terminal window following the log, in the same mode as the view. Spawns a process, so it has the same
    same-origin + JSON-body guard as the settings write."""
    if not _origin_ok(request) or request.content_type != "application/json":
        return web.json_response({"error": "refused"}, status=403)
    try:
        raw = bool((await request.json()).get("raw"))
    except (ValueError, AttributeError):
        return web.json_response({"error": "bad request"}, status=400)
    return web.json_response({"ok": bool(await asyncio.to_thread(clLogTail.open_terminal, raw))})


_device_cache = {}          # kind -> (time, options): the first enumeration costs seconds (PortAudio / SDL probe), later ones are free
DEVICE_CACHE_SECONDS = 30


def list_devices(kind):
    """[{value, label}] for the audio devices of one kind, System Default first. `value` is the exact name that is stored and
    matched against the real device list; `label` is the cleaned-up name to show."""
    from utils import clAudioDevices
    names = [clAudioDevices.SYSTEM_DEFAULT] + (clAudioDevices.list_input_device_names() if kind == "input"
                                                else clAudioDevices.list_output_device_names())
    try:
        labels = clAudioDevices.get_clean_display_names(names, kind)
    except Exception:
        labels = {}
    return [{"value": n, "label": labels.get(n, n)} for n in names]


async def audio_devices_get(request):
    kind = request.query.get("kind")
    if kind not in ("input", "output"):
        return web.json_response({"error": "kind must be input or output"}, status=400)
    now = time.monotonic()
    cached = _device_cache.get(kind)
    if not cached or now - cached[0] > DEVICE_CACHE_SECONDS:
        try:
            options = await asyncio.to_thread(list_devices, kind)
        except Exception as e:
            logging.warning("[WEBUI] could not list %s devices: %s", kind, e)
            options = [{"value": "System Default", "label": "System Default"}]
        _device_cache[kind] = cached = (now, options)
    current = (await asyncio.to_thread(clWebSettings.snapshot, _loader()))["audio_settings"][f"{kind}_device"]
    return web.json_response({"current": current, "options": cached[1]})


@web.middleware
async def no_cache(request, handler):
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"        # a dev loop: always the file on disk
    return response


def build_app(loop):
    app = web.Application(middlewares=[no_cache])
    app["bridge"] = MqttBridge(loop)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/config", config_handler)
    app.router.add_get("/style.css", style_handler)
    app.router.add_get("/api/settings", settings_get)
    app.router.add_post("/api/settings", settings_post)
    app.router.add_get("/api/audio-devices", audio_devices_get)
    app.router.add_get("/api/keybinds", keybinds_get)
    app.router.add_post("/api/keybinds", keybinds_post)
    app.router.add_get("/api/pairing-info", pairing_info)
    app.router.add_get("/api/devices", devices_get)
    app.router.add_post("/api/devices", devices_post)
    app.router.add_get("/api/env", env_get)
    app.router.add_post("/api/env", env_post)
    app.router.add_get("/api/log", log_get)
    app.router.add_post("/api/log/terminal", log_terminal)
    for name in ASSET_DIRS:
        app.router.add_static(f"/assets/{name}/", os.path.join(ROOT, "assets", name))
    app.router.add_get("/", lambda r: web.FileResponse(os.path.join(WEB_DIR, "index.html")))
    app.router.add_static("/", WEB_DIR)
    return app


REMINDERS_DIR = os.path.join(ROOT, "data", "reminders")
REMINDERS_TOPIC = "jarvis/web/reminders"


def read_reminders():
    """Upcoming reminders from data/reminders (the service keeps them as files, not on a topic)."""
    items = []
    try:
        names = os.listdir(REMINDERS_DIR)
    except OSError:
        return items
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(REMINDERS_DIR, name), "r", encoding="utf-8") as f:
                data = json.load(f)
            items.append({"id": name[:-5], "text": data.get("text") or data.get("message") or "",
                          "time": data["time"], "time_created": data.get("time_created")})
        except Exception:
            continue
    items.sort(key=lambda r: r["time"])
    return items


async def watch_reminders(bridge):
    """Publishes the reminder list to the page whenever the folder's contents change."""
    previous = None
    while True:
        current = await asyncio.to_thread(read_reminders)
        if current != previous:
            previous = current
            text = json.dumps({"reminders": current})
            bridge.last[REMINDERS_TOPIC] = text
            bridge._broadcast(json.dumps({"t": REMINDERS_TOPIC, "p": text}), REMINDERS_TOPIC)
        await asyncio.sleep(1.0)


async def main():
    loop = asyncio.get_running_loop()
    app = build_app(loop)
    runner = web.AppRunner(app, access_log=access_log())
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    app["bridge"].start()
    watcher = asyncio.create_task(watch_reminders(app["bridge"]))
    print(f"[WEBUI] dashboard on http://{HOST}:{PORT}/", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        watcher.cancel()
        app["bridge"].stop()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
