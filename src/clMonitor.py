import asyncio
import logging
import json
import time
from bleak import BleakScanner
from typing import Dict, Any
import paho.mqtt.client as mqtt_client

import sys, os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..' if 'src' in __file__ else 'src'))
from utils.clLogging import setup_logging
setup_logging('MONITOR')

class PresenceMonitor:
    def __init__(self):
        self.tracked_devices: Dict[str, Dict[str, Any]] = {}
        self.is_present: bool = False
        self.room_threshold = -60
        self.exit_threshold = -70

        # --- PERSISTENT MQTT CONNECTION ---
        self.mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
        try:
            self.mqtt.connect("localhost", 1883, 60)
            self.mqtt.loop_start()  # Starts the background network thread
            logging.info("Connected to persistent MQTT bus.")
        except Exception as e:
            logging.critical(f"Failed to connect to MQTT Broker: {e}")

    def _trigger_light(self, action: str):
        try:
            payload = json.dumps({"action": action})
            self.mqtt.publish("home/room/desk_light/set", payload)
        except Exception as e:
            logging.error(f"MQTT Publish Failed: {e}")

    async def _monitor_loop(self):
        def callback(device, adv):
            mac = device.address
            rssi = adv.rssi
            if rssi > self.exit_threshold:
                self.tracked_devices[mac] = {"rssi": rssi, "last_seen": time.time()}

        async with BleakScanner(detection_callback=callback) as scanner:
            while True:
                await asyncio.sleep(2)
                now = time.time()
                
                # Logic: Check if any device exceeds the threshold
                active = any(d["rssi"] >= self.room_threshold for d in self.tracked_devices.values())
                
                if active and not self.is_present:
                    logging.info("Proximity detected. Lights ON.")
                    self._trigger_light("on")
                    self.is_present = True
                elif not active and self.is_present:
                    logging.info("Presence lost. Lights OFF.")
                    self._trigger_light("off")
                    self.is_present = False

                # Cleanup stale devices
                self.tracked_devices = {m: d for m, d in self.tracked_devices.items() 
                                      if (now - d["last_seen"]) < 60}

    def run(self):
        try:
            asyncio.run(self._monitor_loop())
        except KeyboardInterrupt:
            logging.info("Shutting down Presence Monitor.")
        finally:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()

if __name__ == "__main__":
    monitor = PresenceMonitor()
    monitor.run()