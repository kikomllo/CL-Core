# Presence monitor (`src/clMonitor.py`) — one paired beacon, not "any device"

Read when working on `clMonitor.py`, `utils/clBeacon.py`, the Settings widget's Presence tab, or anything that reacts to whether the user is home.

## What it does
It answers one question — *is my paired device nearby?* — and publishes the answer; it never acts on it (no lights, no greeting yet). Other modules subscribe:

| Topic | Payload |
|---|---|
| `jarvis/sys/presence` (retained) | `{present, rssi, last_seen, since, paired, test_mode, scanning, available, reason, baseline}` |
| `jarvis/sys/presence/event` | `{event: "arrived" \| "left", rssi, ts}` |
| `jarvis/monitor/pairing` | `{state: waiting \| paired \| expired \| cancelled \| failed, code, uri, expires_at, reason}` — **not retained** (it carries the secret while waiting) |
| `jarvis/sys/user_activity` (in, from `clKeybinds.py`) | `{source, ts}` — from `clKeybinds.py` and the dashboard; calibration input, see below |
| `jarvis/monitor/control` (in) | `{action: pair_start \| pair_cancel \| unpair \| status}` |

It replaced an orphaned script that turned lights on for *any* BLE device via a legacy `desk_light` topic. It is now a supervised module (`NATIVE_SERVICES` "Monitor", `modules.json`), publishes `module_ready`, and speaks its own results (`clDaemon.py` treats `monitor.*` actions as self-reporting, like alarms).

## The beacon protocol (`utils/clBeacon.py`)
The beacon advertises BLE **service data** under `BEACON_SERVICE_UUID`: 1 version byte + an 8-byte token = `HMAC-SHA256(secret, floor(unix_time / 30))[:8]`. The token rotates every 30 s and tokens from the previous/next step are accepted (clock skew), so a copied advertisement is useless within ~90 s and can't be forged without the secret. **Identification never uses the MAC address** (phones randomise it). `python src/utils/clBeacon.py payload <CODE>` prints the current service data for a manual advertiser (e.g. nRF Connect) while there is no app.

## Pairing (TOTP-style enrolment — no BLE bonding)
`pair_start` generates a 20-byte secret, publishes it as a grouped base32 **code** (and a `jarvisbeacon://pair?...` URI for a future QR) and opens a 5-minute window. The device is given the code out-of-band (typed/scanned into the app; the future JARVIS app implements the same protocol). The first valid token derived from that secret seen while the window is open completes the pairing: the secret is saved to `data/monitor/paired_device.json` (gitignored, mode 0600), replacing any previous device. Ways to start it: Settings → **Presence** tab (step-by-step instructions, Copy buttons, countdown; its "Test mode" checkbox sends `static: true` and shows the service UUID plus the ready-to-paste fixed service data instead of the code), voice ("pair my phone", intents `monitor_pair/unpair/status` — fast-path only until the SLM is regenerated and retrained), or `python src/clMonitor.py --pair` (`--cancel`, `--unpair`, `--status` too; they talk to the running service over MQTT).

## Presence logic
Only valid-token sightings count. `PresenceTracker` smooths RSSI. **Arrive** as soon as the smoothed signal reaches the enter threshold. **Leave** with a decaying estimate: every packet resets the estimate to its signal, and until the next packet it worsens linearly (`decay_db_per_s`, 0.5 dB/s), so a missing beacon is evidence of distance and a weak last packet crosses the exit threshold sooner than a strong one; you also leave immediately if the smoothed signal itself is below the exit threshold, and after `away_timeout_s` of silence at most (30 s on BlueZ, `away_timeout_fast_s` = 10 s with the raw-HCI scanner). Thresholds are relative to the learned in-room level (below) with fixed fallbacks `enter_rssi` (−75) / `exit_rssi` (−88) until calibrated. Optional overrides in `config/core.json` → `settings.monitor_settings` (`enabled`, `enter_rssi`, `exit_rssi`, `away_timeout_s`, `away_timeout_fast_s`, `enter_margin_db`, `exit_margin_db`, `decay_db_per_s`, `smoothing`, `pairing_timeout_s`, `evaluate_every_s`, `scanner_stall_s`, `unavailable_retry_s`).

