# Smart devices: colour button, timed on/off, LSC Smart Connect plug — plan

Status: **1. Colour button is built (2026-11-21). 2. The LSC plug is built in code (2026-11-25) and waits for a live check with the owner's key; 3 is a plan only.** Tracked in `docs/todo.md`.

**Customisable colour swatches (2026-11-25):** right-click one of the 8 colour swatches (not the whites: they are temperatures) in the picker, web or Qt, and a colour wheel opens (hue round from the right, saturation from the middle, full value; brightness stays the picker's slider, because a bulb is told its brightness separately). Dragging recolours the bulb live (the first colour at once, then at most one message every 120 ms, and the colour the finger lifts on always goes) and the swatch changes with it; releasing saves it, Reset puts the default back. It is stored in `core.json` `settings.light_swatches` as `{slot name: [r, g, b]}` (only changed slots), through the allow-listed `/api/settings` key `light_swatch.<name>` (`clWebSettings`: three whole numbers 0-255 or null; the Qt widget writes through the same `clWebSettings.apply`), so both UIs, every window and both machines (after a copy of `core.json`) agree. A default swatch still sends its voice colour name; a changed one sends its `rgb`, so it is no longer "the red" of voice (the slot keeps its default name). Under the wheel are red, green and blue sliders (0-255, each track from 0 to full of its channel with the other two as they are), so any mix can be set, also a darker one; a Tapo bulb takes a hue, a saturation and a brightness, so how dark a mix is becomes the brightness (`utils/clColour.py` `dark_brightness`, and the same rule in `swatch-model.js`: the brightest channel / 255 to the power `light_colour_gamma`, 1-100; the service applies it to any explicit rgb that is not at full value, and the sliders also move the brightness slider, 100 % at 255); a voice colour name keeps its brightness; they follow the wheel and the wheel follows them, and a drag is rate limited and kept on release like the wheel's. Wheel maths: `web/js/swatch-model.js` (tests `tests/test_webSwatches.py`) and `src/ui/clColourPicker.py` (same formulas, `tests/test_clColourPicker.py`).

**As built (LSC plug):** `src/utils/clTuya.py` (tinytuya, one short session per call: connect, act, close; a lock per device; on/off/status retried once after a random pause, a toggle never; the toggle reads and writes in one session). `clControl.py`: a device entry has `kind` (`light` by default, or `plug`) and `type: "tuya"`; `_resolve_targets` leaves plugs out of "all"/"everything" and a generic "the light" never lands on a plug through the sticky last target (an empty target, "turn it off", still does); colour, brightness or a brightness step asked of a plug is a `LightCommandError` before anything is touched; the key is `TUYA_KEY_<NAME>` from `.env` (`tuya_key`); `poll_light_status` reads a plug at most every 45 s (`PLUG_POLL_INTERVAL`) unless a command or a refresh asked, and every status now carries `kind`. The web row shows "· Plug" and no colour disc; the Qt widget hides the colour button the same way. **Not built yet:** the Tuya branch of discovery (`tinytuya.deviceScan`) and the Settings add-device form: a plug is added by hand to `config/devices.json`, e.g. `"heater": {"type": "tuya", "kind": "plug", "ip": "192.168.1.60", "device_id": "bf...", "version": "3.5"}` under the Wi-Fi network, with `TUYA_KEY_HEATER=...` in `.env`. The wizard's own files (`tinytuya.json` holds the API secret, `devices.json`, `snapshot.json`) are gitignored at the repo root.

**As built (colour button):** a popup window (`src/ui/clColourPicker.py`, opened from a round `ColourButton` on each row), not an inline drawer, because it can overhang the small widget; 8 colours (red, orange, yellow, green, cyan, blue, purple, hot pink) and 3 whites (warm white, daylight, coolwhite), all names from `config/entities.json` (a test keeps them in step); brightness and white-temperature sliders send once they settle (200 ms). `light.set` gained `rgb` (array) and `temp` (integer) in `actions.json`, and `ActionRouter` only passes schema fields, so `temp` had been silently dropped before. `light_status` now carries `brand`, `colour`, `brightness`, `rgb`, `kelvin` per light. A light's `colour: false` in `devices.json` hides its button; otherwise WiZ is detected from the bulb (`bulbtype.features.color`), Tapo from `TAPO_MODEL` (l510/l520/l610 are white-only) and an unknown light gets the button. Not built: the hue strip for any colour (second version).

Three requests, one rule: **everything is controlled the way every IoT device is today**: the dashboard, voice and automation all send an MQTT action through `ActionRouter` (`light.set` -> `home/room/all/set`), `clControl.py` is the only process that talks to hardware, devices live in `config/devices.json` under their Wi-Fi network, and it is local-first (home network only; away from home they show offline, exactly like the bulbs). No phone app, no vendor cloud at runtime.

## What exists (checked 2026-11-21)

- `clControl.py` `LightManager`: brand is `type` in `devices.json` (`wiz` | `tapo`), branched with `if wiz ... else tapo` in `control_bulb`, `adjust_brightness`, `poll_light_status` and discovery. Colours go in **by name** only (`color_matrix` from the entities file, rgb or temp); WiZ takes an rgb tuple, Tapo is converted to hue/saturation.
- `LightControlWidget` (`src/ui/clLightControlWidget.py`): a row per light (status dot, toggle, delete) plus "Toggle All Off"; no colour control.
- `light.set` schema (`config/actions.json`): `action`, `light_target`, `silent`, `target_str`, `color`, `lum`. `light_target` is a free string matched fuzzily against device names in `_resolve_targets`.
- `clAutomation.py`: pure `AutomationEngine` (events and clock ticks in, `outbox` of actions out) with retry-on-error and a deferred "off" after a delay. Scheduled triggers are already named as its next step (`docs/context/automation.md`).
- Heater plug: **LSC Smart Connect** (sold by Action). It is a Tuya device with a re-skinned app, so it speaks the Tuya protocol on the LAN.

## Order of work

1. **Colour button** (small; no unknowns).
2. **LSC plug** (medium; you set up the Tuya account while 1 is built). It also introduces `kind: plug`, which the schedules need.
3. **Timed on/off** (medium). Changed from my first suggestion (schedules before the plug): the heater is the schedule that needs a fail-safe, and it is cleaner to build that knowing the plug's on/off contract.

---

## 1. Colour button

**UI** (`LightControlWidget`): a colour button on each row whose device can do colour (hidden for white-only bulbs and plugs). It opens a small popover (a styled frame, not `QColorDialog`, which is a native window that ignores the skin):

- a grid of ~12 swatches taken from the colour matrix, so what you click is what voice can say ("red", "purple"...);
- two white swatches (warm, cool) plus a temperature slider;
- a hue strip + brightness slider for any colour (v2 if the swatches feel limiting).

Clicking sends `light.set` with `action: "on"`, `light_target: <name>`, `color` (a name) or `rgb` (`[r, g, b]`) and `lum`. The row's button shows the current colour (optimistic, then the status poll confirms).

**Backend**: add `rgb` (array of 3 integers) to the `light.set` schema; `control_bulb` takes it next to `color`; `_execute_wiz_target` already builds an rgb tuple and `_execute_tapo_target` already converts rgb to hue/saturation, so the colour-name lookup becomes "name or explicit rgb". `poll_light_status` adds the current rgb/temperature to the status payload so the button can show it.

**Capability**: not every Tapo/WiZ bulb has colour. Record `colour: true|false` per device (set from the model at discovery, overridable in `devices.json`); the UI hides the button when false.

**Gotchas**: swatch colours are real colours and must not follow the skin. The Ultron skin recolours warm hues, so paint them with raw `QColor` from `PyQt6.QtGui` (with a comment, like the Ultron head) instead of `clSkinQt`. No `setParent(None)`; hide + `deleteLater` the popover.

**Tests**: row shows the button only for colour lights; a swatch click dispatches the exact payload; `rgb` validation (3 ints, 0-255); WiZ/Tapo get the right values (fakes, no network); status payload carries colour; popover closes on outside click.

---

## 2. LSC Smart Connect plug (Tuya, local)

### Setup you do once per plug (docs go in `docs/context/cross-platform.md` or a new `docs/context/devices.md`)

1. The plug is paired in the LSC Smart Connect app. Home Assistant users report that the LSC app login does **not** link to a Tuya developer project, and the usual route is to move the devices to the **Smart Life** app (same cloud, different skin). Moving means re-pairing, which **changes the local key**.
2. Free Tuya IoT developer account, create a cloud project, link the Smart Life account by QR code (Devices -> Link Tuya App Account).
3. `python -m tinytuya wizard` prints each device's `id`, `key` (the local key), `ip` and `version` (newer LSC plugs 3.5, older 3.3). Delete the wizard's output files afterwards.
4. Put the key in `.env` (`TUYA_KEY_<NAME>=...`; gitignored, like `SPOTIPY_*`/`TAPO_*`), never in `devices.json`, which is tracked. Copy it to the other machine by hand.

### Device model

```jsonc
// config/devices.json -> networks.<ssid>.heater
{ "type": "tuya", "kind": "plug", "ip": "192.168.1.60", "mac": "...", "device_id": "bf...", "version": "3.5" }
```

`kind` is new: `light` (default, so existing entries are unchanged) or `plug`. `type` stays the brand/protocol, `kind` says what the UI and "all lights" may do with it.

### Backend

- New `src/utils/clTuya.py`: a thin async wrapper over `tinytuya` (`set_power`, `get_power`; blocking calls in `asyncio.to_thread`). **Connect, send, close every time** (no persistent socket): a Tuya device accepts one local session at a time, and both machines run `clControl`, as does the phone app's LAN mode. Timeout ~3 s, one retry with a small jitter, then `LightCommandError` like any other device. Switch is data point 1 on a socket; if a model differs, a `dps` override key in the device entry.
- `clControl.py`: a third branch in `control_bulb` (on/off/toggle only; asking a plug for colour or brightness is a clear error, not a silent no-op), `poll_light_status` (on/off/offline; poll less often than bulbs, ~30 s, to keep sessions rare), and `discovery_mode` (`tinytuya.deviceScan` finds ip/id/version by UDP broadcast without keys; the key is still typed in).
- `_resolve_targets`: "all"/"all lights"/"everything" **excludes `kind: plug`**; a plug is only hit when named ("heater", "the heater"). So "turn off all the lights" can never switch the heater off, and "turn on everything" does not turn it on. Decide at implementation whether "everything" should include plugs (I would say no).
- `adjust_brightness` skips plugs. `last_target` stickiness works as for lights ("turn it off" after "turn on the heater" means the heater).
- `requirements.txt`: `tinytuya` (pure Python plus crypto; check it installs the same on Windows and Linux, `sys_platform` markers only if not).

### UI

A plug row: status dot, an on/off toggle and delete, plus a plug icon; no colour button. "Toggle All Off" skips plugs. Settings -> Lights: the add-device flow gets the `tuya` type with fields device id, version and key (key written to `.env` through the existing `_update_env`). The widget title stays "Smart Lights" for now (rename to "Smart Home" is optional).

### Voice

`light_target` is a free string slot, so "turn off the heater" may already route to `light.set` with `light_target: heater` and work without retraining. **Verify with `benchmark_slm.py` and live**; if the SLM does not pick it up, add the phrasing to `config/intents.json` and retrain. **Batch that retrain with the colour-command retrain** (`docs/todo.md`, voice section) so the models are trained once (`training/CLAUDE.md`).

### Tests (never the network)

A fake `tinytuya` device: on/off/toggle; session closed after every call; timeout -> `LightCommandError`; plug refuses colour/lum; "all lights" excludes plugs but "the heater" matches; status poll on/off/offline; `kind` defaults to `light` for old entries; keys come from `.env`, never `devices.json`; discovery parses a fake scan.

### Risks

- A key changes whenever the plug is re-paired: the plug shows offline until the new key is entered. Say so in the error text ("check the local key").
- Away from home the plug is offline (same as lights). Outside-the-house control would need the vendor cloud or a VPN, which is out of scope here.
- Local sessions collide if the phone app, the desktop and the laptop talk at the same moment: short sessions + one retry keep it to a rare, brief "offline".

---

## 3. Timed on/off (schedules)

**Where**: `clAutomation.py`, as the "scheduled triggers" its doc names. The rule shape stays "trigger + conditions + actions", so presence rules and schedules share the outbox, the retry logic and `light.set`.

```jsonc
// core.json -> settings.automation_settings.schedules (list; every key but time/targets/action optional)
{ "id": "heater-morning", "name": "Heater morning", "enabled": true,
  "time": "07:00", "days": ["mon","tue","wed","thu","fri"],
  "targets": ["heater"], "action": "on",
  "color": null, "lum": null,
  "max_on_minutes": 120, "grace_minutes": 10 }
```

**Engine**: `tick(now)` fires each rule once per matching day at/after `time`, remembers `last_fired` (date) so a restart in the same minute does not repeat it. Clock is injected (tests never sleep). Re-read on every tick like the other automation settings, so edits apply without a restart.

**Missed triggers**: if the machine was off or asleep at the time, fire on the next tick **only within `grace_minutes`**; later than that it is skipped, so a missed 07:00 heater-on never switches the heater on at 15:00.

**Heater fail-safe (required for plugs)**: any schedule that turns a `kind: plug` device on must have `max_on_minutes` (the engine refuses to load a plug "on" rule without one, default 120). On sending the "on" it schedules a deferred "off" (the same mechanism as the leave delay) and **persists it** (`data/automation_state.json`, gitignored) so a restart still turns the device off at the due time, or immediately if already past it. It also cancels the deferred off if an explicit schedule "off" fires first.

**Two machines**: both run the ecosystem, so both would fire the same schedule (harmless for idempotent on/off but it doubles the Tuya sessions and retries). `core.json` is per-machine, so schedules are enabled only on the machine that is normally on (`automation_settings.schedules_enabled`, default true; set false on the other). Document it.

**Manual override**: not tracked today (a light you switch yourself is still switched by the schedule). v1 documents it; a later step: a schedule is skipped if the device was changed by hand within N minutes.

**UI**: a clock button on each light/plug row opens a small editor (time, weekdays, on/off, optional colour/brightness for lights, max-on for plugs) listing that device's schedules; writes `automation_settings.schedules` through the same atomic update the Presence tab uses. The Settings Presence/automation area can list all schedules in one place later.

**Voice** ("turn the heater on at seven"): out of scope for v1 (needs intents and a retrain; batch with the retrain above).

**Tests**: due/not due by weekday and time; fires once per day; restart does not repeat; grace window (missed within -> fires, beyond -> skipped); plug rule without `max_on_minutes` is rejected; deferred off fires and survives a restart (state file in `tmp_path`); explicit off cancels it; the outbox carries `light.set` with the right names; a failed command retries (existing logic) and a stale retry for an "on" is dropped when the "off" is due.

---

## Open questions (none block step 1)

1. Should "everything"/"all devices" include plugs? (Recommendation: no.)
2. Is the heater plug an energy-meter model? If it reports power, a later step shows watts in its row (Tuya data points for current power) and could cut the heater if it draws nothing/too much.
3. Default `max_on_minutes` for the heater (2 hours?) and whether the manual toggle in the UI also gets an auto-off.
