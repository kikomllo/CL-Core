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

from aiohttp import web, WSMsgType
import paho.mqtt.client as mqtt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "utils"))

from utils.clActionRouter import ActionRouter  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_WEB_PORT", "8765"))
WEB_DIR = os.path.join(ROOT, "web")
# Only these asset folders are public; assets/ also holds the TTS cache and voice samples.
ASSET_DIRS = ("fonts", "icons", "themes")
CONFIG_PATH = os.path.join(ROOT, "config", "core.json")


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
    return web.json_response(settings)


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
    runner = web.AppRunner(app)
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
