# CL-Core

CL-Core is an asynchronous, local-first home automation and NLP framework built on an MQTT messaging backbone. It provides localized Natural Language Processing (NLP) to parse voice transcripts and route JSON payloads to decoupled microservices spanning smart lighting, media, and proximity automation.

The system utilizes a master launcher (`jarvis.py`) for automated deployment, but adheres strictly to a microservice architecture where every module can be run, tested, and deployed entirely independently.

## Ecosystem Architecture

The system relies on a publish/subscribe model to ensure low latency and strict decoupling between sensors, the brain, and actuators.

1. **The Orchestrator (`jarvis.py`)**: A lightweight subprocess manager that boots the core system modules concurrently.
2. **The Voice Sensor (`clVoice.py`)**: The primary input node. It records audio via PyAudio, runs local offline speech-to-text inference using `faster-whisper`, and publishes raw text to the network.
3. **The Router (`clDaemon.py`)**: The core NLP engine. It ingests raw voice transcripts, performs intent extraction (via hybrid tokenization/compression), and publishes action payloads.
4. **The Actuators (`clControl.py` & `clSpotify.py`)**: Independent edge nodes. `clControl.py` interfaces with TP-Link Tapo smart devices, while `clSpotify.py` handles the Spotify Web API.
5. **The Proximity Sensor (`clMonitor.py`)**: A standalone presence radar. It utilizes `bleak` for Bluetooth Low Energy (BLE) scanning to track user devices and publishes direct automation commands (e.g., lights on/off) based on room entrance/exit thresholds.
6. **The Broker**: A local Mosquitto MQTT broker handles all intra-system routing.
   
## Repository Structure

* **`jarvis.py`** — Master process launcher
* **`clDaemon.py`** — Core NLP router and MQTT publisher
* **`clVoice.py`** — Local STT and voice ingestion sensor
* **`clControl.py`** — Tapo smart lighting actuator
* **`clSpotify.py`** — Spotify Web API multimedia actuator
* **`clMonitor.py`** — BLE presence scanning and proximity automation
* **`routing.json`** — Maps extracted actions to MQTT topics
* **`actions.json`** — Maps language synonyms to standard actions
* **`topics.json`** — Maps voice triggers to system topics
* **`colors.json`** — Hex mappings for smart lighting integration
* **`.env`** — Environment variables and secrets


## Setup & Installation

**1. OS-Level Prerequisites**
Before installing Python packages, your operating system requires audio drivers for `pyaudio` and an MQTT broker.

*Debian/Ubuntu:*
```
sudo apt update
sudo apt install mosquitto mosquitto-clients portaudio19-dev python3-pyaudio
```

**2. Install Python Dependencies**
Install the required packages based on the module requirements.
```
pip install aiomqtt paho-mqtt python-dotenv spotipy tapo faster-whisper pyaudio numpy bleak
```

**3. Environment Variables**
Create a `.env` file in the project root. The system requires credentials for both Spotify OAuth and local Tapo device authentication.

# Spotify Web API Credentials
```
SPOTIPY_CLIENT_ID="your_spotify_client_id"
SPOTIPY_CLIENT_SECRET="your_spotify_client_secret"
SPOTIPY_REDIRECT_URI="https://www.spotify.com/account/apps/"
```

# Tapo Smart Home Credentials
```
TAPO_EMAIL="your_tapo_account_email"
TAPO_PASSWORD="your_tapo_account_password"
```

# Monitor / Radar Thresholds (Optional Defaults)
```
ROOM_THRESHOLD="-60"
EXIT_THRESHOLD="-70"
```

## Service Deployment

The architecture supports both automated and granular deployment strategies.

### Option A: The Automated Orchestrator
To boot the standard ecosystem (Voice, Daemon, Lights, Spotify) simultaneously using the master runner:
```
python jarvis.py
```

### Option B: Independent Microservices
Because components are strictly decoupled, you can run or restart any module independently for debugging or distributed deployment across multiple machines.

**Terminal 1: NLP Brain**
```
python clDaemon.py
```
**Terminal 2: Smart Lights Actuator**
```
python clControl.py
```
**Terminal 3: Voice Sensor**
```
python clVoice.py
```
**Terminal 4: Proximity Sensor**
*Note: The Monitor module is designed to run completely out-of-band and should be started independently.*
```
python clMonitor.py
```

## Example Payload Flows

Because the system is event-driven, actuators respond to payloads regardless of which sensor generated them.

**Flow 1: Voice Command**
*User: "Hey Jarvis, set the desk to blue and play the song Bohemian Rhapsody."*
1. **`clVoice.py`:** Detects wake word, runs Whisper inference, publishes string to `jarvis/sensor/voice`.
2. **`clDaemon.py`:** Ingests string, extracts intents, and routes payloads to `home/room/desk_light/set` and `pc/spotify/control`.
3. **Actuators:** `clControl.py` and `clSpotify.py` receive their respective payloads and execute the hardware/API changes.

**Flow 2: Proximity Automation**
*User walks into the room with their phone.*
1. **`clMonitor.py`:** `BleakScanner` detects the known MAC address breaking the `-60 dBm` RSSI threshold.
2. **`clMonitor.py`:** Immediately publishes `{"action": "on"}` directly to `home/room/desk_light/set`.
3. **`clControl.py`:** Receives the payload and turns the Tapo bulb on without requiring voice interaction.
