"""The console service: one real shell, as a stream, for the dashboard's terminal (xterm.js in the Claude window, web/js/console.js).

Same rule as every service: topics in, topics out. The shell lives in this process (a separate one from the web server and the dashboard window,
so restarting either does not end a job running in it) and is shared by every window that shows it, like a tmux session.
  jarvis/console/input   raw keystrokes (a plain string, not JSON)  -> written to the shell
  jarvis/console/resize  {"cols", "rows"}                           -> the shell's window size
  jarvis/console/attach  (anything)                                  -> replays the recent output to whoever just opened a terminal
  jarvis/console/close   (anything)                                  -> ends the shell; the next input or attach starts a fresh one
  jarvis/console/out     {"d": text}  /  {"d": text, "reset": true} (a replay)  /  {"exit": code}
  jarvis/console/state   {"alive", "shell"} (retained)
The shell starts when first needed, in the project folder."""
import json
import logging
import os
import sys
import threading
from typing import Any, Callable, Dict, Optional

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
from utils.clPtySession import PtySession

TOPIC_IN, TOPIC_RESIZE, TOPIC_ATTACH, TOPIC_CLOSE = "jarvis/console/input", "jarvis/console/resize", "jarvis/console/attach", "jarvis/console/close"
TOPIC_OUT, TOPIC_STATE = "jarvis/console/out", "jarvis/console/state"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class ConsoleService:
    """`publish(topic, payload, retain=False)` sends an MQTT message; `session_factory(on_output, on_exit, cols, rows)` makes the shell (tests give a fake)."""

    def __init__(self, publish: Callable[..., None], session_factory: Optional[Callable[..., Any]] = None):
        self._publish = publish
        self._factory = session_factory or self._default_factory
        self._session = None
        self._cols, self._rows = 120, 30
        self._lock = threading.Lock()

    @staticmethod
    def _default_factory(on_output, on_exit, cols, rows):
        return PtySession(on_output, on_exit, cwd=ROOT, cols=cols, rows=rows)

    # ---- the shell ----
    def _ensure(self):
        with self._lock:
            if self._session is None or not self._session.alive:
                self._session = self._factory(self._on_output, self._on_exit, self._cols, self._rows)
                self._session.start()
                self._state(True)
            return self._session

    def _state(self, alive: bool) -> None:
        self._publish(TOPIC_STATE, json.dumps({"alive": alive}), retain=True)

    def _on_output(self, data: str) -> None:
        self._publish(TOPIC_OUT, json.dumps({"d": data}, ensure_ascii=False))

    def _on_exit(self, code) -> None:
        self._state(False)
        self._publish(TOPIC_OUT, json.dumps({"exit": code}))

    # ---- messages ----
    def handle(self, topic: str, payload: str) -> None:
        try:
            if topic == TOPIC_IN:
                self._ensure().write(payload)
            elif topic == TOPIC_RESIZE:
                size = json.loads(payload)
                self._cols, self._rows = max(2, int(size["cols"])), max(2, int(size["rows"]))
                if self._session is not None and self._session.alive:
                    self._session.resize(self._cols, self._rows)
            elif topic == TOPIC_ATTACH:
                session = self._ensure()
                self._publish(TOPIC_OUT, json.dumps({"d": session.replay(), "reset": True}, ensure_ascii=False))
            elif topic == TOPIC_CLOSE:
                with self._lock:
                    session, self._session = self._session, None
                if session is not None:
                    session.close()
                    self._state(False)
        except Exception as e:
            logging.error(f"[CONSOLE] {topic} failed: {e}")


def main() -> None:
    import paho.mqtt.client as mqtt
    setup_logging('CONSOLE')
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def publish(topic, payload, retain=False):
        client.publish(topic, payload, retain=retain)

    service = ConsoleService(publish)

    def on_connect(c, userdata, flags, reason_code, properties):
        for t in (TOPIC_IN, TOPIC_RESIZE, TOPIC_ATTACH, TOPIC_CLOSE):
            c.subscribe(t)
        service._state(service._session is not None and service._session.alive)
        c.publish("jarvis/sys/module_ready", json.dumps({"module": "console"}), qos=1)
        logging.info("[CONSOLE] ready.")

    def on_message(c, userdata, msg):
        service.handle(msg.topic, msg.payload.decode("utf-8", "replace"))

    client.on_connect, client.on_message = on_connect, on_message
    client.connect("localhost", 1883, 60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if service._session is not None:
            service._session.close()


if __name__ == "__main__":
    main()
