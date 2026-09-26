# Presence monitor (`src/clMonitor.py`) — one paired beacon, not "any device"

Read when working on `clMonitor.py`, `utils/clBeacon.py`, the Settings widget's Presence tab, or anything that reacts to whether the user is home.

## What it does
It answers one question — *is my paired device nearby?* — and publishes the answer; it never acts on it (no lights, no greeting yet). Other modules subscribe:

| Topic | Payload |
|---|---|
| `jarvis/sys/presence` (retained) | `{present, rssi, last_seen, since, paired, scanning, available, reason}` |
| `jarvis/sys/presence/event` | `{event: "arrived" \| "left", rssi, ts}` |
| `jarvis/monitor/pairing` | `{state: waiting \| paired \| expired \| cancelled \| failed, code, uri, expires_at, reason}` — **not retained** (it carries the secret while waiting) |
| `jarvis/monitor/control` (in) | `{action: pair_start \| pair_cancel \| unpair \| status}` |

It replaced an orphaned script that turned lights on for *any* BLE device via a legacy `desk_light` topic. It is now a supervised module (`NATIVE_SERVICES` "Monitor", `modules.json`), publishes `module_ready`, and speaks its own results (`clDaemon.py` treats `monitor.*` actions as self-reporting, like alarms).

## The beacon protocol (`utils/clBeacon.py`)
The beacon advertises BLE **service data** under `BEACON_SERVICE_UUID`: 1 version byte + an 8-byte token = `HMAC-SHA256(secret, floor(unix_time / 30))[:8]`. The token rotates every 30 s and tokens from the previous/next step are accepted (clock skew), so a copied advertisement is useless within ~90 s and can't be forged without the secret. **Identification never uses the MAC address** (phones randomise it). `python src/utils/clBeacon.py payload <CODE>` prints the current service data for a manual advertiser (e.g. nRF Connect) while there is no app.

## Pairing (TOTP-style enrolment — no BLE bonding)
`pair_start` generates a 20-byte secret, publishes it as a grouped base32 **code** (and a `jarvisbeacon://pair?...` URI for a future QR) and opens a 5-minute window. The device is given the code out-of-band (typed/scanned into the app; the future JARVIS app implements the same protocol). The first valid token derived from that secret seen while the window is open completes the pairing: the secret is saved to `data/monitor/paired_device.json` (gitignored, mode 0600), replacing any previous device. Ways to start it: Settings → **Presence** tab (shows code + countdown + Copy), voice ("pair my phone", intents `monitor_pair/unpair/status` — fast-path only until the SLM is regenerated and retrained), or `python src/clMonitor.py --pair` (`--cancel`, `--unpair`, `--status` too; they talk to the running service over MQTT).

## Presence logic
Only valid-token sightings count. `PresenceTracker` smooths RSSI and uses hysteresis: arrive at ≥ `enter_rssi` (−75), leave at < `exit_rssi` (−88) or after `away_timeout_s` (45 s) of silence; after leaving it must reach `enter_rssi` again. All thresholds are optional overrides in `config/core.json` → `settings.monitor_settings` (`enabled`, `enter_rssi`, `exit_rssi`, `away_timeout_s`, `smoothing`, `pairing_timeout_s`, `evaluate_every_s`, `scanner_stall_s`, `unavailable_retry_s`).

## Scanning lifecycle
Scanning (`bleak`) runs **only while a device is paired or a pairing is open**, so an unpaired install never touches Bluetooth. If Bluetooth is off/missing, the module still reports ready, publishes `available: false` + the reason, fails an open pairing with a clear message, and retries every 30 s. A scan that hears no advertisements at all for 90 s is restarted (adapter went away). Linux needs BlueZ running (`systemctl status bluetooth`); the adapter must be powered on.

## Not done / next
- Real-hardware verification (needs a beacon device; a machine can't hear its own adapter's advertisements).
- Reacting to presence (lights, greeting, eco mode) — deliberately left to subscribers of the topics above.
- The intents added to `config/intents.json` are not in the SLM's grammar/dataset yet: run `gen_dataset.py` and retrain to route them via the smart path.
- Windows: `bleak` uses WinRT there; untested on the Windows machine.
