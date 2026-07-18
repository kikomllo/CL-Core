import sys
import time
import logging
import paho.mqtt.client as mqtt_client

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="\r\033[K[%(asctime)s] [DEBUG] %(message)s", datefmt="%H:%M:%S")

def main():
    # Setup persistent MQTT connection
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
    
    try:
        client.connect("localhost", 1883, 60)
        client.loop_start()
        logging.info("Connected to central MQTT bus.")
    except Exception as e:
        logging.error(f"Could not connect to broker: {e}")
        sys.exit(1)

    print("==================================================")
    print(" JARVIS TEXT DEBUGGER ONLINE")
    print(" Type a direct command (e.g., 'turn on the desk light')")
    print(" NOTE: Do not type 'hey jarvis'. The wake word is bypassed.")
    print(" Press Ctrl+C to exit.")
    print("==================================================\n")
    
    while True:
        try:
            # Blocks and waits for terminal input
            text_command = input("\033[94m[TEXT INPUT] > \033[0m")
            
            if text_command.strip():
                # Publish with QoS 1 to guarantee the Daemon receives it
                client.publish("jarvis/sensor/voice", text_command, qos=1)
                logging.info(f"Injected payload: '{text_command}'")
                
                # Give the system a tiny fraction of a second to process before asking for next input
                time.sleep(0.2)
                
        except KeyboardInterrupt:
            print("\n")
            logging.info("Shutting down text debugger.")
            client.loop_stop()
            client.disconnect()
            sys.exit(0)
        except Exception as e:
            logging.error(f"Injection failed: {e}")

if __name__ == "__main__":
    main()