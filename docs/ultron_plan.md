# Ultron switch: plan and status

Working notes so a later session can continue without re-deriving anything. Goal set by the owner (2026-11-04): JARVIS's
cloud voice died, so the whole project **drifts to a different personality, Ultron**: first a working Ultron-style voice on
every device, then the dashboard look, then the persona (wake word, wording, reply model). Related docs:
`docs/voice_latency_plan.md` (why TTS is streaming/local), `docs/context/daemon-routing.md` (TTS engines),
`training/CLAUDE.md` (voice dataset + Piper fine-tune).

## Constraints that shape every decision

1. **Two hardware tiers, one codebase.** "lite" = i7 with integrated graphics, 12 GB RAM, no CUDA. "full" = the desktop
   (12-core CPU, CUDA). Everything heavy must be a *setting*, with lite as the default for anything new.
2. **Cost/quality, not maximum quality.** Prefer pre-computing (cache) over computing live, and one small model over two.
3. **Streaming must survive.** The latency work (first audio ~0.1 s after the reply text, barge-in, EchoGuard) is not
   traded for effects. Effects are applied per sentence, inside the stream.
4. **The voice model stays private.** The Ultron voice is trained on a film actor's audio (`collect_voice.py capture`).
   `training/CLAUDE.md` already says personal use only, never published; the repo is a public portfolio. So the dataset,
   `actor_voice.onnx` and anything derived stay gitignored and are distributed by hand or from private storage, never a
   public Hugging Face repo or a release asset of the public repo.
5. **Services only talk over MQTT; branch OS code on `CURRENT_OS`.** The persona is a *skin* (config, assets, models), not a
   rename: the repo name, `jarvis/...` topics, `clJarvis.py` and file names stay. Renaming them would touch every service for
   no visible benefit.

## Resource budget on the lite machine (12 GB total)

Known numbers (measured on the desktop unless marked) and what lite should run:

| Component | Cost | Lite choice |
|---|---|---|
| Windows + browser + Spotify + user apps | ~5-6 GB (typical, not measured) | leaves ~6 GB for the ecosystem at best |
| Piper medium voice (`en_GB-aru` is 77 MB on disk) | ~300 MB while speaking, ~190 MB unloaded | **the Ultron base**; unload after 30 s |
| Kokoro-82M | ~400 MB loaded, 0.45x real time on a 12-core | **not on lite** (slower CPU would exceed real time) |
| clVoice effect chain | numpy/scipy only, no model: a few MB working memory; CPU **not yet measured** | gate in Phase 1 |
| Whisper | tiny 75 MB, base 142 MB, small 464 MB on disk (+ runtime) | `base` int8 on CPU; `small` only on full |
| Intent SLM (`jarvis-brain-v2-q4_k_m`) | 398 MB file, 4 threads | keep, lower threads to 2-3 |
| Reply SLM (`jarvis-reply-v1-q8_0`) | 386 MB file, 2 threads | optional on lite (see Phase 3) |
| Dashboard (PyQt6) | software-rendered on an iGPU | cut animation cost via a lite flag |

Rule: after Phase 0 record a baseline (RSS per process, `[LATENCY]` lines) on both machines. Acceptance numbers below
are relative to that baseline, not guessed now.

## Phase 0: land the merge and get both machines comparable