## Self-calibrating thresholds (no config needed)
Fixed dBm values differ per phone/PC/house, so the monitor learns the user's in-room signal level. `clKeybinds.py` publishes `jarvis/sys/user_activity` (`{source: "keybind" | "ui", ts}`, rate-limited to one per 5 s, off-thread) whenever a configured keybind/PTT fires, or a real key press / mouse click reaches the dashboard (`ui/clActivity.py`, an app-wide event filter; focus changes are ignored because the UI can trigger them itself) — the only proof the user is at this PC (voice commands are deliberately excluded: a Bluetooth headset works from anywhere). While the paired device is present and was heard in the last 5 s, `on_activity` stores its current RSSI in `RoomBaseline` (`data/monitor/room_baseline.json`, last 200 samples, ≥ 8 needed). The baseline is the *lower quartile* (the weak end of the usual in-room level) and the tracker's thresholds become baseline − `enter_margin_db` (15) / `exit_margin_db` (21), clamped to sane dBm ranges; until calibrated the fixed `enter_rssi`/`exit_rssi` apply. A new pairing or unpairing clears the samples (a different phone has a different level). The presence payload carries `baseline` (null until calibrated) and the log prints "Calibrated: in-room signal level …" once. Not done: transmit power in the beacon payload (for the future app).

**Simulation notes** (real tracker, assumed noise/loss model, `/tmp` scripts — not in the repo): a time-windowed median/mean instead of the EMA gained nothing clear, so the EMA stays. The decay rate trades false departures in weak in-room spots against leave speed: derived 2.1 dB/s gave ~170 false leaves/h at −82 dBm (1 packet/s) vs ~9/h at 0.5 dB/s, at the cost of ~4 s slower walking out of range and ~2x slower leaving to −88 dBm. A wider arrive/leave gap (10 dB) made leaving to −88 dBm nearly impossible, so the gap stays 6 dB. Arriving is unaffected by all of these.

## Reception rate
The monitor counts valid beacon packets over a 10 s window (`packets_per_s` in the presence payload) and logs "Reception: X beacon packets/s (Y/s from all nearby devices)" every 30 s while scanning a paired device — the way to tell a weak radio link from a phone that isn't sending. A 250 ms beacon sends ~4/s; hearing far fewer means reception, not the phone.

**Why there are two scanners.** Through BlueZ the dev machine (Realtek dongle, BlueZ 5.85) hears the 250 ms beacon only ~once per 10 s (gaps up to ~12 s) whatever `DuplicateData` says: the controller/kernel filter duplicates. A raw HCI scan with duplicate filtering off hears ~1.2 packets/s (gaps ≤ ~2.3 s), and ~15 advertisements/s overall vs ~1.5. That scanner is `utils/clHciScan.py` (stdlib only): it sends the *extended* scan commands (a Bluetooth 5 controller answers the legacy ones with "command disallowed"), needs `CAP_NET_RAW`, and prints one JSON line per beacon packet; `HciBeaconScanner` runs it as a subprocess. `AutoScanner` (the default) tries it first and falls back to BlueZ/bleak once if it can't start (not set up, no permission, adapter refuses, non-Linux). While the fast scanner runs, the silence limit drops to `away_timeout_fast_s` (10); on BlueZ it stays 30 — anything under ~20 s there causes false departures.

**One-time setup (Linux):** the capability goes on a *private copy* of Python, never the system one: `python src/utils/clHciScan.py --setup` prints the commands (`cp` the interpreter to `data/monitor/hci_python`, then `sudo setcap cap_net_raw,cap_net_admin+eip` on it), then restart the monitor. `python src/utils/clHciScan.py --check` (run with that copy) exits 0 if it works. Only that copy runs the helper. Don't scan with BlueZ and raw at once in a test: the adapter refuses ("command disallowed") while bluetoothd is scanning.

## Scanning lifecycle
Scanning (`bleak`) runs **only while a device is paired or a pairing is open**, so an unpaired install never touches Bluetooth. If Bluetooth is off/missing, the module still reports ready, publishes `available: false` + the reason, fails an open pairing with a clear message, and retries every 30 s. A scan that hears no advertisements at all for 90 s is restarted (adapter went away). Linux needs BlueZ running (`systemctl status bluetooth`); the adapter must be powered on.

## Not done / next
- Real-hardware verification (needs a beacon device; a machine can't hear its own adapter's advertisements).
- Reacting to presence (lights, greeting, eco mode) — deliberately left to subscribers of the topics above.
- The intents added to `config/intents.json` are not in the SLM's grammar/dataset yet: run `gen_dataset.py` and retrain to route them via the smart path.
- Windows: `bleak` uses WinRT there; untested on the Windows machine.
