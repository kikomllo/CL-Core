# CL-Core to-do list

The one place to see what is still to implement. Updated 2026-11-25. Each item says where its detail lives. **Size**: S = under an hour, M = a day or two, L = several days or more. Tick a box when it is done and move it to "Done recently" at the bottom; when a new plan is written, add it here.

## Owner to-dos (things only you can do)

- [ ] **Revoke the Hugging Face token** that was pasted in chat (S, security). Nothing needs it any more. (`voice_latency_plan.md`)
- [ ] **Live-check the Up Next title fix**: click a row in the queue and confirm the player shows the song that really plays. (S)
- [ ] **Restart the ecosystem once and live-check the web UI work on branch `wip/web-ui-floating-windows`**: pin/unpin a window (no gap, no shift, no growth), "show the logs" / "show my todo list" from the overlay (floats), zoom (Ctrl + / - / 0, Ctrl+wheel over a window, Settings > Display), the overlay orb in the corner, then merge the branch. (S) (`web_ui_plan.md`)
- [ ] **Live-check Start on login** on the desktop and on the laptop: log out and back in, JARVIS should start. (S each)
- [ ] **Re-pick the audio output device** in Settings: the pinned "Altifalantes (Realtek High Definition Audio)" no longer exists, so TTS falls back to the default. (S)
- [ ] **Barge-in live checklist** (say "Hey Jarvis, stop" over a long reply, PTT key, abort key, echo guard). (S) (`voice_latency_plan.md`)
- [ ] **Decide the deleted `data/notes/1789982628692.json`**: it is a tracked file the app deleted; commit the deletion or restore it. (S)
- [ ] **Google Cloud project** (Desktop-app OAuth client, Calendar API, publish as "In production" so tokens do not expire after 7 days). Needed before the calendar. (S-M) (`google_calendar_plan.md`)

## Web front end (`docs/web_ui_plan.md`)

- [ ] **Port the dashboard from PyQt6 to HTML/CSS/JS** served by `src/clWebUI.py` (MQTT over a WebSocket bridge, no broker config). Done so far: bridge, orb, clock, media widget, window manager, dock. Left: a standalone presence view, local line editing + slash popup in the Claude window (it is a pure mirror, no xterm.js needed); tests, and retiring `clUI.py` once the shell (Electron, done: overlay + fullscreen, `shell/`) has been lived with; shell follow-ups: voice/keybind monitor switching, the overlay's option prompts, `ui_lite`. Supervisor wiring is done ("Web UI" in `clJarvis.py`/`modules.json`/`boot.py`). (L)

## Smart devices (`docs/smart_devices_plan.md`, in this order)

- [ ] **LSC Smart Connect plug (Tuya, local)**: built and tested with a fake device (`kind: plug`, `clTuya.py`, `tuya` branch, plug row, "all lights" excludes plugs). Live-checked 2026-11-25 on the owner's heater (on, off, toggle, colour refused; status follows). Left: voice phrasing ("turn off the heater", batch with the colour retrain), the `tuya` discovery scan and the Settings add-device form. (S)
- [ ] **Timed on/off (schedules)** in `clAutomation`: weekday + time rules, grace window for missed triggers, **heater max-on fail-safe** persisted across restarts, editor on the light row. (M)
- [ ] Later: watts in the plug's row if it is an energy-meter model; skip a schedule after a manual change; voice "turn the heater on at seven". (M)
- [ ] **Voice lines for devices that are not lights**: the spoken reply to "turn off the heater" is the light wording ("lights off"). Give plugs their own replies ("heater off" / "switching the heater on"), chosen from the target's `kind` in `clControl`'s feedback or the daemon's reply path, and add plug phrasing to `config/intents.json` (`{light_target}` already routes "the heater"; check `benchmark_slm.py`). **Batch with the colour-command retrain** so the models are trained once (`training/CLAUDE.md`). (M)
- [ ] **Tapo colour correction** (`core.json` `settings.light_colour_gamma`, the owner kept 2.2 on 2026-11-25 after comparing; the code default is 1 = none): show it as a slider in Settings -> Lights, and decide whether 2.2 becomes the default so the laptop matches the desktop (`core.json` is per machine). WiZ bulbs are not corrected yet: judge them by eye first. (S)

## Google Calendar (`docs/google_calendar_plan.md`)

