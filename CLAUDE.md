# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CL-Core ("JARVIS Smart Home OS") is an async, local-first home-automation and NLP system built on an MQTT pub/sub backbone. Every service is an independent process that talks to every other service only through MQTT topics — never through direct imports of another service's code.

## Commands

- **Boot the full ecosystem**: `python boot.py` — creates/updates the venv from `requirements.txt`, takes a single-instance lock on port 64000 (killing any stale ecosystem processes), then execs `clJarvis.py`. `python clJarvis.py` runs the supervisor directly if the venv is already set up.
- **Run one microservice standalone** (for debugging): `python src/clDaemon.py`, `python src/clWhisper.py`, `python src/clControl.py`, etc. — run from the repo root; each service path-hacks `sys.path` to reach `src/utils` and `src/nlp`.
- **Bypass the microphone** for text-only NLP testing: `python src/clDebug.py`.
- **Tests**: `pytest` from the repo root. Single test: `pytest tests/test_clDaemon.py::TestClass::test_name -v` or `pytest -k pattern`. `tests/conftest.py` provides `mock_mqtt` (mocks `aiomqtt.Client` — no real broker needed) and `message_stream`. Async tests are marked explicitly with `@pytest.mark.asyncio`.
- **Bundle source for external LLM review**: `python clBundler.py [file ...]` → `outputs/jarvis_ai_review.txt` (defaults to the root runner + everything under `src/`, `src/utils/`, `src/nlp/`, `config/*.json`).

## Architecture (overview)

- **Entry point**: `boot.py` → `clJarvis.py`. `clJarvis.py` is the master supervisor: spawns every microservice as a subprocess, tracks health, restarts crashed modules, and can trigger a full ecosystem reboot via an MQTT directive on `jarvis/sys/manager`. Its `TeeLogger` relays every subprocess's stdout to both the console and `logs/latest_run.log` on a per-thread reader; the console side writes through the OS's legacy codepage (cp1252 on Windows), which can't encode a lot of real subprocess output (box-drawing, spinners, emoji) — `reconfigure(errors="replace")` is applied to that stream so an unencodable character gets substituted instead of raising and permanently killing that one subprocess's log relay thread.
- **Central Brain** (`src/clDaemon.py`) routes voice commands through a fast fuzzy path and a grammar-constrained SLM path; **ActionRouter** (`src/utils/clActionRouter.py`) turns an `action_id` into an MQTT topic + payload via `config/actions.json`; actuators (`clControl.py`, `clSpotify.py`, `clTerminal.py`, `clTTS.py`, `clMonitor.py`) are independent MQTT subscribers.
- The dashboard UI (`src/clUI.py`, PyQt6) is a decoupled MQTT client.
- The dual-OS codebase (Linux and Windows) means OS-specific code branches on `CURRENT_OS`.

## Topic docs — read the matching file before working on that area

Detailed notes live in separate files so they're only loaded when relevant:

| Working on | Read |
|---|---|
| `clDaemon.py`, intent routing, follow-ups, `ActionRouter`/`actions.json`, config loading, actuators, Redis state | `docs/context/daemon-routing.md` |
| OS-specific code: volume/media/eco mode, alarms, WiFi SSID, PTT keys, Spotify wakeup, process sweeps, Windows two-process note | `docs/context/cross-platform.md` |
| `clUI.py`, `src/ui/*Widget.py`, window flags, widget geometry | `docs/context/ui.md` |
| Claude bridge: backends, config dir, `--continue`, requirements | `docs/context/claude-bridge-core.md` |
| Claude bridge: first-run auto-answers, speech, stall watchdog, sign-in/`--setup` | `docs/context/claude-bridge-session.md` |
| Claude widget, `/terminal` ⇄ `/claude`, terminal resize | `docs/context/claude-bridge-widget.md` |
| SLM training, datasets, benchmarks, model paths (`training/`) | `training/CLAUDE.md` (auto-loads when working under `training/`; read it directly when editing `config/intents.json`) |

**Before editing code in any area above, read its topic file first** — the docs hold constraints that aren't visible from the code alone, and skipping them tends to produce plausible implementations that break later. A change spanning two areas needs both files (bridge work often needs all three bridge files).

**Gotchas that apply everywhere** (details in the topic files):
- Services talk only via MQTT topics, never by importing each other.
- Branch OS-specific code on `CURRENT_OS`; Linux and Windows both run the full ecosystem.
- The Claude bridge uses its own `CLAUDE_CONFIG_DIR` (`data/claude_bridge_config/`), never `~/.claude/` — sharing it causes lock-contention hangs.
- Every spawn of the bridged `claude` must strip inherited `CLAUDE_CODE_*` env vars.
- `--continue` only works once `<config dir>/projects/` exists.
- `data/ui_state.json` is one shared file; don't point test instances of the UI at it.
- Never use broad `pkill -f` patterns; they can match the user's live session.

The bridge plan and status is in `docs/claude_bridge_plan.md`.

**Standing instruction for any Claude Code session working in this repo**: when the JARVIS ecosystem is running live, speak a short, one-sentence summary of what you did or what's needed next at the end of every turn by publishing to `jarvis/sys/speak`, unconditionally — not only when reached through the voice bridge. The one exception: if your system prompt says the host application already reads your answers aloud (the bridged voice session), do not run this; the bridge speaks every final answer itself and would double it:
  ```
  python3 -c "import paho.mqtt.publish as publish, json; publish.single('jarvis/sys/speak', json.dumps({'text': '...', 'ignore_silent': True}), hostname='localhost')"
  ```

## Working in this repo

- `models/` (~2GB of GGUF + faster-whisper HuggingFace cache blobs) is gitignored — never read or grep into it.
- `data/` holds only gitignored per-user runtime state (`todos/`, `reminders/`, `alarms/`, `events/`, `ui_state.json`) — none of it is a fixture, don't treat its contents as canonical. The versioned training datasets live separately under `training/data/`.
- When editing `config/intents.json`, the action and reply models need regenerating/retraining together — see `training/CLAUDE.md`.
