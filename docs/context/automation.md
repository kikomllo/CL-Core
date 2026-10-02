# Automation engine (`src/clAutomation.py`)

Read when working on `clAutomation.py`, `settings.automation_settings`, or the Presence tab's "Presence Lights" section.

## What it does
A supervised service ("Automation", `modules.json`, `module_ready` = `automation`) that reacts to events from other services and asks actuators to act. Like everything else it only speaks MQTT: it subscribes to `jarvis/sys/presence/event` (and the retained `jarvis/sys/presence`, once, at startup) and publishes ActionRouter actions (`light.set`) — it never imports the monitor or the light service. `AutomationEngine` is pure logic (events and clock ticks in, `outbox` of `(action_id, kwargs)` out); `AutomationService` does the MQTT and turns the outbox into topics through `ActionRouter.prepare`.

## The one rule today: presence lights
- **Arrived** → the chosen lights turn on, only inside the evening window (19:00 → 07:00). Ignored if the engine already thinks you're home (the monitor restarted) — the retained presence state seeds that at startup, so a restart never causes a false arrival.
- **Left** → after `leave_delay_s` (on top of the monitor's own timeout) the chosen lights turn off, at any time of day. Arriving again inside the delay cancels it.
- Lights are sent as one `light.set` per name with `silent: true`. `clControl` resolves a name to one light, so a "group" is just the list of names.

## Settings — `config/core.json` → `settings.automation_settings`
`enabled` (master switch), `evening_start_hour`/`evening_end_hour` (the window; default to `followup_settings`' 19/7, a range that wraps midnight), and `presence_lights`: `enabled`, `lights` (names as in `devices.json`), `off_on_leave`, `leave_delay_s`. Re-read on every event, so edits apply without a restart. The Presence tab writes the master/light choices; the hours are edited in the file.

## Known limits / next
- `clControl` raises `LightCommandError` when a light can't be reached or refuses a command (the handler then publishes `jarvis/feedback` with `status: "error"`, and `light_target`/`action_cmd` naming which one); it used to log the failure and report success.
- The engine now retries a failed light command: it tracks each light it commanded (`_pending`, keyed by light name) and reacts to `jarvis/feedback`. A `status: "error"` matching a pending command schedules a retry `RETRY_DELAY_S` (5 s) later; three attempts total, then it gives up and logs a warning. A `status: "success"` clears the pending entry. A fresh command for the same light (a new arrive/leave decision) always resets the attempt count rather than continuing an old one, and scheduling a deferred "off" (the leave delay) cancels any retry still pending for an "on" that's now stale.
- `clControl` updates its sticky `last_target` on every named command, so an automatic light change also changes what a bare "turn it off" means afterwards; it also falls back to the default bulb when a name matches nothing. A no-side-effect/strict mode there would make automation safer.
- No manual-override tracking yet (a light you switch yourself is still turned off on leave).
- Only presence triggers exist. Scheduled (time-of-day) triggers, weekday conditions and named groups are the planned next step; keep the rule shape "trigger + conditions + actions" so they slot in.
