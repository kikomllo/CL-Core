# Voice latency, local TTS and barge-in: plan and status

Working notes so a later session can continue without re-deriving anything. Topic docs with the durable
constraints are `docs/context/voice-pipeline.md` (mic/endpointing/barge-in) and `docs/context/daemon-routing.md`
(daemon, TTS engines). **Nothing here is committed yet** (working tree only) — the owner asks for commit messages
separately, one or two sentences about *why*.

## Where we are (2026-10-11)

Measured end of speech → reply audio, from `logs/latest_run.log` `[LATENCY]` lines:

| | start of work | now |
|---|---|---|
| typical command | 3.3–4.6 s | **1.5–1.8 s** |
| command that falls to the SLM | 3.4–7 s | 2.4–2.8 s |

Typical breakdown now: endpoint 0.72 + whisper 0.4–0.7 + reply_gen 0.05 + tts first chunk 0.10 + blip 0.27.

## Done (all tested; touched test files pass: 356 passed, 1 skipped)

- **Phase 0 – tracing**: `utils/clLatency.py`; stamps through mic → Whisper → Daemon → TTS; two `[LATENCY]` lines per command.
- **Phase 1 – neural endpointing**: `utils/clVad.py` (bundled Silero) + `utils/clEndpointer.py`; ends a command 700 ms after speech (was ~1.6–2.2 s). `core.json` `vad_settings` {engine, end_silence_ms, speech_threshold}, optional.
- **Phase 2**: `_is_complex_request` no longer treats Whisper's closing period / one comma as "complex" (was sending nearly every command to the ~1 s SLM); Whisper `temperature=0.0` (no 6× retry ladder); blip trimmed 0.96 → 0.36 s and the 0.2 s pre-blip wait removed.
- **Local TTS**: edge-tts got 403 from Microsoft (2026-10-10, upstream rany2/edge-tts#490, region rollout). `clTTS.py` now speaks locally with **Piper** (`en_GB-aru-medium` speaker 2, natural pitch (a +5% then +2% lift was tried and dropped), Ryan's pace — chosen by ear from a tuned A/B of small voices), loaded on demand and released 30 s after use. `settings.tts_engine` = piper (default) | edge. A cloned-Ryan **Pocket TTS** engine (streaming, closest to the original) was built and then **removed by the owner's decision**: ~0.9 GB of RAM it can't give back, PyTorch in the venv — too heavy for a lean ecosystem. Boot announcement waits for the voice to load; only the primary voice is ever cached (`assets/tts_*_local.wav`, ≤140 chars).
- **Bugs fixed on the way**: playlist search crash on `items: [None]` + phonetic match ("cartooning" → "CarTuning"); silent mic reopening after every action (removed the unconditional `open_window`; follow-up roll logic untouched and still works); zero-byte TTS cache files; fallback-voice phrase cached as Ryan.
- **Phase 3 – barge-in**: wake word over speech (pyaec echo canceller, one pass, threshold 0.6, one chunk), PTT key interrupts speech, abort keybind verified, **EchoGuard volume limiter** (option A, owner's choice: lowers only JARVIS's own TTS gain when the speaker is too loud to interrupt; `data/barge_in_state.json`). Replay of real recordings through `_barge_in_step`: 3/3 up to equal loudness, 2/3 at 3× louder, 0 false stops. Config `settings.barge_in`.

## Done: SLM fast decode

The grammar costs ~36-40 ms per generated token vs ~8 ms free. `nlp/clSLM.py` now decodes freely and re-runs under the
grammar only when `nlp/clIntentSchema.py` (built from the .gbnf) rejects the output. Harness
`training/tools/compare_slm_decoding.py` on 296 prompts: 99.0% valid first try, identical to constrained apart from
sampling noise. Real engine: pause music 655 -> 177 ms, colour 891 -> 345 ms, playlist 969 -> 367 ms, two actions
2144 -> ~580 ms; a rejected free output costs ~2.2 s (about 1% of prompts). Tests: `tests/test_clIntentSchema.py`,
`tests/test_clSLM.py::TestFastDecode`. Switch: `slm_settings.fast_decode`. Not yet re-measured in the live
`[LATENCY]` lines (`route=` on SLM commands should drop from 0.7-0.96 s to ~0.2-0.4 s after a restart).

## Decision: Pocket TTS removed (2026-10-11)

It was the best voice (cloned Ryan) but cost PyTorch + the model: ~0.9 GB that Python cannot hand back (deleting the model and
collecting garbage freed nothing; int8 saved 90 MB and was 26% slower; bf16/fp16 crashed). A separate on-demand worker process
solved the idle cost (service 80 MB idle, +0.85 GB only while in use) but had an 8.7 s cold start (4.6 s of it `import pocket_tts`:
PyTorch's attention module + scipy.signal). The owner preferred a small engine. Removed: `PocketEngine`/`PocketWorkerEngine`,
`src/clTTSWorker.py`, the worker tests, `torch`/`pocket-tts` and everything only they pulled in (pydantic, fastapi, sympy, ...),
`--make-voice`. Piper (ARU 2, natural pitch) replaces it: ~300 MB while speaking, ~190 MB unloaded, no PyTorch.
`models/pocket/ryan.safetensors` (the cloned voice, gitignored) can be deleted; recreating it needs a Hugging Face login.
Ideas left if the voice ever needs to be closer to Ryan at small cost: fine-tune a Piper voice on Ryan's cached audio (~15 min),
or try running a cloning model on ONNX Runtime instead of PyTorch (not verified to exist/sound the same).

## Parked: a trained Ryan voice for Piper (local, WSL)

Fine-tuned Piper (Alan base) on the 159 real cached Ryan clips (13 min) in WSL2, two runs: raw clips (600 epochs planned, stopped ~340) and
clips cleaned of mp3 damage (`training/tools/voice_clean_data.py`: 7 kHz low-pass, digital silence filled, pauses capped) warm-started from the
first run (150 epochs). Result, judged with the GPU idle: every checkpoint, snapshot-averaged or not, sounds glitchy/wavy next to the real
recording (the sentence "Right away. Is there anything else you require, sir?" is a training clip and still glitches), so it is **not good enough
to ship**; `en_GB-aru-medium` stays the runtime voice. Findings worth keeping: lower `noise_scale` (0.2-0.3) steadies the pitch; averaging
late snapshots and 80/20 blends with the base did not remove the glitches; merging short sentences did not help. Realistic next step is more
Ryan audio (edge-tts was still 403) or a multi-speaker run that borrows phoneme coverage from another voice. Models: WSL `~/voice/out/`.
Tooling and the working recipe: `training/CLAUDE.md` ("Local voice training"). **Audition voices with training paused** — a running
training job makes every playback path cut words at random.

## Tried and removed: Kokoro (and the film-JARVIS Piper voice)

After the Ryan fine-tune was parked, the owner asked to pick an existing small model and shape it. Kokoro-82M (ONNX, no PyTorch), Fable+Lewis blend at
speed 1.3 with "!" for ".", was the best by pitch movement (Daniel 1.7 st = "dead", Fable 3.3, Ryan 4.2): ~400 MB loaded, first audio 1.1-1.9 s per sentence **but
only on onnxruntime 1.31 (1.26 was 3.5x slower)**. The community film voice (`jgkawell/jarvis`, jarvis-high) needed noise 1.0/1.2 plus an EQ matched to Ryan
(it reproduces the films' honky/harsh "radio" sound: ~6 dB too much at 1-2.5 kHz, 8 dB at 6 kHz); a PSOLA pitch stretch matched Ryan's range but was judged
worse. Both were built as optional `tts_engine` values with tests, then **removed on the owner's call ("still robotic ... just keep ARU")** — the code is
gone from `clTTS.py`/`clTTSEngines.py`, `requirements.txt` is back to onnxruntime 1.26.0. If a livelier local voice is ever wanted again, the recipe above is all
it takes (a ~100-line engine with per-sentence generation and a linear-phase FIR EQ).

## Local voice survey (2026-10-12) — nothing lean matched Ryan

Judged by the owner with the machine idle. Measured on this 12-core desktop CPU (loaded RAM / real-time factor): Piper ARU speaker 2 ~300 MB;
Kokoro Fable+Lewis ~400 MB / 0.45 (best local result, optional engine); Supertonic 3 (99M, M1-M5) ~565 MB / 0.3, preset voices, OpenRAIL-M, repo
archived; Kitten TTS 0.8 mini ~330 MB / 0.27 and nano-int8 ~250 MB / 0.1 (no PyTorch at runtime, but the `kittenml` package installs torch;
Kitten 2 needs ~6 GB); Piper en_US-ryan-high ~290 MB / 0.2-0.5 (American) — all rejected by ear. MOSS-TTS-Nano (ONNX voice cloning from Ryan
clips) peaked at 2.45 GB and ran ~3x slower than real time — over budget. Untested: NeuTTS Air (cloning, ~400-600 MB GGUF + ONNX decoder).
**Then found:** a community Piper voice trained on the Iron Man films (`jgkawell/jarvis`): see the removed-engines section above; the owner called it "better", not "good". Remaining routes: real Ryan clips as a phrase cache for the known replies, more Ryan audio when edge-tts works again, or a cloning model if the
RAM budget is ever relaxed. All survey scripts, clips and downloads were deleted afterwards (WSL `~/voice`, `data/voice_training/`, `models/pocket|kokoro`, `outputs/voice_*`, HF cache entries, uv).

## Done: wake-word clean-up

"hey tervis pause music", "HRVS, pause music", "No Jarvis …", "New Jarvis …" now have the name stripped and take the fast path; "X hey jarvis X" and "X X" repeats collapse to one copy; "No, Jarvis." stays a decline. Not yet seen in the live `[LATENCY]` lines.

## Other open items (small → large)

- ~~Wake word stripping too strict / doubled phrase~~ — done: see `docs/context/voice-pipeline.md` ("Wake-word clean-up"); tests in `tests/test_clDaemon.py::TestWakeWordMishearings`.
- **Colour commands never match the fast path** ("change the light color to red/white"): needs `config/intents.json` template + regenerate/retrain both SLMs (`training/CLAUDE.md`). Separate task.
- **Adaptive endpoint** (shorter wait for short complete commands, ~0.2 s gain) — only behind a setting; risk of cutting people off.
- **Phase 4 streaming/speculative STT**: measured not worthwhile on this machine (Whisper is 0.4–0.7 s; best case saves 0.3–0.5 s for a lot of complexity and extra GPU load). Only reconsider for CPU-only machines where Whisper takes 2–4 s.
- **Reply SLM** (`generate_reply_async`, ~0.05 s) is not a bottleneck.

## Owner to-dos / things to verify live

- **Revoke the Hugging Face token** that was pasted in chat (used once for the voice clone; nothing needs it any more).
- **Barge-in live checklist**: restart the ecosystem; make JARVIS say a long reply (publish to `jarvis/sys/speak`), say "Hey Jarvis, stop" over it → expect a `BARGE-IN` log line and speech cut within ~0.3 s; try the PTT key and the abort keybind; raise the system volume and look for `[ECHO] Lowering JARVIS's voice…` after a few replies and the one-time spoken notice. The owner switched the input device to a Logitech G435 headset (in `core.json`) — echo measurements were all made with the Razer Seiren Mini, so a wireless headset is untested.
- The configured output device "Altifalantes (Realtek High Definition Audio)" no longer exists (TTS falls back to the system default) — re-pick it in Settings if it should be pinned.

## Measurements worth remembering

- Voice: Piper `en_GB-alan` is ~108 Hz and flat — "too deep"; the original Ryan is ~135 Hz. Kokoro was 8–12× slower than real time on 1 core (rejected); Chatterbox ~6× slower on CPU and GPU was ruled out ("must run on low-end PCs"). Pocket TTS ≈ 0.7× real time on **one** core.
- Echo: this setup's echo is ~15 dB *below* the owner's voice (delay ≈ 114 ms) — an easy case. Re-mixed at stronger echo, the canceller helps up to ~+10 dB (echo 3× the voice); beyond that only lowering JARVIS's own level works (hence EchoGuard). WebRTC AEC3 (livekit) removed echo but also suppressed the voice (worse for the wake word). Two cancellation passes were worse than one. Requiring two consecutive wake-word chunks missed most interruptions without preventing any false stop.
- Whisper pads every clip to 30 s, so trimming silence does not speed it up; `wire`/`queue`/`wav` are ~0.

## Gotchas hit while working (so they aren't hit again)

- The Bash tool's shell layer **collapses backslashes** in commands and heredocs (a `\b` regex turned into a backspace character in `clDaemon.py`; fixed). Write files with the editor tool, or build such strings from `chr(92)`; scan with `grep -P '[\x00-\x08]'` after scripted edits. Apostrophes inside a heredoc can also break the shell.
- Importing `llama_cpp` before `nlp.clSLM` fails on Windows (the CUDA DLL folders are registered by `clSLM`); import `nlp.clSLM` first in scripts.
- `openwakeword.Model(wakeword_models=…)` raises `TypeError` here; the mic uses `wakeword_model_paths=` as a fallback — copy that in test scripts.
- `core.json` is rewritten by the app (the input device changed on its own); don't treat a modified `core.json` in `git status` as mine.
- Scratch experiments (echo recordings, simulations, AEC/TTS candidates) lived in the session scratchpad and are gone; their conclusions are in this file and in `docs/context/voice-pipeline.md`.
