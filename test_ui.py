import paho.mqtt.publish as publish
import json

publish.single("jarvis/sys/ui_control", json.dumps({"action": "set_fullscreen"}), hostname="localhost")
