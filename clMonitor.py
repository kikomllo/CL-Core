import argparse
import os
import asyncio
import time
import logging
import json
import sys
from bleak import BleakScanner
from bleak.exc import BleakError
from dotenv import load_dotenv

import paho.mqtt.publish as publish 

try:
    import clControl 
except ImportError:
    pass

load_dotenv()

# --- CONFIGURATION ---
COMPANY_IDS = {76: "IPHONE", 224: "GOOGLE", 117: "SAMSUNG", 911: "XIAOMI", 6: "MICROSOFT"}
ROOM_THRESHOLD = int(os.getenv("ROOM_THRESHOLD", -60))
EXIT_THRESHOLD = int(os.getenv("EXIT_THRESHOLD", -70))
GRACE_PERIOD_SECONDS = int(os.getenv("GRACE_PERIOD_SECONDS", 40))
EMA_ALPHA = float(os.getenv("EMA_ALPHA", 0.8))
FALLOUT_TIME = float(os.getenv("FALLOUT_TIME", 3.0))
FALLOUT = float(os.getenv("FALLOUT", -0.2))
MAC_MEMORY_SECONDS = int(os.getenv("MAC_MEMORY_SECONDS", 900))

# --- LOGGING SETUP ---
LOG_FILEPATH = os.path.join(os.path.dirname(__file__), "clMonitor.log")

def logger_setup(terminal=True):

    handlers = [logging.FileHandler(LOG_FILEPATH, mode='w')]
    if terminal: handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level = logging.INFO,
        format = "[%(asctime)s] %(message)s",
        datefmt = "%H:%M:%S",
        handlers = handlers
    )

# --- STATE VARIABLES ---
is_user_present = False
tracked_devices = {}
known_macs = {}

# --- NEW SYNCHRONOUS EVENT PUBLISHER ---
def trigger_light(action):    
    clean_action = action[0].replace("--", "") 
    payload = json.dumps({"action": clean_action})
    
    try:
        # Fire and forget instantly. Blocks the loop for <2ms, which is imperceptible to the radar.
        publish.single("home/room/desk_light/set", payload, hostname="localhost")
    except Exception as e:
        print("".ljust(80), end='\r')
        logging.error(f"Failed to publish MQTT message: {e}")

# --- RADAR SCRIPT ---
def detection_callback(device, advertisement_data):    
    global tracked_devices, known_macs
    
    current_mac = device.address
    raw_rssi = advertisement_data.rssi

    # --- UPDATE TRACKED DEVICES ---
    if current_mac in tracked_devices:
        old_rssi = tracked_devices[current_mac]["rssi"]
        
        if raw_rssi > old_rssi:
            new_smooth_rssi = float(raw_rssi) 
        else:
            new_smooth_rssi = (EMA_ALPHA * raw_rssi) + ((1 - EMA_ALPHA) * old_rssi)
        
        tracked_devices[current_mac]["rssi"] = new_smooth_rssi
        tracked_devices[current_mac]["last_seen"] = time.time()
        tracked_devices[current_mac]["packets"] += 1
        
        known_macs[current_mac] = time.time()
        return

    # --- ENTRANCE GATE ---
    is_known = current_mac in known_macs
    
    if raw_rssi >= ROOM_THRESHOLD or is_known:
        matched_ids = set(COMPANY_IDS.keys()).intersection(advertisement_data.manufacturer_data.keys())
        if matched_ids:
            if 76 in matched_ids: 
                data = advertisement_data.manufacturer_data[76]
                if not (data.startswith(b'\x10') or data.startswith(b'\x0c')):
                    return

            tracked_devices[current_mac] = {
                "rssi": float(raw_rssi),
                "last_seen": time.time(),
                "packets": 1
            }
            
            known_macs[current_mac] = time.time()

            brand_name = COMPANY_IDS.get(list(matched_ids)[0])
            
            print("".ljust(80), end='\r') # Clear the terminal line before logging
            if is_known:
                logging.info(f"[+] RETURNING {brand_name} RE-ENTRY: {current_mac} (Picked up early at {raw_rssi} dBm)")
            else:
                logging.info(f"[+] NEW {brand_name} TARGET ENTRY: {current_mac} (Initial: {raw_rssi} dBm)")

# --- RADAR LOOP ---
async def radar_loop():
    global is_user_present, tracked_devices, known_macs

    logging.info("--- PHONE MONITOR - ClMonitor Active ---")
    
    async with BleakScanner(detection_callback=detection_callback, scanning_mode="active") as scanner:
        while True:
            await asyncio.sleep(1)
            current_time = time.time()
            
            # --- PURGE & DECAY CYCLE (Active Tracking) ---
            stale_macs = []
            for mac, data in tracked_devices.items():
                time_since_last = current_time - data["last_seen"]
                
                if time_since_last > FALLOUT_TIME:
                    tracked_devices[mac]["rssi"] += FALLOUT
                
                if time_since_last > GRACE_PERIOD_SECONDS:
                    stale_macs.append(mac)
            
            for mac in stale_macs:
                print("".ljust(80), end='\r')
                logging.warning(f"[!] Target {mac[-5:]} timed out. Dropping from active radar...")
                del tracked_devices[mac]

            # --- VIP MEMORY JANITOR CYCLE ---
            stale_memory = []
            for mac, last_seen in known_macs.items():
                if (current_time - last_seen) > MAC_MEMORY_SECONDS:
                    stale_memory.append(mac)
            for mac in stale_memory:
                logging.warning(f"[!] Target {mac[-5:]} memory timed out. Purging from memory...")
                del known_macs[mac]

            # --- TARGET SELECTION ---
            timestamp = time.strftime('%H:%M:%S', time.localtime())
            
            if not tracked_devices:
                best_rssi = -100.0
                best_mac = None
                print(f"[{timestamp}] [*] Locked: None | tracks: 0 | avgRSSI: - dBm".ljust(80), end='\r')
            else:
                best_mac = max(tracked_devices, key=lambda k: tracked_devices[k]["rssi"])
                best_rssi = tracked_devices[best_mac]["rssi"]
                
                packets = tracked_devices[best_mac]["packets"]
                print(f"[{timestamp}] [*] Locked: {best_mac[-5:]} | tracks: {len(tracked_devices)} | avgRSSI: {best_rssi:.1f} dBm [{packets}]".ljust(80), end='\r')

            # --- LIGHT CONTROL ---
            if best_rssi >= ROOM_THRESHOLD and not is_user_present:
                print("")
                logging.info(f"[+] Proximity Trigger! Lights ON.")
                trigger_light(["--on"])
                is_user_present = True

            elif best_rssi < EXIT_THRESHOLD and is_user_present:
                print("")
                logging.info(f"[-] All targets exited range. Turning OFF.")
                trigger_light(["--off"])
                is_user_present = False

async def main():
    parser = argparse.ArgumentParser(description="CLI Control for Tapo Bulb")
    
    parser.add_argument("-bg", "--background", action="store_true", help="Run without terminal logging.")
    
    args = parser.parse_args()
    
    logger_setup(not args.background)

    while True:
        try:
            await radar_loop()
        except BleakError as e:
            print("".ljust(80), end='\r')
            logging.error(f"[!] Bluetooth Adapter Error: {e}")
            logging.info("[!] Attempting to restart scanner in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print("".ljust(80), end='\r')
            logging.critical(f"[!] Unexpected Fatal Error: {e}")
            logging.info("[!] Attempting to recover in 5 seconds...")
            await asyncio.sleep(5) 

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("")
        logging.info("[!] Stopping script...")