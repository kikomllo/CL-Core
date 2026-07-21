# CL-Core (JARVIS Smart Home OS)

CL-Core is an asynchronous, local-first home automation and NLP operating system built on an MQTT messaging backbone. It provides localized Natural Language Processing (NLP) to parse voice transcripts and routes JSON payloads to highly decoupled microservices spanning smart lighting, multimedia, OS-level terminal navigation, and proximity automation.

The system utilizes a central supervisor (`clJarvis.py`) for automated deployment, crash recovery, and ecosystem management, while adhering to a strict microservice architecture where every module can be run, tested, and deployed entirely independently.

## Ecosystem Architecture

The system relies on a publish/subscribe model over a local MQTT broker to ensure low latency and strict decoupling between sensors, inference engines, the central brain, and actuators.

1. **The Supervisor (`clJarvis.py`)**: The master orchestrator. It boots the core system modules concurrently, tracks process health, and executes surgical module restarts or full ecosystem reboots via MQTT directives.
2. **The Acoustic Sensor (`src/clMic.py`)**: The primary input node. It handles environmental noise baselining, local wake-word detection (`openwakeword`), Voice Activity Detection (VAD), and streams Base64-encoded audio arrays to the network.
3. **The Inference Engine (`src/clWhisper.py`)**: A dedicated STT microservice. It ingests audio arrays from the network, processes them using hardware-accelerated `faster-whisper`, filters out AI hallucinations, and publishes raw text transcripts.
4. **The Central Brain (`src/clDaemon.py`)**: The core state machine and router. It handles multi-turn conversational states (e.g., awaiting user selection) and uses a custom RapidFuzz-powered `IntentEngine` to extract slots and route actionable JSON payloads.
5. **The Actuators**: Independent execution nodes.
   * **Light Actuator (`clControl.py`)**: Interfaces with WiZ and Tapo smart lights. Features a dual-ecosystem network discovery engine and self-healing logic (auto-recovers lost IPs via MAC address sweeping).
   * **Music Actuator (`clSpotify.py`)**: Handles the Spotify Web API. Features a fuzzy-math confidence engine for track selection, local app wake-up, and audio "ducking" during voice interactions.
   * **Terminal Actuator (`clTerminal.py`)**: Handles OS-level navigation, application launching, web searching, and process termination.
   * **Text-To-Speech (`clTTS.py`)**: Generates dynamic audio responses using `edge-tts` and manages the playback mixer.
6. **The Proximity Sensor (`src/clMonitor.py`)**: A standalone BLE presence radar. It tracks user devices and publishes direct automation commands based on room entrance/exit thresholds.

## Repository Structure

```text
├── clJarvis.py                # Master process supervisor and crash-recovery manager
├── .env                       # Environment variables and secrets
├── config/
│   ├── core.json              # System settings, language rules, and VAD tuning
│   ├── intents.json           # NLP templates and target topics
│   ├── entities.json          # Truth matrices (e.g., RGB/Temp color mappings)
│   ├── system.json            # OS shortcuts, terminal aliases, and folder paths
│   └── responses.json         # Dynamic TTS response templates
└── src/
    ├── clMic.py               # Local VAD and wake-word ingestion
    ├── clWhisper.py           # Decoupled Whisper STT engine
    ├── clDaemon.py            # Core NLP router and state machine
    ├── clControl.py           # WiZ & Tapo smart lighting actuator
    ├── clSpotify.py           # Spotify multimedia actuator
    ├── clTerminal.py          # OS navigation and execution actuator
    ├── clTTS.py               # Edge-TTS voice generation microservice
    ├── clMonitor.py           # BLE presence scanning
    ├── clDebug.py             # CLI text-injection debugger
    ├── nlp/
    │   └── clIntentEngine.py  # RapidFuzz typo-forgiving slot extraction
    └── utils/
        ├── clConfigLoader.py  # Strict JSON Schema validation loader
        └── clEnvLoader.py     # Centralized .env manager
```

## Setup & Installation

### 1. OS-Level Prerequisites & MQTT Broker

