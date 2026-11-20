# Embedded browser / YouTube player for the fullscreen view — small start plan

Status: **plan only.** Written 2026-11-18. Lowest priority of the three ideas (see `spotify_search_plan.md`, `notification_feed_plan.md`); start only after a measured spike.

## What "a browser that handles everything" would cost

- Qt's web view is Chromium (`PyQt6-WebEngine`): hundreds of MB of RAM while a page is open, a large install, slower start, a second heavy process next to the Piper/Kokoro voice and the SLMs. The notes say the lite machine (12 GB) matters.
- It cannot play DRM streams (Netflix, Spotify Web...), so "handles everything" is not true.
- It brings logins, cookies and a bigger security surface, steals keyboard focus from the push-to-talk keys, and has to share the screen with the transparent fullscreen window and the visualizer / Ultron head.
- Compare: opening the user's real browser on the second monitor already works today and costs the ecosystem nothing.

## Two shapes, from light to heavy

| Option | What | Rough cost | Limits |
|---|---|---|---|
| **A. YouTube-only player** | `mpv` (video window, controllable over its IPC socket) fed by `yt-dlp`; the ecosystem only sends play/pause/seek/volume. `ytmusicapi` is already a dependency (search). | small: two external tools, no Chromium; mpv is nearly idle when stopped | YouTube and anything `yt-dlp` supports, not a page with buttons |
| **B. Embedded web view** | `QWebEngineView` inside the fullscreen window or a widget | heavy (above) | real pages, but no DRM, high RAM |

Recommendation: **spike A first**; only if A is not enough, spike B behind a setting that is off by default.

## Step 0 — spike A (about 1 hour, nothing in the repo)

1. Install `mpv` and `yt-dlp` (winget on Windows, packages on Linux); neither is installed on the desktop today.
2. `mpv --input-ipc-server=<pipe> "https://www.youtube.com/watch?v=..."`; drive it from a script over the IPC (pause, seek, volume, quit).
3. Measure RAM/CPU while playing a 1080p video, and idle; check start-to-first-frame; check on the laptop too (software decoding vs `--hwdec=auto`).
4. Check where the window goes: on the portrait monitor, and over/under the fullscreen dashboard; whether it can be placed in a rectangle the dashboard leaves free.
5. Verdict: playable, light enough, controllable. If not, stop.

## Step 1 — service (if A passes)

`src/clPlayer.py`, an independent MQTT service owning one `mpv` process: actions `play(url|query)`, `pause`, `seek`, `volume`, `stop` on a `pc/player/control` topic (schema in `config/actions.json` like `spotify.control`), state on `jarvis/sys/player_status`. Search/resolve a spoken query with `ytmusicapi` or `yt-dlp ytsearch1:`. Ducks and pauses with TTS exactly like Spotify (reuse the duck logic), stops Spotify when it starts (one audio source at a time), kills `mpv` on shutdown. `CURRENT_OS` branches for the IPC (named pipe vs unix socket) and the install hint. Tests: command building, IPC messages (fake socket), duck/pause during TTS, process cleanup.

## Step 2 — UI

A small "Now playing (video)" widget with the same controls as the media widget; fullscreen placement is the open design question from step 0.4 (mpv window positioned by geometry vs embedding its output with `--wid`). Keep it a separate window at first.

## Step 3 — voice

"Play <something> on YouTube" through the existing intent path (`config/actions.json` + `config/intents.json`, so the SLM needs retraining: see `training/CLAUDE.md`; that cost is why this is last).

## Only if B is ever needed

Behind `settings.embedded_browser.enabled` (default false), loaded lazily so the Chromium cost exists only while a page is open; measure RAM on the laptop; keep it out of the supervisor's always-on set; decide the focus rules for the push-to-talk keys before building.
