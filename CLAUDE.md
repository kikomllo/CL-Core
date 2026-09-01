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

### SLM fine-tuning pipeline (`tools/`)

1. `python tools/gen_dataset.py` — regenerates **both** `data/synthetic_lora_dataset.jsonl` and `config/grammars/intent_schema.gbnf` from `config/intents.json` in one pass. These two outputs must stay in lockstep, so the `.gbnf` file is auto-generated and headed "do not hand-edit" — its `action_id`/`action` enums are derived directly from `intents.json`. Change intents, then re-run this script; never hand-edit the grammar.
2. `tools/kaggle_train.py` — not run locally. Copy its cells into a Kaggle (or Colab) notebook to LoRA-fine-tune `unsloth/Qwen2.5-0.5B-Instruct` on the generated JSONL and export a quantized GGUF.
3. `python tools/benchmark_slm.py` — runs `data/benchmark_suite.json` against a GGUF under `config/grammars/intent_schema.gbnf` (grammar-constrained decoding via `llama-cpp-python`) and reports per-category pass rates. Run this before promoting a new GGUF into `config/core.json`.

## Architecture

- **Entry point**: `boot.py` → `clJarvis.py`. `clJarvis.py` is the master supervisor: spawns every microservice as a subprocess, tracks health, restarts crashed modules, and can trigger a full ecosystem reboot via an MQTT directive on `jarvis/sys/manager`.
- **Central Brain** (`src/clDaemon.py`, class `CentralDaemon`): the dialogue state machine and router. Its core is `route_voice_command()`, which uses **hybrid cognitive routing**:
  1. **Fast-Path** — `nlp/clIntentEngine.py`'s `IntentEngine`, a stateless RapidFuzz fuzzy matcher built from `config/intents.json` templates/priority words (~0.01s).
  2. **Smart-Path** — `nlp/clSLM.py`'s `SLMInferenceEngine`, invoked when the fast-path misses or the request looks compound/corrective. Runs the fine-tuned Qwen2.5-0.5B GGUF through `llama-cpp-python` with `config/grammars/intent_schema.gbnf` grammar-constrained decoding, and also sees recent dialogue history for context (pronoun resolution, corrections).
  3. Falls back to the fuzzy parser again if the SLM is disabled or returns no actions.
  Multi-turn state (e.g. "awaiting Spotify choice") lives in `self.active_context` / `self.dialogue_history`; `intents.json` and `responses.json` are hot-reloaded on mtime change via `_watch_configs`.
- **ActionRouter** (`src/utils/clActionRouter.py`, singleton): the single place an `action_id` (e.g. `"light.set"`) — whether it came from the fast-path or the SLM — is translated into an MQTT topic + validated payload, using `config/actions.json` as the registry. Actuators never receive anything except already-routed MQTT messages.
- **Actuators** are independent MQTT subscribers, each owning one integration: `clControl.py` (WiZ/Tapo lights, with MAC-sweep self-healing on IP change), `clSpotify.py` (Spotify Web API, ducking during TTS), `clTerminal.py` (OS-level app/window control), `clTTS.py` (edge-tts speech + playback mixer), `clMonitor.py` (BLE presence).
- **ConfigLoader** (`src/utils/clConfigLoader.py`) enforces JSON Schema validation (`config/intents_schema.json` etc.) rather than tolerating malformed config — per `.agents/AGENTS.md`, don't add legacy-compat fallbacks here, keep configs on the strict modern schema.
- **UI** (`src/clUI.py`, ~1900 lines, PyQt6, plus `src/ui/*Widget.py`) is a separate desktop app that talks to the rest of the system purely over MQTT (`paho-mqtt`) — treat it as just another decoupled client, not part of the daemon process.
- **State persistence is mid-migration to Redis** (`src/utils/clRedisClient.py`, pooled client keyed off `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB` in `.env`) — see `tests/test_redis_migration.py` for the target shape when touching persisted daemon/UI state.
- The live daemon's SLM model path is `config/core.json` → `settings.slm_settings.model_path`, independent of the hardcoded `MODEL_PATH` in `tools/benchmark_slm.py` — keep both pointed at the same GGUF when swapping in a newly trained model.

## Working in this repo

- `models/` (~2GB of GGUF + faster-whisper HuggingFace cache blobs) is gitignored — never read or grep into it.
- `data/` mixes generated/versioned files (`synthetic_lora_dataset.jsonl`, `benchmark_suite.json`) with gitignored per-user runtime state (`todos/`, `reminders/`, `alarms/`, `events/`, `ui_state.json`) — the runtime-state subfolders aren't fixtures, don't treat their contents as canonical.