- [ ] **Phase 1, read-only sync**: `clGoogleCalendar.py`, OAuth (loopback + PKCE), cache, merge refactor in `clUtilities`, sync label, Settings tab, tests, setup doc. Primary calendar only. (L)
- [ ] **Phase 2, write-back**: create/edit/delete in Google, new events default to Google, fix the edit path that drops description/end first. (L)
- [ ] **Phase 3, several calendars**: a button on the calendar header to choose shown calendars (own colours) and a calendar switch in the create-event dialog. (M)
- [ ] Phase 4 extras: recurring "this and following", all-day creation, push local events to Google. (M)
- [ ] Later: CL-Core's own accounts as another provider (keep the merge point provider-neutral). (L)

## Spotify

- [ ] **Search widget**: find your playlists and songs by typing, play or queue them (30-minute API check first). (L, 7 phases) (`spotify_search_plan.md`)
- [ ] **Reorder from Up Next**: playlists you own only (the queue cannot be reordered through the API); needs a new permission scope; 30-minute check first; do together with search. (M) (`spotify_reorder_plan.md`)
- [ ] **Lyrics sync from audio** (parked until the web UI rebuild is done): lyrics with no timestamps drift, so sample ~4 s of system audio, run Whisper with word timestamps, match against the known lyrics and store corrected timings in the lyrics cache (`aligned`). Start with plain-only tracks ("Cem anos" by Alcool Club is the test), then an offset check for synced ones. (L, 4 slices) (`lyrics_sync_plan.md`)
- [ ] The Windows media overlay can still show the stale title after a queue jump (only the dashboard is corrected). (S, optional)

## Voice and NLP

- [ ] **Colour commands never match the fast path** ("change the light to red"): `config/intents.json` template + regenerate and retrain both SLMs. **Batch with the heater/plug phrasing** so the models are trained once; audition/train only with other heavy work paused. (M) (`voice_latency_plan.md`, `training/CLAUDE.md`)
- [ ] Adaptive endpoint (shorter wait for short complete commands, ~0.2 s gain, risk of cutting people off); only behind a setting. (S, optional)

## Ultron (`docs/ultron_plan.md`)

- [ ] **Phase 0**: laptop pull, `--profile lite`, laptop baseline measurement. (S)
- [ ] **Voice**: 1.4 model distribution through Hugging Face (deferred), 1.6 laptop gate, tune `light`/`full` by ear with the real voice. (M)
- [ ] **Look (Phase 2)**: tune by eye on the real dashboard; the "evil setup" extras (red-eye orb, etc.); `ui_lite` for the lite machine; then switch `"theme": "ultron"` on. (M-L)
- [ ] **Persona (Phase 3)**: 3.1 `settings.persona` and no "sir"; 3.2 rewrite `responses.json`; 3.3 train the "ultron" wake word (Colab) and make the wake word a setting; 3.4 check the intent model; 3.5 rewrite the reply dataset and retrain the reply model. (L)
- [ ] **Rollout (Phases 4-5)**: `docs/context/ultron.md`, a clear message in `boot.py` when the voice model is missing, run the measurement harness on both machines, switch the checked-in defaults only when both pass. (M)

## Claude bridge (`docs/claude_bridge_plan.md`)

- [ ] **Stop-hook delay / false stalls** (a 60 s wait after the reply was already published); consider a longer stall window while "running Stop hook" shows. (M)
- [ ] Live-verify the untested paths, including the trust prompt `AUTO_ANSWERS` against a real populated repo. (S-M)
- [ ] **Windows crash resilience**: a detached session-host that owns the ConPTY. (L)
- [ ] **Replace the VS Code session** (config-dir cutover, configurable model/permission mode per machine, token-cost measuring). (L)
- [ ] Open decisions: should bridge speech ignore silent mode; permission mode for the dev role; one shared session or separate ones.
- [ ] **Mirror the VS Code session in the ecosystem's Claude terminal** (parked 2026-11-22). Findings: the VS Code panel's `claude.exe` has private stdio pipes and the extension has no submit-prompt command (the `vscode://anthropic.claude-code/open?session=&prompt=` URI only prefills the box), so a true mirror is impossible without terminal mode (`claudeCode.useTerminal`) plus a small extension that calls `terminal.sendText`. What does work: Remote Control (`/rc`) is on for both the dev session and the bridge's Claude (logged in, Claude Pro), and `SendMessage` between them is tested both ways. Options: (a) a forwarding rule in `data/claude_bridge_config/bridge_prompt.txt` so "ask the dev session ..." goes bridge -> dev session -> spoken answer (a relay, one model turn per hop); (b) the read-only transcript mirror (tail `~/.claude/projects/<repo>/<id>.jsonl`) shown in the web UI and spoken; (c) terminal mode + extension for a real two-way mirror. (M)
- [ ] **Two clients resize the one terminal** (the Qt widget sizes the grid to itself, ~62x21; the web window no longer resizes): a shrink leaves the bridge's pyte screen and Claude's redraw out of step (stale lines, dialogs clipped, keys logged but nothing changes). Needs a single-owner sizing scheme or a bridge-side floor; until then close the Qt widget while using the web one. Also: the bridge only republishes its screen on new output, so a closed dialog (login "Press Enter", `/remote-control`) stays on screen until a keypress. (M)