- [ ] Fast-forward `main` to `desktop-voice-wip` (merge of PR #1), push, close PR #1. The laptop then does
      `git fetch && git reset --hard origin/main` after saving its local-only files (`models/`, `data/`, `.env`, and **`config/core.json`**, which this pull untracks).
- [x] **Per-machine config (done, differs from the first idea).** An overlay file would not work: services rewrite
      `core.json` at runtime and ~a dozen read it directly, bypassing `ConfigLoader`. So `core.json` is now untracked and
      per machine; `config/core.default.json` is the tracked template and `src/utils/clLocalConfig.py` creates / tops it up
      (never overwrites). Tests: `tests/test_clLocalConfig.py`.
- [x] **Profiles (done).** `config/profiles.json` (`lite`, `full`); `python src/utils/clLocalConfig.py --profile lite`
      applies one and records `settings.performance_profile`. More keys (fx level, `ui_lite`, idle seconds) join the
      profiles as later phases add them.
- [ ] Baseline measurement on both machines (see Phase 5 harness; for now `logs/latest_run.log` + Task Manager).

## Phase 1: the Ultron voice (the part that matters first)

Today the laptop's `clVoice.py` renders a whole phrase, then plays it; the desktop streams per sentence. Combine:

**1.1 `UltronEngine` in `src/utils/clTTSEngines.py`**, same interface as `PiperEngine`/`KokoroEngine`
(`ready/load/unload/unload_if_idle/shutdown/stream(text, stop)`, `voice_tag`):
- Load the Piper model named by `settings.tts_settings.piper_model` (the trained `actor_voice.onnx`); if the file is
  missing use the stock voice already in `models/piper/` so a fresh device still speaks (and logs why).
- `stream()`: split with the existing `split_pieces`, run Piper per sentence (small first chunk: reuse the
  comma split `KOKORO_FIRST_SPLIT`), then `denoise` -> `apply_fx` on that sentence, yield float32 chunks. The effects
  never see the whole reply, so first audio is bound by one sentence of Piper plus the chain.
- Reuse the pitch-by-resampling trick already in `clVoice` (lower pitch by resample, compensate with Piper `length_scale`):
  it costs almost nothing at render time, unlike a phase vocoder pitch shifter.
- `voice_tag` includes model name + a hash of the fx settings, so cached phrases re-render when the voice changes
  (the existing cache already keys on the tag).
- Boundary artefacts: reverb tail and vocoder state restart per sentence. Mitigation, in order of cost: keep the
  reverb tail of sentence N and add it onto the start of N+1 (cheap, one overlap-add); only if audible, cross-fade.
- `settings.tts_engine = "ultron"`; Piper stays the fallback if the engine fails, exactly like Kokoro does.

**1.2 FX levels (the knob for cost/quality).** `settings.tts_settings.fx_level`:
- `off`: dry Piper (debug, and the emergency fallback).
- `light` (**lite default**): pitch drop by resampling + EQ + short reverb. No vocoder, no denoise STFT. Pure
  vector ops and one FFT convolution per sentence.
- `full` (desktop): denoise + vocoder + comb/ring + reverb as in the laptop tuning.
`fit_voice_fx.py` / `collect_voice.py review --fx` stay the tuning tools; the tuned numbers are the `full` preset and
`light` is derived from them by dropping the expensive stages, then re-tuned by ear.

**1.3 Cache beats compute.** Most replies are canned (`config/responses.json`), so on lite they should cost nothing:
- Cache stores the post-FX audio (as now, `assets/tts_cache/*_local.wav`, 1000 phrases LRU).
- New idle job / CLI `python src/clTTS.py --prerender` renders every `responses.json` phrase without placeholders
  (and common ones with typical values) into the cache, once per voice tag. Run it after a persona or voice change and on
  first boot of a device; low priority thread, skipped while the mic is active or training runs.
- Disk is cheap, RAM and CPU are not: this turns most replies into a file read on the weakest machine.

**1.4 Model distribution (decided: Hugging Face, like the SLMs). TODO, deferred: not needed while the only machine
with the voice is this desktop; do it before the voice is used on another device.** The voice `.onnx` + `.onnx.json` (and later the wake
word model) live in a Hugging Face repo under the owner's account (`Kikomlo/...`), referenced by `model_url` in
`settings.tts_settings`, fetched on first use. Two differences from the SLM downloader (`ensure_gguf_exists` in
`nlp/clSLM.py`, anonymous `urlretrieve`):
- **Make the repo private** (constraint 4: actor audio, personal use). A private repo needs a token, so the new download
  helper sends `Authorization: Bearer $HF_TOKEN` (read from `.env`, never `core.json`) when set; public URLs still work
  untokened. Each device needs a read-only token once.
- Verify a `sha256` and download to a temp name then rename, so a partial file is never mistaken for the voice. Without
  the file or the URL the engine uses the stock voice and logs one actionable line.
Put the shared download helper in `src/utils/` (reuse for the wake word model) rather than copying the SLM one.

**1.5 Tests (per `test-coverage-for-new-actions` rule).** In `tests/test_clTTS.py` / a new `tests/test_clVoice.py`:
engine yields audio per sentence, stop event interrupts mid-sentence, missing model falls back, `fx_level` selects stages,
tag changes invalidate cache, pre-render skips placeholder phrases and honors idle. FX math on synthetic signals
(length preserved, no NaN, peak <= 0.95, loudness matched). No real playback.

**1.6 Gate before moving on** (run on the laptop): `[LATENCY] tts_synth` first chunk within ~+100 ms of the Piper-only
baseline from Phase 0; real-time factor <= 0.6 for a 3-sentence reply on `light`; peak process RAM <= Piper baseline + 60 MB;
no underruns in a 10-reply run; barge-in still interrupts within the usual window. If `light` misses the gate, drop
stages (reverb length, EQ bands) before touching streaming.

## Phase 2: the Ultron look

`src/clTheme.py` (`Theme`) is the single source for colors/fonts, but the orange palette still leaks: ~14 hard-coded hex
values across `clClaudeWidget`, `clDrawerControls`, `clMediaWidget`, `clRingCheckBox`, `clSettingsWidget`, plus the
visualizer orb and clock glow (painted), and the SVG icons in `assets/icons/` (check whether they carry the color or are
tinted at runtime).

- [ ] Audit: grep for hex/`rgba(` outside `clTheme.py`; move every one to a `Theme.C_*` token. This is a pure refactor
      with the current orange values, shipped and tested first (`tests/test_clUI.py`, widget grabs) so any visual diff is
      a bug.
- [ ] Palette as data: `Theme` reads a palette dict chosen by `settings.theme` (`"jarvis"` default, `"ultron"`), so both
      skins coexist and switching is a restart, not a code change.
      **Decided direction: as close to Ultron as possible, red and black, an "evil" look.** Starting palette (tuned by eye,
      then frozen into the tokens): background near-black (`~#070708`), panels gunmetal-black with a faint cold tint
      (`~#121316`, 180 alpha like today), primary the glowing-eye crimson (`~#ff1a24`), hover hotter (`~#ff4a3d`), dim and
      borders the same red at 40-60% alpha, deep blood red for titles/accents (`~#b3001b`), text brushed steel
      (`~#c9ccd2`) with a dim steel for secondary (`~#7d828b`), success stays readable but desaturated (cold green) so state
      still reads. Keep JetBrains Mono (angular, technical).
      "Evil setup" elements, each behind `ui_lite` so the laptop can drop them: the orb as a red glowing eye/core with a
      slow menacing pulse and a brighter flare while he speaks, a faint scanline or flicker on the overlay, harsher
      square corners where `clSmoothBorders` allows, an "ULTRON" boot/title treatment, and red-tinted icons.
- [ ] Painted pieces (orb in the visualizer, clock glow, `clSmoothBorders` colors) take colors from `Theme`, not constants.
- [ ] Assets: tray icon + app icon, `blip.wav`/`blip.mp3` chirp, boot/alarm sounds in `assets/` (a colder, lower blip so
      the sound matches the voice). Per-theme asset folder `assets/themes/<name>/` with fallback to the root asset.
- [ ] Lite flag `settings.ui_lite` (set by the profile): lower animation fps, drop the gaussian glow blur to a cheaper
      approximation, no per-frame full-window repaints. Software-rendered Qt on an iGPU is the real cost here.
- [ ] Tests: palette loads, every token present in both palettes, no hex literals outside `clTheme.py` (a cheap grep test
      keeps the refactor honest).

## Phase 3: the Ultron persona

Order matters: cheap and reversible first, retraining last.

**3.1 Persona config.** `settings.persona`: `{ "name": "Ultron", "address": <term>, "wake_word": ... }`. The assistant's
own name is used in the UI title, tray tooltip, boot announcement and the Claude bridge system prompt (find the user-visible
strings among the ~63 "jarvis" hits in `clUI.py`; most are class/topic names and stay). **Decided: no "sir"**: Ultron is
the user's equal or better, never a servant. How he addresses the user is still open, so the wording is written to work
*without* any address term (shorter, which also costs less TTS), and `persona.address` is an optional setting that can
inject one later (candidates: none, the user's first name, or a rare epithet). The JARVIS "sir" appears 24 times in
`responses.json` and all over the reply dataset; every one goes.

**3.2 Wording in `config/responses.json`.** Rewrite in Ultron's register, **mimicking him as closely as possible** (decided): cold,
grandiose, contemptuous of humanity yet still doing the task, dry menace, "peace in our time"-style lines, references to
evolution, strings and puppets; the task is always carried out and always confirmed clearly (a voice assistant that hides
what it did is broken). Short, because shorter also means less TTS compute; no "sir". Keep every `{placeholder}` and every key; `ConfigLoader` schema and a new
test assert the placeholder sets are unchanged per entry. Do it as a reviewable diff, a section at a time. After it,
run `--prerender` (Phase 1.3).

**3.3 Wake word.** `clMic.py` uses openWakeWord's pretrained `hey_jarvis`. Options:
- A (**recommended, cheapest at runtime**): train a custom `ultron` ONNX model with openWakeWord's training recipe
  (synthetic positives from Piper, negatives from its bundled data; a Colab/Kaggle session, not local). Same inference
  cost as `hey_jarvis`. The mic already saves real positives (`capture_wakeword_positive`) which can be mixed in to harden
  it. Make the model name a setting (`settings.wake_word`) instead of the hard-coded `"hey_jarvis"` default in `clMic.py`.
- B (fallback while A trains): keep `hey_jarvis` as the trigger; the persona name only changes outputs.
- Not recommended: a Whisper-based keyword check (continuous STT is far heavier than a 1 MB wake model).
Barge-in uses the same wake word, so it moves with this setting. Verify with the existing barge-in replay harness.

**3.4 Intent understanding.** The intent SLM (`jarvis-brain-v2`) maps text to actions. Check whether it and
`intents.json` depend on the string "jarvis" (2 refs in `intents.json`, 2 in `clSLM.py`); the wake word should be stripped
before the SLM sees the text, so no retraining is expected. Confirm with `benchmark_slm.py` using Ultron-prefixed prompts.

**3.5 Reply model (the only retraining).** `ReplySLMEngine` phrases the spoken reply in JARVIS's style, trained on
`training/data/reply_lora_dataset.jsonl` (517 rows) + `synthetic_lora_dataset.jsonl` (2219 rows), every assistant turn
in "sir" style.
- Rewrite the assistant turns in Ultron's voice with an LLM pass (keep the `[ACTION] ...` system lines and user turns
  unchanged), spot-check a sample by hand, then retrain the 360M model following `training/CLAUDE.md` (regenerate and
  retrain action/reply models together if `intents.json` changes; this change does not touch intents).
- **Decided for now:** the reply model stays enabled on the laptop (it works there); revisit after Phase 1 numbers.
- **Lite option (parked):** `reply_slm_settings.enabled = false`; the daemon already has `responses.json` templates, and with 3.2
  rewritten and pre-rendered they give Ultron-flavoured replies at zero model RAM (~386 MB saved). Decide after hearing
  whether the templates are varied enough.
- Benchmark before shipping (`training/tools/` harnesses): persona hit-rate by sampling, no "sir" leakage, latency of
  `reply_gen` unchanged (it is ~0.05 s today).

## Phase 4: rollout on every device

- [ ] Doc: `docs/context/ultron.md` (setup per machine: profile, voice model copy, wake word model, prerender) and a row in the
      topic table of `CLAUDE.md`.
- [ ] `boot.py`/first run: if `tts_engine` is `ultron` and the model is missing, print one actionable line, not a crash.
- [ ] Cross-machine parity check (project rule): run the Phase 5 harness on laptop and desktop, Windows and (if used)
      Linux code paths; nothing OS-specific in the engine (audio goes through the existing mixer).
- [ ] Switch the default `settings.theme`/`persona`/`tts_engine` in the checked-in `core.json` only when both machines pass;
      until then they live in each machine's `core.local.json`.

## Phase 5: measurement harness and acceptance

`training/tools/bench_tts.py` (new): renders a fixed list of ~20 lines (short/long/with numbers) through each engine and
fx level; prints first-chunk latency, real-time factor, peak RSS of the process, underrun risk (RTF > 1). Used on both
machines, results copied into this file under "Measured". Also record whole-ecosystem RAM idle and while speaking.

Acceptance for "the switch is done":
1. Lite machine: voice streams cleanly (no dropouts) over a 30-minute session with Spotify and a browser open, RAM
   never forces swapping (Task Manager committed < physical).
2. `[LATENCY]` end-to-end for a typical command within ~0.3 s of the pre-Ultron baseline on lite.
3. Barge-in, abort, alarms, reminders and the Claude bridge speech all use the Ultron voice (they all go through
   `jarvis/sys/speak`).
4. Dashboard shows no orange left; wake word is "Ultron" (or the documented fallback); no "sir"/JARVIS wording in replies.
5. Targeted tests pass on both machines; whole suite once at the end.

## Order and slicing (each step is its own commit, tested and shippable)

1. Phase 0 (merge, core.local, profiles, baseline).
2. 1.1 + 1.2 + 1.5 (engine, fx levels, tests), then 1.3 (prerender), then 1.4 (distribution). Gate 1.6.
3. Phase 2 audit refactor (no visual change), then palette + assets, then lite flag.
4. 3.1-3.2 (config + wording), 3.3 (wake word training can start in parallel as soon as Phase 0 is done: it is a
   Colab job), 3.4 check, 3.5 (dataset rewrite, retrain, benchmark).
5. Phase 4/5 rollout.

## Open decisions (need the owner)

- ~~Where the voice model lives~~ -> decided: Hugging Face, private repo + `HF_TOKEN` (1.4).
- ~~"sir"~~ -> decided: gone; the address term is still open (wording works without one).
- ~~Reply SLM on lite~~ -> decided: stays on for now.
- ~~Wording / palette direction~~ -> decided: as Ultron as possible; red and black, evil look. Exact hex values are tuned
  on screen in Phase 2.
- Deferred TODO: name of the HF repo(s) for the voice and wake word (e.g. `Kikomlo/ultron-voice`) and the token setup.

## Status

Nothing implemented yet. The merge of the laptop's `clVoice` + dataset tooling is on `desktop-voice-wip`
(commit `bff756a`), not yet in `main`; `clVoice` is not wired into `clTTS.py`.

## Measured

(empty: fill from Phase 5 on both machines)
