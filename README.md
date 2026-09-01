# CL-Core (JARVIS Smart Home OS)

CL-Core is an asynchronous, local-first home automation and NLP operating system built on an MQTT messaging backbone. It provides localized Natural Language Processing (NLP) to parse voice transcripts and routes JSON payloads to highly decoupled microservices spanning smart lighting, multimedia, OS-level terminal navigation, desktop UI, and proximity automation.

The system utilizes a central supervisor (`boot.py` → `clJarvis.py`) for automated environment setup, deployment, crash recovery, and ecosystem management, while adhering to a strict microservice architecture where every module can be run, tested, and deployed entirely independently.

## Ecosystem Architecture

The system relies on a publish/subscribe model over a local MQTT broker to ensure low latency and strict decoupling between sensors, inference engines, the central brain, and actuators.

1. **The Supervisor (`boot.py` → `clJarvis.py`)**: `boot.py` bootstraps and updates the Python virtual environment from `requirements.txt`, takes a single-instance lock, and starts the local Mosquitto broker before handing off to `clJarvis.py`, the master orchestrator. It boots every host service concurrently (per `config/modules.json`), tracks process health, and executes surgical module restarts or full ecosystem reboots via MQTT directives.
2. **The Acoustic Sensor (`src/clMic.py`)**: The primary input node. It handles environmental noise baselining, local wake-word detection (`openwakeword`), Voice Activity Detection (VAD), and streams Base64-encoded audio arrays to the network.
3. **The Inference Engine (`src/clWhisper.py`)**: A dedicated STT microservice. It ingests audio arrays from the network, processes them using hardware-accelerated `faster-whisper`, filters out AI hallucinations, and publishes raw text transcripts.
4. **The Central Brain (`src/clDaemon.py`)**: The core state machine and router. It handles multi-turn conversational states (e.g., awaiting user selection) and uses a custom RapidFuzz-powered `IntentEngine` (`nlp/clIntentEngine.py`) to extract slots and route actionable JSON payloads.
5. **The Action Router (`src/utils/clActionRouter.py`)**: The single translation point from an `action_id` (e.g. `light.set`) to a validated MQTT topic and payload, driven by the `config/actions.json` registry.
6. **The Actuators**: Independent execution nodes.
   * **Light Actuator (`clControl.py`)**: Interfaces with WiZ and Tapo smart lights across multiple saved networks (`config/devices.json`). Features a dual-ecosystem network discovery engine and self-healing logic (auto-recovers lost IPs via MAC address sweeping).
   * **Music Actuator (`clSpotify.py`)**: Handles the Spotify Web API. Features a fuzzy-math confidence engine for track selection, local app wake-up, and audio "ducking" during voice interactions.
   * **Terminal Actuator (`clTerminal.py`)**: Handles OS-level navigation, application launching, web searching, and process termination.
   * **Text-To-Speech (`clTTS.py`)**: Generates dynamic audio responses using `edge-tts` and manages the playback mixer.
7. **The Desktop UI (`src/clUI.py`)**: A PyQt6 dashboard (media, lights, to-dos, reminders, calendar, settings, live logs) that talks to the rest of the ecosystem purely over MQTT — a fully decoupled client, not a privileged process. Paired with a system tray icon (`clTrayIcon.py`) and global OS-level keybindings (`clKeybinds.py`, with optional Linux `evdev` support for background use on Wayland).
8. **Lifecycle & Utility Services**: `clUtilities.py` owns alarms, reminders, to-dos, and calendar events (with natural-language time parsing via `dateparser`), while standalone `clAlarmTrigger.py` / `clReminderTrigger.py` scripts fire scheduled events and can auto-boot the ecosystem if it's offline. `clUpdater.py` handles self-updates over MQTT, and `clHealth.py` is a standalone diagnostic CLI.
9. **The Proximity Sensor (`src/clMonitor.py`)**: A standalone BLE presence radar. It tracks user devices and publishes direct automation commands based on room entrance/exit thresholds.

## Repository Structure

```text
├── boot.py                    # Venv bootstrap, single-instance lock, launches the supervisor
├── clJarvis.py                # Master process supervisor and crash-recovery manager
├── .env                       # Environment variables and secrets
├── config/
│   ├── core.json              # System settings, language rules, and VAD tuning
│   ├── intents.json           # NLP templates and target action_ids
│   ├── actions.json           # action_id -> MQTT topic/payload registry (used by the Action Router)
│   ├── entities.json          # Truth matrices (e.g., RGB/Temp color mappings)
│   ├── devices.json           # Per-network saved smart light IP/MAC/type records
│   ├── modules.json           # Enables/disables individual host services
│   ├── keybinds.json          # Global OS keybinding -> action_id map
│   ├── system.json            # OS shortcuts, terminal aliases, and folder paths
│   └── responses.json         # Dynamic TTS response templates
└── src/
    ├── clMic.py               # Local VAD and wake-word ingestion
    ├── clWhisper.py           # Decoupled Whisper STT engine
    ├── clDaemon.py            # Core NLP router and dialogue state machine
    ├── clControl.py           # WiZ & Tapo smart lighting actuator
    ├── clSpotify.py           # Spotify multimedia actuator
    ├── clTerminal.py          # OS navigation and execution actuator
    ├── clTTS.py               # Edge-TTS voice generation microservice
    ├── clMonitor.py           # BLE presence scanning (standalone)
    ├── clUI.py                # PyQt6 desktop dashboard
    ├── clKeybinds.py          # Global OS-level keybinding listener
    ├── clTrayIcon.py          # System tray icon and quick controls
    ├── clUtilities.py         # Alarms, reminders, to-dos, and calendar backend
    ├── clUpdater.py           # MQTT-driven self-update service
    ├── clHealth.py            # Ecosystem health-check CLI
    ├── clDebug.py             # CLI text-injection debugger
    ├── ui/                    # Dashboard widgets (media, lights, todo, reminders, calendar, settings, logs)
    ├── nlp/
    │   └── clIntentEngine.py  # RapidFuzz typo-forgiving slot extraction
    └── utils/
        ├── clConfigLoader.py  # Strict JSON Schema validation loader
        ├── clActionRouter.py  # action_id -> MQTT topic/payload dispatcher
        ├── clEnvLoader.py     # Centralized .env manager
        └── clRedisClient.py   # Pooled Redis client (state persistence, in migration)
```