## Other ideas (not started, lower priority)

- [ ] **Notification feed widget** (WhatsApp/Discord/Instagram previews read from OS notifications): 1-hour spike on whether Windows allows it first. (M) (`notification_feed_plan.md`)
- [ ] **Embedded YouTube player** (`mpv` + `yt-dlp` spike first, a web view only if that is not enough). (M) (`embedded_player_plan.md`)

## Tech debt and housekeeping

- [ ] **Start on login** stores absolute paths: after moving the repo, toggle it off and on. Optional: re-write the entry at boot when the path changed. (S)

## Done recently

- [x] Startup cover for the web dashboard (branch `feat/boot-cover`, off `main`): windows and dock stay off the screen, the orb runs alone and big, and a module readout fills until the supervisor's retained `jarvis/sys/boot_state` says online; then everything slides in filled. Left: live-check with a real restart, then merge. (`web_ui_plan.md`)
- [x] Shut down the computer by voice, with a spoken confirmation: an exact phrase, "yes", a 20 s delay that "cancel" stops (`daemon-routing.md`). Left: "restart the computer" (not built; today it restarts a module) and a live check of the real shutdown by the owner. Overnight voice capture (`collect_voice.py capture --unattended`, `training/CLAUDE.md`).
- [x] Customisable colour swatches in the light colour picker (web and Qt): right-click opens a colour wheel that recolours the bulb live and keeps the colour (`core.json` `settings.light_swatches`); Reset per swatch. (`smart_devices_plan.md`)
- [x] Test-suite housekeeping: the Linux-only Bluetooth scanner tests skip on Windows, the bridge env-var test no longer depends on the Claude Code session running it, and "wait cancel that command" / "please stop doing that" now abort (`wait` and `doing` are filler in `clAbort.py`; a phrase with any other content word is still a command).
- [x] Floating (unpinned) windows in the web UI, like the Qt widgets: own always-on-top window, any monitor, also from the overlay; seamless unpin/pin (no gap, exact position, Windows' open animation hidden). (`web_ui_plan.md`)
- [x] Zoom: the whole UI and per window (a factor on the fluid unit: only the content scales, window frames stay as they are); keys, Ctrl+wheel, badge, Settings. (`web_ui_plan.md`)
- [x] Light reliability (one session per Tapo bulb, timeouts that return, self-refreshing status), hidden-console boot on Windows, "restart ui module" restarting the window, overlay 200x200 in the corner and flash-free mode switch, tab-strip scrolling, session restore, to-do trash (`data/todos/.trash`), show-logs/show-todo by voice. (`daemon-routing.md`, `cross-platform.md`, `web_ui_plan.md`)
- [x] Colour button on the Smart Lights rows (popup with 8 colours, 3 whites, brightness and white-temperature sliders; `rgb`/`temp` in `light.set`; colour fields in `light_status`; per-light `colour` override). Not committed yet. Follow-ups: the hue strip for any colour (second version); the same button in the web front end.
- [x] Start on login setting (Windows Startup shortcut, Linux XDG autostart): `c3af532`
- [x] Spotify queue-jump backend and the stale-title fix: `35ec7a8`
- [x] Clickable Up Next rows and drawer state kept across a restart: `b555951`
- [x] Responsive layout and a layout per monitor: `d4a3f54`
- [x] Live Logs widget reads only the tail, condensed by default: `5914868`
- [x] Ultron voice picked by the skin and loudness-matched; dataset audit tools: `75fba4c`, `eaa0991`
- [x] Plans for Spotify search/reorder, notification feed, embedded player: `5e5cd62`
