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

### SLM fine-tuning pipeline (`training/`)

Everything training-related — dataset generators, Kaggle/Colab notebook scripts, benchmark tooling, and the generated `.jsonl` datasets themselves — lives under `training/` (`training/tools/`, `training/data/`), deliberately separated from the runtime ecosystem so it reads as its own self-contained sub-project. `config/intents.json` and `config/grammars/intent_schema.gbnf` stay in `config/` regardless, since both are also consumed at runtime (`nlp/clIntentEngine.py`, `nlp/clSLM.py`) — only `training/tools/gen_dataset.py` writes to the latter. A dedicated `training/.venv` (or `.venv-train` before it's relocated) holds the heavy training-only dependencies (`torch`, `unsloth`, `bitsandbytes`, `trl`, `peft`, `accelerate`) — kept out of the main ecosystem venv entirely, since runtime inference only ever needs `llama-cpp-python`, not a training stack.

There are two separate models: the **action model** (Qwen2.5-0.5B-Instruct, grammar-constrained, classifies intent into `action_id`/args) and the **reply model** (SmolLM2-360M-Instruct, no grammar, phrases a spoken JARVIS-voiced confirmation for an already-decided action). A change to `config/intents.json` — a new intent, a changed template — generally needs both models regenerated and retrained together, since the action model's grammar and the reply model's phrase bank both key off the same intent set.

1. `python training/tools/gen_dataset.py` — regenerates **both** `training/data/synthetic_lora_dataset.jsonl` and `config/grammars/intent_schema.gbnf` (the action model's inputs) from `config/intents.json` in one pass. These two outputs must stay in lockstep, so the `.gbnf` file is auto-generated and headed "do not hand-edit" — its `action_id`/`action` enums are derived directly from `intents.json`. Change intents, then re-run this script; never hand-edit the grammar.
2. `python training/tools/gen_reply_dataset.py` — regenerates `training/data/reply_lora_dataset.jsonl` (the reply model's input) from `config/intents.json` plus its own hand-written `PHRASE_BANK`/`SLOTTED_PHRASES`/`FOLLOWUP_PHRASES` in the script. A new intent needs a `PHRASE_BANK` entry here to get a spoken confirmation at all; without one it's silently skipped. `training/tools/listen_reply_samples.py` plays samples from this dataset aloud (matching the live TTS voice exactly) to judge phrasing/tone before committing to a retrain.
3. Training — either copy cells into a Kaggle (or Colab) notebook, or run locally against a CUDA GPU using `training/.venv`:
   - `training/tools/kaggle_train_all.py` — trains **both** models in one run (action model, then frees GPU memory, then reply model); the normal path when both datasets changed together. On Kaggle/Colab, needs both `.jsonl` files added as notebook inputs; run locally, it finds them under `training/data/` automatically.
   - `training/tools/kaggle_train.py` / `training/tools/kaggle_reply_train.py` — the same two phases as standalone scripts, for retraining just one model.
4. `python training/tools/benchmark_slm.py` — runs `training/data/benchmark_suite.json` against the action model's GGUF under `config/grammars/intent_schema.gbnf` (grammar-constrained decoding via `llama-cpp-python`) and reports per-category pass rates. Run this before promoting a new GGUF into `config/core.json`'s `slm_settings.model_path` (the reply model has no equivalent pass/fail benchmark — judge it by ear via `listen_reply_samples.py` instead, and point `reply_slm_settings.model_path` at it once trained).

## Architecture

- **Entry point**: `boot.py` → `clJarvis.py`. `clJarvis.py` is the master supervisor: spawns every microservice as a subprocess, tracks health, restarts crashed modules, and can trigger a full ecosystem reboot via an MQTT directive on `jarvis/sys/manager`.
- **Central Brain** (`src/clDaemon.py`, class `CentralDaemon`): the dialogue state machine and router. Its core is `route_voice_command()`, which uses **hybrid cognitive routing**:
  1. **Fast-Path** — `nlp/clIntentEngine.py`'s `IntentEngine`, a stateless RapidFuzz fuzzy matcher built from `config/intents.json` templates/priority words (~0.01s).
  2. **Smart-Path** — `nlp/clSLM.py`'s `SLMInferenceEngine`, invoked when the fast-path misses or the request looks compound/corrective. Runs the fine-tuned Qwen2.5-0.5B GGUF through `llama-cpp-python` with `config/grammars/intent_schema.gbnf` grammar-constrained decoding, and also sees recent dialogue history for context (pronoun resolution, corrections).
  3. Falls back to the fuzzy parser again if the SLM is disabled or returns no actions.
  Multi-turn state (e.g. "awaiting Spotify choice") lives in `self.active_context` / `self.dialogue_history`; `intents.json` and `responses.json` are hot-reloaded on mtime change via `_watch_configs`.
- **Proactive follow-ups** (`CentralDaemon._roll_followup`): after a dispatched action, whether to speak a follow-up is a two-stage roll over a weighted candidate pool (`config/core.json` → `settings.followup_settings`), not an always-on "anything else" — `generic` (any time), `lights_off_evening` (no lights on + evening hours), `music_paused` (Spotify not playing). Stage 1 picks a candidate by weight among those currently eligible; stage 2 rolls its own `fire_probability` to decide if it's actually spoken. Each candidate has an independent cooldown (short after any ask, escalated after an explicit decline, tracked in `self.suggestion_cooldowns`), and `system.discovery`'s `discover` action suppresses the whole mechanism (it's already a lengthy, TTS-heavy interaction). `settings.enable_followup = false` disables it entirely. Live light on/off state comes from subscribing to `jarvis/sys/light_status`, separate from the pre-existing `is_spotify_playing` tracking off `jarvis/sys/media_status`.
- **ActionRouter** (`src/utils/clActionRouter.py`, singleton): the single place an `action_id` (e.g. `"light.set"`) — whether it came from the fast-path or the SLM — is translated into an MQTT topic + validated payload, using `config/actions.json` as the registry. Actuators never receive anything except already-routed MQTT messages.
- **Actuators** are independent MQTT subscribers, each owning one integration: `clControl.py` (WiZ/Tapo lights, with MAC-sweep self-healing on IP change), `clSpotify.py` (Spotify Web API, ducking during TTS), `clTerminal.py` (OS-level app/window control), `clTTS.py` (edge-tts speech + playback mixer), `clMonitor.py` (BLE presence).
- **ConfigLoader** (`src/utils/clConfigLoader.py`) enforces JSON Schema validation (`config/intents_schema.json` etc.) rather than tolerating malformed config — per `.agents/AGENTS.md`, don't add legacy-compat fallbacks here, keep configs on the strict modern schema.
- **UI** (`src/clUI.py`, ~1900 lines, PyQt6, plus `src/ui/*Widget.py`) is a separate desktop app that talks to the rest of the system purely over MQTT (`paho-mqtt`) — treat it as just another decoupled client, not part of the daemon process.
- **State persistence is mid-migration to Redis** (`src/utils/clRedisClient.py`, pooled client keyed off `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB` in `.env`) — see `tests/test_redis_migration.py` for the target shape when touching persisted daemon/UI state.
- The live daemon's SLM model path is `config/core.json` → `settings.slm_settings.model_path`, independent of the hardcoded `MODEL_PATH` in `training/tools/benchmark_slm.py` — keep both pointed at the same GGUF when swapping in a newly trained model.
- Both `slm_settings` and `reply_slm_settings` in `core.json` also carry a `model_url` — if `model_path` isn't present under `models/` at boot, `nlp/clSLM.py`'s `ensure_gguf_exists()` downloads it from there (e.g. a Hugging Face `resolve/main/...` link). Neither GGUF is a stock model, so there's no hardcoded fallback URL; an empty `model_url` with a missing file just disables that engine with a clear log message instead of crashing.

## Working in this repo

- `models/` (~2GB of GGUF + faster-whisper HuggingFace cache blobs) is gitignored — never read or grep into it.
- `data/` holds only gitignored per-user runtime state (`todos/`, `reminders/`, `alarms/`, `events/`, `ui_state.json`) — none of it is a fixture, don't treat its contents as canonical. The versioned training datasets live separately under `training/data/`.