## Setup & Installation

### 1. OS-Level Prerequisites & MQTT Broker

The OS requires audio capture drivers and a local MQTT broker (Mosquitto) installed. `clJarvis.py` will attempt to start it automatically on boot (`mosquitto -d`), so it doesn't need to be running as a separate background service beforehand on Linux.

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

The easiest path is to let the supervisor handle it — `python boot.py` creates a virtual environment and installs everything from `requirements.txt` automatically. To do it manually instead:

```bash
pip install -r requirements.txt
```

* **Windows only:** `requirements.txt` is cross-platform, so a couple of Windows-specific OS integration packages need to be installed separately — `pycaw` / `comtypes` for master audio volume actuation (`pyautogui`, used for Spotify app wake-up, is already included).
  ```powershell
  pip install pycaw comtypes
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

# --- REDIS (state persistence) ---
REDIS_HOST="localhost"
REDIS_PORT="6379"
REDIS_DB="0"
```

You only need to hand-edit this file once, to bootstrap it. After that, every credential above except the Redis connection settings — `LIGHT_TYPE`/`LIGHT_IP`/`LIGHT_MAC`, `TAPO_EMAIL`/`TAPO_PASSWORD`, and the `SPOTIPY_*` keys — can be viewed and updated from the **Settings** tab in the UI's **Fullscreen Mode** (`SettingsWidget`, only available while fullscreen). Changes there write straight back to `.env` via `clEnvLoader`, so there's no need to edit the file by hand again — though since each microservice only reads `.env` once at its own startup, a changed credential still needs an ecosystem restart (or a targeted module restart) to take effect for the actuator that uses it.

## Service Deployment

The architecture supports automated, supervised deployment, as well as granular debugging.

### Option A: The Automated Supervisor (Recommended)
To boot the entire ecosystem simultaneously — venv setup, the MQTT broker, every microservice, and the desktop UI — managed by the `clJarvis.py` supervisor (which handles auto-restarts if a module crashes):
```bash
python boot.py
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

## Default Keybinds

`clKeybinds.py` listens globally at the OS level (with optional Linux `evdev` support for background use on Wayland) and maps each combo below to an `action_id`, dispatched through the same **Action Router** as every other command. Bindings are defined in `config/keybinds.json` and can be remapped there.

* **`Ctrl+Alt+Shift+A`** — Abort. Cancels whatever the Daemon is currently doing (an in-progress multi-turn dialogue, an active alarm challenge, etc). *Single*: fires once per press.
* **`Ctrl+Alt+Shift+F`** — Switch the desktop UI to **Fullscreen Mode**. *Single*.
* **`Ctrl+Alt+Shift+O`** — Switch the desktop UI to **Overlay Mode** (the compact, always-on-top HUD). *Single*.
* **`Right Alt` (hold)** — Push-to-talk. Opens the microphone for as long as the key is held, bypassing wake-word detection. *Continuous*: unlike the single-fire binds above, this dispatches a distinct action on press (`mic.ptt_start`) and on release (`mic.ptt_stop`) rather than firing once.

## Key Features & Payload Flows

**Self-Healing Network Recovery**
If a smart light changes its IP address via DHCP, the `clControl.py` module will detect the communication failure, automatically sweep the local subnet for the device's MAC address, restore the connection, and persist the new IP to `config/devices.json` (per network, supporting multiple lights) as well as mirroring the primary light back to `.env`.

**Multi-Turn Interaction & "Ducking"**
*User: "Hey Jarvis, play a playlist."*
1. **`clMic.py`** detects the wake word and pauses background noise. It instructs Spotify to "duck" (lower volume by 20%).
2. **`clDaemon`** recognizes a vague intent and flags `awaiting_spotify_choice = True`.
3. **`clSpotify.py`** performs a fuzzy search and returns multiple options to the TTS engine.
4. **`clTTS.py`** reads the options aloud. The microphone window re-opens natively (bypassing the wake-word requirement).
5. User says: *"Option 2"*. The Daemon routes the integer directly to the Spotify actuator, which executes the playback and restores the volume.
