import asyncio
import logging
import json
import time
from bleak import BleakScanner
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [MONITOR] %(message)s", datefmt="%H:%M:%S")

class PresenceMonitor:
    def __init__(self):
        self.tracked_devices: Dict[str, Dict[str, Any]] = {}
        self.is_present: bool = False
        self.room_threshold = -60
        self.exit_threshold = -70

    def _trigger_light(self, action: str):
        # We assume the MQTT client is created contextually for simplicity, 
        # or you can make a persistent class-level client.
        try:
            import paho.mqtt.publish as publish
            publish.single("home/room/desk_light/set", json.dumps({"action": action}), hostname="localhost")
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
        asyncio.run(self._monitor_loop())

if __name__ == "__main__":
    monitor = PresenceMonitor()
    monitor.run()