The OS requires audio capture drivers and a local MQTT broker (Mosquitto) running in the background.

* **Linux (Debian/Ubuntu):**
  ```bash
  sudo apt update
  sudo apt install mosquitto mosquitto-clients portaudio19-dev python3-pyaudio libasound2
  ```

* **Windows:**
  1. Install Mosquitto Broker via PowerShell / Command Prompt:
     ```powershell
     winget install -e --id EclipseFoundation.Mosquitto
     ```
  2. Open Windows Services (`Win + R` $\rightarrow$ `services.msc`), locate **Mosquitto Broker**, and verify that the service status is **Running**.

---

### 2. Install Python Dependencies

* **Linux Installation:**
  ```bash
  pip install aiomqtt paho-mqtt python-dotenv spotipy tapo pywizlight faster-whisper pyaudio openwakeword numpy bleak rapidfuzz edge-tts jellyfish
  ```

* **Windows Installation:**
  *(Includes Windows-specific OS integration packages: `pyautogui` for Spotify app wake-up and `pycaw` / `comtypes` for master audio volume actuation).*
  ```powershell
  pip install aiomqtt paho-mqtt python-dotenv spotipy tapo pywizlight faster-whisper pyaudio openwakeword numpy bleak rapidfuzz edge-tts jellyfish pyautogui pycaw comtypes
  ```

---

### 3. Environment Variables

Create a `.env` file in the project root directory. The system utilizes centralized environment management for API credentials and self-healing hardware tracking.

```ini
# --- SPOTIFY CREDENTIALS ---
SPOTIPY_CLIENT_ID="your_spotify_client_id"
SPOTIPY_CLIENT_SECRET="your_spotify_client_secret"
SPOTIPY_REDIRECT_URI="[https://www.spotify.com/account/apps/](https://www.spotify.com/account/apps/)"

# --- TAPO SMART HOME ---
TAPO_EMAIL="your_tapo_account_email"
TAPO_PASSWORD="your_tapo_account_password"

# --- LIGHT STATE (Auto-Updated by clControl.py) ---
LIGHT_TYPE="tapo" 
LIGHT_IP="192.168.1.xxx"
LIGHT_MAC="XX:XX:XX:XX:XX:XX"
```

## Service Deployment

The architecture supports automated, supervised deployment, as well as granular debugging.

### Option A: The Automated Supervisor (Recommended)
To boot the entire ecosystem simultaneously, managed by the `clJarvis.py` supervisor (which handles auto-restarts if a module crashes):
```bash
python clJarvis.py
```

### Option B: Independent Microservices
Because components are completely decoupled over MQTT, you can run or restart any module independently for debugging or distributed deployment across multiple machines (e.g., running the Whisper engine on a GPU server, and the Mic on a Raspberry Pi).

```bash
# Terminal 1: NLP Brain
python src/clDaemon.py

# Terminal 2: STT Engine
python src/clWhisper.py

# Terminal 3: Smart Lights Actuator
python src/clControl.py

# Terminal 4: Text Debugger (Bypass the microphone)
python src/clDebug.py
```

## Key Features & Payload Flows

**Self-Healing Network Recovery**
If your smart light changes its IP address via DHCP, the `clControl.py` module will detect the communication failure, automatically sweep the local subnet for the device's MAC address, restore the connection, and permanently update the `.env` file with the new IP.

**Multi-Turn Interaction & "Ducking"**
*User: "Hey Jarvis, play a playlist."*
1. **`clMic.py`** detects the wake word and pauses background noise. It instructs Spotify to "duck" (lower volume by 20%).
2. **`clDaemon`** recognizes a vague intent and flags `awaiting_spotify_choice = True`.
3. **`clSpotify.py`** performs a fuzzy search and returns multiple options to the TTS engine.
4. **`clTTS.py`** reads the options aloud. The microphone window re-opens natively (bypassing the wake-word requirement).
5. User says: *"Option 2"*. The Daemon routes the integer directly to the Spotify actuator, which executes the playback and restores the volume.
