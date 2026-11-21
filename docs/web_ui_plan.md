# Web front end plan

Why: Qt's QSS fights us (smooth-border engine, skin recolouring, scaler injector, responsive layout hacks). A web UI gets rounded borders, CSS variables for skins, grid/rem layout, canvas/SVG/WebGL and `xterm.js` for free.

## Architecture
- `src/clWebUI.py` (aiohttp, 127.0.0.1:8765): serves `web/` and `assets/{fonts,icons,themes}`, and bridges one paho connection to a WebSocket `/ws`. Frames: client sends `{sub:[topics]}`, `{act:"spotify.control", k:{...}}` (an `actions.json` action, validated by `ActionRouter.prepare`), `{pub:topic, p:payload}`; server sends `{t:topic, p:text}` and replays the last message per topic to a new tab. Cross-origin WebSockets are refused. The system Mosquitto config is not touched (needs admin).
- `web/` is plain ES modules, no build step. `js/bridge.js` (topics in/out), `js/state.js` (orb state machine, ported from `MqttThread.evaluate_state`), `js/orb.js`, `js/clock.js`, `js/skin.js`, `js/ui.js` (icons + draggable/resizable windows, layout saved per window as fractions of the viewport), one `js/<widget>.js` per widget, `js/main.js` (dock + start-up). `--u` is the fluid unit (`min(long/1920, short/1080)`); every size is `calc(N * var(--u))`.
- Run: `python src/clWebUI.py`, open `http://127.0.0.1:8765/` (`?open=media` opens a widget at start).

## Design sources (claude.ai artifacts; the web UI is built from these, not from the Qt look)
- Media Widget Concept `54d1885c-0c6a-4780-b2de-d1fea000a661` (card chrome, hover fader, App Volume rows), Media Widget Redesign `03f758a2-4e10-4700-898c-a1acbf5c08ad` (badge + shuffle/repeat, seek, queue, three circles).
- Dashboard Clock `c93ffcaa-4423-43b1-86fb-b79386789a71`, Widget Dock Row `e1a5c254-33ba-45f0-b8b0-428903733cb8` (capsule pills that grow a label, command bar), Visualizer Glow Concept `d3e3a50f-396b-4601-a2e5-bb627ad5e314` (glow on the lines), Ecosystem Palette Preview `fc531fe9-29ec-4b1a-a880-452649e6f1eb` (tokens), Lyrics Revolver `b0680b33-33f6-46f7-8e77-327055fa37b4`.
- Ported from the `Brand*` module boards (Lights/To-Do/Notes) with the Qt widgets' data flow: `web/js/lights.js` (with a colour popover; the Light Colour Button artifact is still to compare), `todo.js`, `notes.js`, `reminders.js` (clWebUI publishes `jarvis/web/reminders` from `data/reminders`, since the service keeps them as files).
- Still to read when porting: Calendar Drawer Designs `1a444e04-2ee9-41cf-9b60-6d6a756589c0` (the chosen one is "U3", `CurrentU3.dc.html`; Lights*/Todo*/Notes* boards too), Ultron Console `93bb5ea1-7e6d-4556-afe2-82e4af1f2458`, Light Colour Button `eb35c82d-fe3c-4254-9f92-ca68e69436d9`, Claude Sign-in Code UI Sketches `b0532da2-5327-4b2c-b241-9bfe9d318a13`.
- Read them with `Artifact action=read` (single pages) or `list_files` + `read_file` (Design canvases: `project/*.dc.html`).

## Shell (decision pending)
Browser tab works today. A desktop window with a transparent, click-through, always-on-top overlay needs a shell: Tauri (light, needs Rust), Electron (npm, heavy), pywebview (no real click-through). See the to-do list.

## Done
bridge, orb (JARVIS skin, line glow), clock, window manager (title bar, pin = reopen on restart), dock + command bar (Enter sends `voice.submit`), media widget, lyrics revolver.

## Left
Ultron head + skin, Calendar drawer, Live Logs, Settings, Updates, Claude terminal, presence; shell; supervisor wiring; tests (the Qt-specific ones go with `clUI.py`); retire `clUI.py`.
