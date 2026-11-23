# Lyrics sync from audio: plan (not started)

Idea (2026-11-22): lyrics with no timestamps drift (the length-based estimate in `clLyrics.estimate_line_times`), so use
short audio samples + the STT to find where known lyric lines really are, and store the corrected timings locally.
Parked while the web UI rebuild is in progress; picked up after it.

**Why**: in the real cache 37 of 622 tracks are plain-only (estimated, drift as the song goes on) and 107 have no lyrics.
Test track: "Cem anos" by Alcool Club (fast Portuguese rap, plain-only: the worst case, so a good test).

## How it works

Align, don't transcribe: we already have the text, we only need *when*. A bad transcription then only gives fewer anchors,
never wrong lyrics.

1. **Capture** ~4 s of system output (Windows WASAPI loopback, Linux PulseAudio monitor source; both machines). Skip a
   window that overlaps TTS/notification audio. Keep only text and timings, never the audio.
2. **Transcribe** with the existing Whisper using `word_timestamps`, language forced (not auto-detected on music), first and
   last word of the clip dropped (clipped).
3. **Match** the words to the known lyrics (fuzzy). Repeated lines (choruses, rhyme endings) are resolved by picking the
   occurrence closest to where the current estimate and Spotify's progress say we are. Keep only confident matches = anchors.
4. **Anchor time** = song position when the clip started + the word's time in the clip.
5. **Fill in**: interpolate between anchors, keeping line order monotonic. If no anchor is confident, retry another 4 s a few
   seconds later (do not widen the window); stop after 2-3 agreeing anchors for a synced track.
6. **Store** the result in the track's `data/lyrics_cache/<key>.json` as `aligned` (timed lines + anchor count, confidence,
   date). It never expires like `synced`; `_lyrics_payload` prefers `aligned` over the estimate. Next play is already right.

## Slices (each shippable)

1. **Loopback capture** helper, own MQTT service or inside `clSpotify`/`clMic`'s audio code, no cross-imports; test on both OSes.
2. **Align + store** for plain-only tracks (sample every ~20-30 s during play), `aligned` field, `_lyrics_payload` change, tests on
   synthetic anchors (matching, repeated lines, interpolation, cache round trip). Try it on "Cem anos" first.
3. **Offset check for synced lyrics** (2-3 anchors give a constant offset: wrong master, or Spotify position lag).
4. **Setting** to enable it, off by default on the lite profile (Whisper competes with the voice pipeline on 12 GB); run only
   while idle.
5. Later, only if 1-4 work: lyrics from scratch for tracks with none (poor quality on sung/rapped vocals, mark as approximate);
   forced-alignment model (wav2vec2 CTC) as a desktop-only upgrade.

## Limits to remember

- First play of a song still uses the estimate; later plays are right.
- Whisper pads to 30 s, so a short window saves capture time, not compute.
- Instrumentals and heavily effected vocals give few or no anchors: show as approximate, never invent lyrics.
