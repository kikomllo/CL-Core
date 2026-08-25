import os
import sys
import json
import logging
import threading
import paho.mqtt.client as mqtt
import subprocess

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    print(f"[TRAY] Missing dependencies (pystray, Pillow). Cannot start. Error: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [TRAY] %(message)s', datefmt='%H:%M:%S')

def create_image():
    # Generate an icon with a 'J' for Jarvis
    width = 64
    height = 64
    color1 = (0, 0, 0)
    color2 = (0, 200, 255)
    
    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)
    
    # Draw a simple circle
    dc.ellipse([8, 8, width-8, height-8], outline=color2, width=4)
    
    # Try to draw a J
    try:
        font = ImageFont.truetype("arial.ttf", 36)
        dc.text((22, 12), "J", fill=color2, font=font)
    except Exception:
        # Fallback if arial is missing
        dc.rectangle([width//2-4, 20, width//2+4, height-24], fill=color2)
        dc.rectangle([width//2-16, height-28, width//2+4, height-20], fill=color2)
        
    return image

class TrayApp:
    def __init__(self):
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.mqtt_client.on_connect = self.on_connect
        self.icon = None

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT Broker.")
        else:
            logging.warning(f"Failed to connect to MQTT Broker. Code: {rc}")

    def on_view_logs(self, icon, item):
        logging.info("Requested View Logs.")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.abspath(os.path.join(base_dir, "..", "logs", "latest_run.log"))
        tail_script = os.path.abspath(os.path.join(base_dir, "tail_log.py"))
        
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", sys.executable, tail_script, log_path])
        else:
            subprocess.Popen(["x-terminal-emulator", "-e", f"tail -f {log_path}"])

    def on_shutdown_ecosystem(self, icon, item):
        logging.info("Dispatching ecosystem shutdown command...")
        try:
            self.mqtt_client.publish("jarvis/sys/manager", json.dumps({"action": "shutdown"}))
        except Exception as e:
            logging.error(f"Failed to publish shutdown command: {e}")

    def on_exit(self, icon, item):
        logging.info("Exiting tray application.")
        self.icon.stop()

    def run(self):
        try:
            self.mqtt_client.connect("localhost", 1883, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            logging.error(f"Failed to connect to MQTT broker: {e}")

        menu = pystray.Menu(
            item('View Live Logs', self.on_view_logs),
            item('Shutdown Ecosystem', self.on_shutdown_ecosystem),
            pystray.Menu.SEPARATOR,
            item('Exit Tray', self.on_exit)
        )
        
        self.icon = pystray.Icon("jarvis_tray", create_image(), "Jarvis Ecosystem", menu)
        
        logging.info("Starting System Tray Icon...")
        self.icon.run()
        
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

if __name__ == "__main__":
    app = TrayApp()
    app.run()
