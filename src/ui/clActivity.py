"""Tells the rest of the ecosystem the user is physically at this PC, from real key presses and
mouse clicks in the dashboard. The presence monitor uses it to calibrate its signal thresholds."""
import json
import threading
import time

from PyQt6.QtCore import QEvent, QObject

ACTIVITY_TOPIC = "jarvis/sys/user_activity"
MIN_GAP_S = 5.0
_INPUT_EVENTS = (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress)


class ActivityReporter(QObject):
    """Application-wide event filter. Only real input counts: focus changes can be triggered by the
    dashboard itself (pop-ups, the tray), so they are ignored."""

    def __init__(self, parent=None, clock=time.time):
        super().__init__(parent)
        self._clock = clock
        self._last = float("-inf")

    def eventFilter(self, obj, event):
        if event.type() in _INPUT_EVENTS:
            self._report()
        return False

    def _report(self):
        now = self._clock()
        if now - self._last < MIN_GAP_S:
            return
        self._last = now
        threading.Thread(target=self._publish, args=(now,), daemon=True).start()

    @staticmethod
    def _publish(now):
        try:
            import paho.mqtt.publish as publish
            publish.single(ACTIVITY_TOPIC, json.dumps({"source": "ui", "ts": now}), hostname="localhost")
        except Exception:
            pass
