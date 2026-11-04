"""
collect_voice.py — Build a clean voice dataset (for a custom TTS voice) from your own speech.

Two ways to feed it:
  record    Speak into the mic. Reads a list of prompt sentences one at a time (or --free for
            open-ended talking), live level meter, resumable.
  Once `enroll` has learned the voice, capture/import keep only clips that match it.
  capture   Record whatever the PC is playing (system audio / loopback) while you play your clips.
  import    Pull speech out of audio/video files already on the PC (recordings, calls, videos).

Either way the audio goes through the same cleanup: mono -> resample -> high-pass -> optional
denoise -> split at pauses into 1-15s clips -> reject clipped / noisy / too-short ones ->
loudness-normalise -> 16-bit WAV. Output is LJSpeech-style (wavs/ + metadata.csv, plus a
manifest.jsonl with source/duration/SNR), ready for Piper / XTTS / similar trainers.

Usage:
    python training/tools/collect_voice.py list-devices
    python training/tools/collect_voice.py record                  # guided prompts
    python training/tools/collect_voice.py record --free --transcribe
    python training/tools/collect_voice.py capture --transcribe   # play clips, press Enter to stop
    python training/tools/collect_voice.py import ~/Videos ~/rec.m4a --transcribe
    python training/tools/collect_voice.py review [--ids 8 15-18]  # listen + fix transcripts
    python training/tools/collect_voice.py review --from 40 --to 90 [--all]  # only that stretch
    python training/tools/collect_voice.py move 377-416 --to ultron_lines   # keep these out of enroll/filter/export
    python training/tools/collect_voice.py cut 8                    # show the clip's waveform with seconds
    python training/tools/collect_voice.py cut 8 --remove 1.2 2.0   # or --keep 0.5 3.0
    python training/tools/collect_voice.py review --fx             # play the clips with the live TTS voice effect on
    python training/tools/collect_voice.py fx-sync                 # render the effect version of clips missing from mix30/
    python training/tools/collect_voice.py capture --fx-sync       # ... or do it after any command
    python training/tools/collect_voice.py merge 12 --next 2       # join clip 12 with the 2 clips after it (undo a split)
    python training/tools/collect_voice.py merge 12 40 41          # join specific clips, in dataset order
    python training/tools/collect_voice.py split 12                # show clip 12's loudness strip + the suggested split point
    python training/tools/collect_voice.py split 12 --at 3.4       # split clip 12 in two at 3.4 s
    python training/tools/collect_voice.py split 12 --at 3.4 7.8   # two split points -> three pieces
    python training/tools/collect_voice.py split 12 --marks labels.txt   # at the marks in an Audacity label export
    python training/tools/collect_voice.py split 12 --auto         # ... at its longest pause
    python training/tools/collect_voice.py split rec.wav --at 14   # split a WAV file into rec_part1.wav / rec_part2.wav
    python training/tools/collect_voice.py sfx 8 --start 1.2 --end 6   # cut a laugh out into sfx/
    python training/tools/collect_voice.py trim sfx/laugh_01.wav --cut 0.4 1.3   # drop a stray voice line
    python training/tools/collect_voice.py enroll                  # learn the voice from current clips
    python training/tools/collect_voice.py filter [--apply]        # move other speakers' clips out
    python training/tools/collect_voice.py dedupe [--apply]       # drop clips that repeat other clips
    python training/tools/collect_voice.py transcribe              # fill in missing text
    python training/tools/collect_voice.py stats

Output goes to training/data/voice_dataset/ (gitignored) unless --out is given.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time

import numpy as np
from scipy import signal
from scipy.io import wavfile
from scipy.ndimage import uniform_filter1d

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_OUT = os.path.join(ROOT, "training", "data", "voice_dataset")
DEFAULT_FX_DIR = os.path.join(ROOT, "training", "data", "voice_ref", "fit_preview", "mix30")
MODEL_DIR = os.path.join(ROOT, "models")
MEDIA_EXTS = {
    ".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma",
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
}

ARGS_SOURCE = None
FRAME_MS = 20
TARGET_SPEECH_DB = -20.0
PEAK_LIMIT = 0.97

PROMPTS = [
    "The quick brown fox jumps over the lazy dog near the riverbank.",
    "Good morning, the weather today is cloudy with a chance of rain in the afternoon.",
    "Please turn off the lights in the living room and lock the front door.",
    "What time is my first meeting tomorrow, and who else is attending?",
    "I have set a timer for twelve minutes and thirty seconds.",
    "Could you play something relaxing while I finish cooking dinner?",
    "The temperature inside is twenty-two degrees, and the humidity is forty-five percent.",
    "She sells seashells by the seashore, but the shells she sells are surely not seashells.",
    "Remind me to call my sister on Thursday at a quarter past six.",
    "Three hundred and fifty-seven people attended the conference in Lisbon last year.",
    "Unfortunately, the package will not arrive until sometime next week.",
    "Wait, did you say the meeting was moved to Friday?",
    "I'm not sure that's the best idea, but let's give it a try anyway.",
    "Increase the volume a little bit, and skip to the next song.",
    "Every morning he walks his dog through the quiet park before work.",
    "The train leaves at seven forty-five from platform nine.",
    "Would you like me to add milk, eggs, and fresh bread to the shopping list?",
    "It's quite remarkable how quickly technology has changed over the past decade.",
    "Thank you very much, that was exactly what I needed.",
    "Whether the weather is warm or whether the weather is cold, we'll weather the weather.",
    "The kitchen light is now on, and the thermostat is set to twenty-one degrees.",
    "Excuse me, could you tell me how to get to the nearest pharmacy?",
    "Your battery is running low, so please connect the charger soon.",
    "On the twenty-third of March, we are travelling to Edinburgh for a long weekend.",
    "He couldn't believe his eyes when he saw the enormous cake on the table.",
    "Is it going to snow this weekend, or will it just stay cold and grey?",
    "Alright, I've started the washing machine and it should finish in about an hour.",
    "A good book, a warm cup of tea, and a quiet evening are all I need.",
    "Please read my unread messages aloud, starting with the most recent one.",
    "The results were surprisingly consistent across all five experiments.",
    "Honestly, I think we should leave early to avoid the traffic.",
    "Set an alarm for six thirty tomorrow morning, and make it repeat on weekdays.",
    "How many calories are in a medium-sized banana, roughly?",
    "The old lighthouse stood alone on the cliff, watching the grey waves roll in.",
    "Open the window blinds, please, it's far too dark in here.",
    "They were thrilled to discover that the museum was free on Sundays.",
    "Never underestimate the power of a good night's sleep.",
    "Can you turn the music down? I'm trying to concentrate on this phone call.",
    "Yes, no, maybe, definitely, absolutely, of course, certainly not.",
    "Finally, the system is online and everything is working exactly as expected.",
]


# --------------------------------------------------------------------------- audio helpers

def load_media(path, sr):
    """Decode any audio/video file to mono float32 at `sr` (av handles downmix + resample)."""
    import av
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=sr)
    chunks = []
    with av.open(path) as container:
        if not container.streams.audio:
            raise ValueError("no audio stream")
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise ValueError("no audio decoded")
    return np.concatenate(chunks).astype(np.float32)


def resample(x, src_sr, dst_sr):
    if src_sr == dst_sr:
        return x.astype(np.float32)
    g = math.gcd(src_sr, dst_sr)
    return signal.resample_poly(x, dst_sr // g, src_sr // g).astype(np.float32)


def highpass(x, sr, cutoff=70.0):
    sos = signal.butter(4, cutoff, btype="highpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def spectral_gate(x, sr, strength=1.5):
    """Mild spectral subtraction using the quietest frames as the noise profile."""
    _, _, Z = signal.stft(x, sr, nperseg=1024, noverlap=768)
    mag = np.abs(Z)
    energy = mag.mean(axis=0)
    quiet = energy <= np.percentile(energy, 15)
    noise = mag[:, quiet].mean(axis=1, keepdims=True)
    gain = np.clip(1.0 - strength * noise / (mag + 1e-9), 0.1, 1.0)
    gain = uniform_filter1d(gain, size=3, axis=1)
    gain = uniform_filter1d(gain, size=3, axis=0)
    _, y = signal.istft(Z * gain, sr, nperseg=1024, noverlap=768)
    y = y[:len(x)]
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y.astype(np.float32)


def frame_db(x, sr):
    n = int(sr * FRAME_MS / 1000)
    m = len(x) // n
    if m == 0:
        return np.zeros(0), n
    frames = x[:m * n].reshape(m, n)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return 20 * np.log10(rms + 1e-9), n


def find_segments(db, args, trim_only):
    """Speech segments as (start_frame, end_frame) plus the noise floor/threshold used.
    The threshold adapts to each recording's own noise floor, so it needs no per-mic tuning."""
    # Digital silence (loopback idle, muted mic) says nothing about the noise between words.
    live = db[db > -80]
    floor = float(np.percentile(live if len(live) else db, 10))
    loud = float(np.percentile(db, 95))
    if loud < -50:
        return [], floor, 0, "silent"
    thr = min(max(floor + 10, -55), loud - 15)

    voiced = db > thr
    runs, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(voiced)])

    min_sil = int(args.min_silence * 1000 / FRAME_MS)
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < min_sil:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    merged = [r for r in merged if r[1] - r[0] >= int(0.25 * 1000 / FRAME_MS)]
    if not merged:
        return [], floor, thr, "silent"

    pad = int(0.15 * 1000 / FRAME_MS)
    padded = []
    for i, (s, e) in enumerate(merged):
        prev_e = merged[i - 1][1] if i else 0
        next_s = merged[i + 1][0] if i + 1 < len(merged) else len(db)
        padded.append((s - min(pad, (s - prev_e) // 2 if i else s),
                       e + min(pad, (next_s - e) // 2 if i + 1 < len(merged) else len(db) - e)))
    if trim_only:
        return [(padded[0][0], padded[-1][1])], floor, thr, None

    max_f = int(args.max_dur * 1000 / FRAME_MS)
    out = []
    for s, e in padded:
        while e - s > max_f:
            lo, hi = s + max_f // 2, s + max_f
            cut = lo + int(np.argmin(db[lo:hi]))
            out.append((s, cut))
            s = cut
        out.append((s, e))
    return out, floor, thr, None


def process_audio(raw, sr, args, trim_only=False):
    """Returns (clips, rejects). Each clip is {"audio", "snr_db"}; rejects maps reason -> count."""
    rejects = collections.Counter()
    any_audio = getattr(args, "any_audio", False)
    x = highpass(raw, sr)
    if args.denoise:
        x = spectral_gate(x, sr)
    db, n = frame_db(x, sr)
    if len(db) < 10:
        rejects["too short"] += 1
        return [], rejects
    segs, floor, thr, why = find_segments(db, args, trim_only)
    if why:
        rejects[why] += 1
        return [], rejects

    clips = []
    fade = int(0.01 * sr)
    for s, e in segs:
        a, b = s * n, min(e * n, len(x))
        seg, seg_raw = x[a:b], raw[a:b]
        dur = len(seg) / sr
        if dur < (0.3 if any_audio else args.min_dur):
            rejects["too short"] += 1
            continue
        if not any_audio and np.mean(np.abs(seg_raw) >= 0.99) > 0.001:
            rejects["clipped"] += 1
            continue
        voiced = db[s:e][db[s:e] > thr]
        if len(voiced) == 0:
            rejects["silent"] += 1
            continue
        speech_db = 10 * math.log10(np.mean(10 ** (voiced / 10)))
        snr = speech_db - floor
        if not any_audio and snr < args.min_snr:
            rejects["noisy"] += 1
            continue
        gain = 10 ** ((TARGET_SPEECH_DB - speech_db) / 20)
        peak = float(np.max(np.abs(seg))) or 1.0
        gain = min(gain, PEAK_LIMIT / peak)
        seg = seg * gain
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        clips.append({"audio": seg.astype(np.float32), "snr_db": round(snr, 1)})
    return clips, rejects


def envelope(x, sr):
    """Coarse log-loudness contour (40 ms frames) -- enough to recognise the same audio played twice."""
    n = int(sr * 0.04)
    m = len(x) // n
    rms = np.sqrt(np.mean(np.asarray(x[:m * n], dtype=np.float32).reshape(m, n) ** 2, axis=1))
    return np.maximum(20 * np.log10(rms + 1e-9), -60)


def overlap_score(a, b, min_frames=25, min_frac=0.5):
    """Best (correlation, overlapping frames) over every alignment of two envelopes where they share
    at least `min_frames` and half of the shorter one. Catches exact repeats, one clip inside another,
    and clips that overlap at the edges (the same line captured twice with different cut points)."""
    need = max(min_frames, int(min_frac * min(len(a), len(b))))
    best = (-1.0, 0)
    for d in range(-(len(b) - need), len(a) - need + 1):  # b starts at a[d]
        lo, hi = max(0, d), min(len(a), d + len(b))
        if hi - lo < need:
            continue
        x, y = a[lo:hi] - a[lo:hi].mean(), b[lo - d:hi - d] - b[lo - d:hi - d].mean()
        den = math.sqrt(float((x * x).sum() * (y * y).sum()))
        if den > 1e-6 and (x * y).sum() / den > best[0]:
            best = ((x * y).sum() / den, hi - lo)
    return best


DUP_CORR = 0.9


# --------------------------------------------------------------------------- speaker identity

SPK_MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                 "wespeaker_en_voxceleb_resnet34.onnx")
SPK_MODEL_PATH = os.path.join(MODEL_DIR, "wespeaker_en_voxceleb_resnet34.onnx")


def _fbank(x):
    """Kaldi-style 80-bin log-mel filterbank (25 ms / 10 ms, 16 kHz) with mean normalisation -- the
    input the Wespeaker model was trained on. x is float32 in [-1, 1]."""
    x = x * 32768.0
    n_fft, win, hop, n_mels = 512, 400, 160, 80
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    idx = np.arange(win)[None, :] + hop * np.arange(1 + (len(x) - win) // hop)[:, None]
    f = x[idx]
    f = f - f.mean(axis=1, keepdims=True)
    f = np.concatenate([f[:, :1], f[:, 1:] - 0.97 * f[:, :-1]], axis=1)
    f = f * ((0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1))) ** 0.85)[None, :]  # Kaldi 'povey' window
    power = np.abs(np.fft.rfft(f, n=n_fft, axis=1)) ** 2
    mel = lambda hz: 1127.0 * np.log(1.0 + hz / 700.0)
    pts = np.linspace(mel(20.0), mel(8000.0), n_mels + 2)
    bins = mel(np.arange(n_fft // 2 + 1) * 16000.0 / n_fft)
    bank = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(n_mels):
        l, c, r = pts[m], pts[m + 1], pts[m + 2]
        bank[m] = np.maximum(0.0, np.minimum((bins - l) / (c - l), (r - bins) / (r - c)))
    feats = np.log(np.maximum(power @ bank.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


class SpeakerEncoder:
    """Voice-identity embeddings (Wespeaker ResNet34 via onnxruntime -- no torch needed)."""

    def __init__(self):
        import onnxruntime as ort
        if not os.path.exists(SPK_MODEL_PATH):
            import urllib.request
            os.makedirs(MODEL_DIR, exist_ok=True)
            print(f"Downloading speaker model (~25 MB) -> {SPK_MODEL_PATH}")
            urllib.request.urlretrieve(SPK_MODEL_URL, SPK_MODEL_PATH + ".part")
            os.replace(SPK_MODEL_PATH + ".part", SPK_MODEL_PATH)
        self.sess = ort.InferenceSession(SPK_MODEL_PATH, providers=["CPUExecutionProvider"])

    def embed(self, audio, sr):
        e = self.sess.run(None, {"feats": _fbank(resample(audio, sr, 16000))[None]})[0][0]
        return e / (np.linalg.norm(e) + 1e-9)


VOICEPRINT_NAME = "voiceprint.npz"


SPEAKER_K = 3          # a clip's score is the mean similarity of its K closest enrolled clips


def load_voiceprint(out_dir):
    """The voiceprint as a dict: threshold, centroid and (from `enroll` on) `bank`, the enrolled clips' embeddings."""
    path = os.path.join(out_dir, VOICEPRINT_NAME)
    if not os.path.exists(path):
        return None
    d = np.load(path)
    vp = {"centroid": d["centroid"], "threshold": float(d["threshold"])}
    if "embeddings" in d.files:
        vp["bank"] = d["embeddings"]
        vp["threshold"] = float(d["knn_threshold"])
    return vp


def speaker_score(vp, emb):
    """How well `emb` matches the enrolled voice, 0-1.
    With a bank: the mean similarity of its SPEAKER_K closest enrolled clips. One average voice is a poor model
    of a real voice -- the same speaker sounds different whispering, projecting, or on another microphone -- so
    a clip only has to resemble the part of the voice it is nearest to. Enrolled copies of the clip itself
    (similarity >= 0.98) are ignored, so a clip already in the bank can't match itself.
    Without one (a voiceprint made by an older version): similarity to the single average voice."""
    bank = vp.get("bank")
    if bank is None:
        return float(emb @ vp["centroid"])
    sims = bank @ emb
    sims = sims[sims < 0.98]
    if len(sims) == 0:
        return 0.0
    return float(np.sort(sims)[-min(SPEAKER_K, len(sims)):].mean())


def gate_speaker(clips, ds, args, rejects):
    """Drop clips that don't sound like the enrolled voice; annotate the rest with their similarity."""
    vp = None if args.no_speaker_filter else load_voiceprint(ds.dir)
    if not vp or not clips:
        return clips
    threshold = vp["threshold"]
    if args.speaker_threshold is not None:
        threshold = args.speaker_threshold
    enc = ds.encoder()
    kept = []
    for c in clips:
        c["speaker_sim"] = round(speaker_score(vp, enc.embed(c["audio"], ds.sr)), 3)
        if c["speaker_sim"] >= threshold:
            kept.append(c)
        else:
            rejects["other speaker"] += 1
    return kept


# --------------------------------------------------------------------------- dataset I/O

class Dataset:
    def __init__(self, out_dir, sr):
        self.dir = out_dir
        self.sr = sr
        self.wav_dir = os.path.join(out_dir, "wavs")
        self.manifest_path = os.path.join(out_dir, "manifest.jsonl")
        os.makedirs(self.wav_dir, exist_ok=True)
        self.rows = []
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                self.rows = [json.loads(line) for line in f if line.strip()]
            # A WAV deleted by hand (or by another process) shouldn't leave a dangling row behind.
            kept = [r for r in self.rows if os.path.exists(os.path.join(self.wav_dir, r["id"] + ".wav"))]
            if len(kept) != len(self.rows):
                print(f"[dataset] dropped {len(self.rows) - len(kept)} row(s) whose WAV is missing: "
                      + ", ".join(r["id"] for r in self.rows if r not in kept))
                self.rows = kept
                self.save()

    def _next_index(self):
        return max((int(r["id"].split("_")[-1]) for r in self.rows), default=0) + 1

    def add(self, clip, source, text=""):
        cid = f"voice_{self._next_index():06d}"
        pcm = (np.clip(clip["audio"], -1, 1) * 32767).astype(np.int16)
        wavfile.write(os.path.join(self.wav_dir, cid + ".wav"), self.sr, pcm)
        row = {"id": cid, "text": text, "source": source,
               "duration": round(len(pcm) / self.sr, 2), "snr_db": clip["snr_db"]}
        if "speaker_sim" in clip:
            row["speaker_sim"] = clip["speaker_sim"]
        self.rows.append(row)
        self.save()
        return row

    def save(self):
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, self.manifest_path)
        with open(os.path.join(self.dir, "metadata.csv"), "w", encoding="utf-8") as f:
            for r in self.rows:
                if r["text"]:
                    f.write(f"{r['id']}|{r['text']}|{r['text']}\n")

    def encoder(self):
        if not hasattr(self, "_encoder"):
            self._encoder = SpeakerEncoder()
        return self._encoder

    def embedding(self, row):
        sr, pcm = wavfile.read(os.path.join(self.wav_dir, row["id"] + ".wav"))
        return self.encoder().embed(pcm.astype(np.float32) / 32768.0, sr)

    def env(self, row):
        cache = self.__dict__.setdefault("_envs", {})
        if row["id"] not in cache:
            sr, pcm = wavfile.read(os.path.join(self.wav_dir, row["id"] + ".wav"))
            cache[row["id"]] = envelope(pcm.astype(np.float32) / 32768.0, sr)
        return cache[row["id"]]

    def overlaps(self, env):
        """Existing rows whose audio repeats (part of) `env`, as (row, corr, frames)."""
        hits = []
        for r in self.rows:
            corr, frames = overlap_score(env, self.env(r))
            if corr >= DUP_CORR:
                hits.append((r, corr, frames))
        return hits

    def add_unique(self, clip, source, text="", check=True):
        """Like add(), but returns None (and writes nothing) if the audio is already in the dataset."""
        if check and self.overlaps(envelope(clip["audio"], self.sr)):
            return None
        row = self.add(clip, source, text)
        self.__dict__.setdefault("_envs", {})[row["id"]] = envelope(clip["audio"], self.sr)
        return row

    def remove(self, row):
        os.remove(os.path.join(self.wav_dir, row["id"] + ".wav"))
        self.rows.remove(row)
        self.__dict__.setdefault("_envs", {}).pop(row["id"], None)

    def has_source(self, source):
        return any(r["source"] == source for r in self.rows)

    def has_text(self, text):
        return any(r["text"] == text for r in self.rows)


def file_source_id(path):
    """Stable id for an imported file (size + head hash), so moving/renaming it doesn't re-import it."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
    return f"file:{os.path.basename(path)}:{os.path.getsize(path)}:{h.hexdigest()[:10]}"


def summarize(rejects):
    return ", ".join(f"{v} {k}" for k, v in rejects.items()) or "none"


# --------------------------------------------------------------------------- transcription

def transcribe_missing(ds, args):
    todo = [r for r in ds.rows if not r["text"]]
    if not todo:
        return
    from faster_whisper import WhisperModel
    print(f"Transcribing {len(todo)} clip(s) with Whisper '{args.whisper_model}' on {args.whisper_device}...")
    model = WhisperModel(args.whisper_model, device=args.whisper_device,
                         compute_type="float16" if args.whisper_device == "cuda" else "int8",
                         download_root=MODEL_DIR)
    for i, r in enumerate(todo, 1):
        segments, _ = model.transcribe(os.path.join(ds.wav_dir, r["id"] + ".wav"),
                                       language=args.language, beam_size=5)
        r["text"] = " ".join(s.text.strip() for s in segments).strip()
        print(f"  [{i}/{len(todo)}] {r['id']}: {r['text']}")
        if i % 20 == 0:
            ds.save()
    ds.save()


# --------------------------------------------------------------------------- commands

def cmd_import(args):
    ds = Dataset(args.out, args.sr)
    files = []
    for p in args.paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)
                          if os.path.splitext(n)[1].lower() in MEDIA_EXTS]
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"[skip] not found: {p}")
    if not files:
        print("No audio/video files found.")
        return
    total = 0
    for path in files:
        src = file_source_id(path)
        if ds.has_source(src) and not args.force:
            print(f"[skip] already imported: {os.path.basename(path)}")
            continue
        try:
            raw = load_media(path, args.sr)
        except Exception as e:
            print(f"[fail] {os.path.basename(path)}: {e}")
            continue
        clips, rejects = process_audio(raw, args.sr, args)
        clips = gate_speaker(clips, ds, args, rejects)
        added = [c for c in clips if ds.add_unique(c, src, check=not args.no_dedupe)]
        rejects["duplicate"] += len(clips) - len(added)
        total += len(added)
        print(f"{os.path.basename(path)}: kept {len(added)} clip(s), rejected: {summarize(rejects)}")
    print(f"Imported {total} clip(s).")
    if args.transcribe:
        transcribe_missing(ds, args)


def _open_mic(pa, args):
    import pyaudio
    idx = args.mic
    info = pa.get_device_info_by_index(idx) if idx is not None else pa.get_default_input_device_info()
    rate = int(info["defaultSampleRate"])
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                     input_device_index=idx, frames_per_buffer=1024)
    return stream, rate, info["name"]


def _record_take(read_chunk, on_start=None, on_stop=None):
    """Record until Enter is pressed, with a live level meter. `read_chunk()` returns the next
    mono int16 chunk (empty = stream ended). Returns float32 mono."""
    stop = threading.Event()
    threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
    chunks, tick = [], 0
    if on_start:
        on_start()
    try:
        while not stop.is_set():
            data = read_chunk()
            if len(data) == 0:
                break
            chunks.append(data)
            tick += 1
            if tick % 3 == 0:
                level = 20 * math.log10(np.sqrt(np.mean((data / 32768.0) ** 2)) + 1e-9)
                bar = int(np.clip((level + 60) / 60, 0, 1) * 30)
                print(f"\r  REC [{'#' * bar}{'.' * (30 - bar)}] {level:6.1f} dB  (Enter to stop) ", end="", flush=True)
    finally:
        if on_stop:
            on_stop()
        print()
    return np.concatenate(chunks).astype(np.float32) / 32768.0 if chunks else np.zeros(0, np.float32)


def _mic_take(stream):
    def read():
        return np.frombuffer(stream.read(1024, exception_on_overflow=False), dtype=np.int16)
    return _record_take(read, stream.start_stream, stream.stop_stream)


def cmd_record(args):
    import pyaudio
    ds = Dataset(args.out, args.sr)
    pa = pyaudio.PyAudio()
    try:
        stream, rate, name = _open_mic(pa, args)
        print(f"Mic: {name} @ {rate} Hz  ->  {ds.dir}")
        print("Speak close to the mic in a quiet room, at your normal speaking pace. Ctrl+C to quit anytime.\n")

        if args.free:
            input("Press Enter to start recording freely (talk as long as you like)...")
            raw = resample(_mic_take(stream), rate, args.sr)
            clips, rejects = process_audio(raw, args.sr, args)
            added = [c for c in clips if ds.add_unique(c, "mic", check=not args.no_dedupe)]
            rejects["duplicate"] += len(clips) - len(added)
            print(f"Kept {len(added)} clip(s), rejected: {summarize(rejects)}")
        else:
            prompts = PROMPTS
            if args.prompts:
                with open(args.prompts, "r", encoding="utf-8") as f:
                    prompts = [ln.strip() for ln in f if ln.strip()]
            todo = [p for p in prompts if not ds.has_text(p)]
            print(f"{len(prompts) - len(todo)} prompt(s) already recorded, {len(todo)} to go.\n")
            i = 0
            while i < len(todo):
                print(f"[{i + 1}/{len(todo)}]  {todo[i]}")
                input("  Press Enter, then read the line aloud... ")
                raw = resample(_mic_take(stream), rate, args.sr)
                clips, rejects = process_audio(raw, args.sr, args, trim_only=True)
                if clips:
                    row = ds.add(clips[0], "mic", todo[i])
                    print(f"  saved {row['id']}  {row['duration']}s  SNR {row['snr_db']} dB")
                else:
                    print(f"  rejected: {summarize(rejects)}")
                ans = input("  Enter = next, r = redo, q = quit: ").strip().lower()
                if ans == "q":
                    break
                if ans == "r":
                    if clips:
                        ds.rows.pop()
                        os.remove(os.path.join(ds.wav_dir, row["id"] + ".wav"))
                        ds.save()
                    continue
                i += 1
                print()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        pa.terminate()
    if args.transcribe:
        transcribe_missing(ds, args)
    cmd_stats(args)


def _open_system_audio():
    """Returns (read_chunk, start, stop, rate, label) for capturing what the PC is playing.
    Linux: `parec` on the default sink's monitor (PulseAudio/PipeWire). Windows: WASAPI
    loopback via the optional PyAudioWPatch package (stock PyAudio can't do loopback)."""
    if sys.platform == "win32":
        try:
            import pyaudiowpatch as pa_mod
        except ImportError:
            sys.exit("Windows system-audio capture needs:  pip install PyAudioWPatch")
        pa = pa_mod.PyAudio()
        info = pa.get_default_wasapi_loopback()
        rate, ch = int(info["defaultSampleRate"]), int(info["maxInputChannels"])
        stream = pa.open(format=pa_mod.paInt16, channels=ch, rate=rate, input=True,
                         input_device_index=info["index"], frames_per_buffer=1024)

        def read():
            d = np.frombuffer(stream.read(1024, exception_on_overflow=False), dtype=np.int16)
            return d.reshape(-1, ch).mean(axis=1).astype(np.int16)
        return read, None, lambda: (stream.stop_stream(), stream.close(), pa.terminate()), rate, info["name"]

    import shutil
    import subprocess
    if not shutil.which("parec"):
        sys.exit("parec not found (install pulseaudio-utils / pipewire-pulse).")
    source = ARGS_SOURCE or "@DEFAULT_MONITOR@"
    rate = 48000
    proc = subprocess.Popen(["parec", "-d", source, "--format=s16le", f"--rate={rate}",
                             "--channels=2", "--latency-msec=50"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read():
        raw = proc.stdout.read(4096)
        usable = len(raw) - len(raw) % 4
        return np.frombuffer(raw[:usable], dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.int16)

    def stop():
        proc.terminate()
        proc.wait(timeout=3)
    return read, None, stop, rate, source


def cmd_capture(args):
    global ARGS_SOURCE
    ARGS_SOURCE = args.source
    ds = Dataset(args.out, args.sr)
    read, start, stop, rate, label = _open_system_audio()
    print(f"Capturing PC audio from: {label}  ->  {ds.dir}")
    print("Play your clips now (only your own voice, ideally -- other voices/music get cut into clips too).")
    print("Mute notifications first; anything the PC plays is recorded.\n")
    try:
        input("Press Enter to start capturing... ")
        raw = resample(_record_take(read, start, stop), rate, args.sr)
    except KeyboardInterrupt:
        print("\nStopped.")
        return
    if len(raw) < args.sr:
        print("Nothing captured. Is audio actually playing through the default output?")
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw_dir = os.path.join(ds.dir, "captures")
    os.makedirs(raw_dir, exist_ok=True)
    wavfile.write(os.path.join(raw_dir, f"capture_{stamp}.wav"), args.sr,
                  (np.clip(raw, -1, 1) * 32767).astype(np.int16))
    clips, rejects = process_audio(raw, args.sr, args)
    clips = gate_speaker(clips, ds, args, rejects)
    added = [c for c in clips if ds.add_unique(c, f"capture:{stamp}", check=not args.no_dedupe)]
    rejects["duplicate"] += len(clips) - len(added)
    print(f"Kept {len(added)} clip(s), rejected: {summarize(rejects)}  (raw kept in captures/)")
    if args.transcribe:
        transcribe_missing(ds, args)
    cmd_stats(args)


def cmd_list_sources(args):
    import subprocess
    out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) > 1:
            print(("[monitor] " if parts[1].endswith(".monitor") else "[input]   ") + parts[1])


def parse_ids(tokens):
    """Clip selectors -> set of ids. Accepts 8, voice_000008 and ranges like 12-15."""
    ids = set()
    for t in tokens:
        t = str(t)
        if t.startswith("voice_"):
            ids.add(t)
        elif "-" in t:
            a, b = t.split("-", 1)
            ids.update(f"voice_{n:06d}" for n in range(int(a), int(b) + 1))
        else:
            ids.add(f"voice_{int(t):06d}")
    return ids


class _Keys:
    """Non-blocking single-key reads while a clip plays (no Enter needed). Inert when stdin isn't a terminal."""

    def __enter__(self):
        self.tty = sys.stdin.isatty()
        if self.tty and sys.platform != "win32":
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if self.tty and sys.platform != "win32":
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
            termios.tcflush(self.fd, termios.TCIFLUSH)

    def poll(self):
        if not self.tty:
            return None
        if sys.platform == "win32":
            import msvcrt
            return msvcrt.getwch() if msvcrt.kbhit() else None
        import select
        return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None


def play_with_clock(sound, scale=1.0):
    """Play a clip with a live clock. Enter/space drops a timestamp mark; q stops. Returns the marks (s).
    `scale` maps the played time back to the original clip's (the voice effect's pitch shift changes the length)."""
    length, marks = sound.get_length() * scale, []
    channel = sound.play()
    t0 = time.monotonic()
    with _Keys() as keys:
        while channel.get_busy():
            t = (time.monotonic() - t0) * scale
            print(f"\r  \u25b6 {t:5.2f}s / {length:.2f}s   (Enter/space = mark time, q = stop)   ", end="", flush=True)
            key = keys.poll()
            if key in ("\n", "\r", " "):
                marks.append(round(t, 2))
                print(f"\r  \u25cf mark {len(marks)}: {t:.2f}s{' ' * 50}")
            elif key == "q":
                channel.stop()
                break
            time.sleep(0.03)
    print("\r" + " " * 70)
    return marks


def play_and_report(sound, scale=1.0):
    marks = play_with_clock(sound, scale)
    if marks:
        print(f"  marks: {', '.join(f'{m:.2f}s' for m in marks)}  -> `cut` removes between the last two, `keep` keeps it")
    return marks


def live_fx(mix=None):
    """The effect settings exactly as the TTS uses them: settings.tts_settings.fx in config/core.json over the
    clVoice defaults. `mix` (--fx-mix) overrides only the vocoder level."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from utils.clVoice import merge_settings
    try:
        with open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8") as f:
            user = json.load(f).get("settings", {}).get("tts_settings")
    except (OSError, ValueError):
        user = None
    fx = merge_settings(user)["fx"]
    return dict(fx, vocoder_mix=mix) if mix is not None else fx


def fx_pitch_ratio(fx):
    """Played length / original length is 1/ratio: the pitch shift resamples, so the clip gets longer."""
    st = float(fx.get("pitch_semitones", 0.0)) if fx.get("enabled", True) else 0.0
    return 2.0 ** (st / 12.0) if abs(st) >= 0.05 else 1.0


def render_fx(pcm, sr, mix=None):
    """The voice effect (src/utils/clVoice.py) on a 16-bit clip, the same chain the TTS runs after synthesis
    (pitch shift, vocoder, comb, reverb tail ...), so the clip can be longer than the original."""
    fx = live_fx(mix)                      # also puts src/ on sys.path for the import below
    from utils.clVoice import _pitch_down, apply_fx
    x = pcm.astype(np.float32) / 32768.0
    y = apply_fx(_pitch_down(x, float(fx.get("pitch_semitones", 0.0))), sr, fx) if fx.get("enabled", True) else x
    return to_pcm(y / max(1.0, float(np.abs(y).max()) / 0.95))


def cmd_fx_sync(args):
    """Render the effect version of every captured clip that has none yet (or whose clip changed since) into
    the fx folder (default training/data/voice_ref/fit_preview/mix30), so it mirrors the dataset's wavs."""
    ds = Dataset(args.out, args.sr)
    os.makedirs(args.fx_dir, exist_ok=True)
    sig_path = os.path.join(args.fx_dir, ".fx_signature")
    sig = hashlib.md5(json.dumps(live_fx(args.fx_mix), sort_keys=True).encode()).hexdigest()
    try:
        stale = open(sig_path).read().strip() != sig
    except OSError:
        stale = True
    done = rendered = 0
    for name in sorted(f for f in os.listdir(ds.wav_dir) if f.endswith(".wav")):
        src, dst = os.path.join(ds.wav_dir, name), os.path.join(args.fx_dir, name)
        if not stale and os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            done += 1
            continue
        sr, pcm = wavfile.read(src)
        wavfile.write(dst, sr, render_fx(pcm, sr, args.fx_mix))
        rendered += 1
        print(f"  + {name}")
    with open(sig_path, "w") as f:
        f.write(sig)
    orphans = [f for f in os.listdir(args.fx_dir) if f.endswith(".wav") and not os.path.exists(os.path.join(ds.wav_dir, f))]
    print(f"fx (live settings): {rendered} rendered, {done} already up to date -> {args.fx_dir}"
          + (f"  ({len(orphans)} file(s) there no longer have a clip)" if orphans else ""))


def set_text(ds, row, text):
    """Change a clip's transcript, appending the old one to text_history.jsonl so a slip is recoverable."""
    if text == row["text"]:
        return
    with open(os.path.join(ds.dir, "text_history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": row["id"], "old": row["text"], "new": text,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
    row["text"] = text


def cmd_review(args):
    """Play each clip and fix its text. Reviewed clips are flagged so a re-run resumes where you left off."""
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
    from pygame import mixer
    try:
        import readline
    except ImportError:  # Windows: no prefilled editing, `e` just asks for the full line
        readline = None
    ds = Dataset(args.out, args.sr)
    if args.ids:
        want = parse_ids(args.ids)
        missing = sorted(want - {r["id"] for r in ds.rows})
        if missing:
            print(f"Not in the dataset: {', '.join(m[-3:].lstrip('0') or '0' for m in missing)}")
        todo = [r for r in ds.rows if r["id"] in want]
    else:
        lo = parse_ids([args.first]).pop()[-6:] if args.first else None
        hi = parse_ids([args.last]).pop()[-6:] if args.last else None
        todo = [r for r in ds.rows if (args.all or not r.get("reviewed"))
                and (lo is None or r["id"][-6:] >= lo) and (hi is None or r["id"][-6:] <= hi)]
    if not todo:
        print("Nothing to review (use --all to go through reviewed clips again)." if not args.ids
              else "None of those clips exist.")
        return
    mixer.init()
    fx_tmp = tempfile.mkdtemp(prefix="cv_fx_") if args.fx else None
    fx_scale = [1.0]                      # played time -> original time (the effect's pitch shift changes the length)

    def load_sound(row):
        """The clip as a mixer Sound; with --fx the effect version (a temp copy -- the dataset is untouched)."""
        path = os.path.join(ds.wav_dir, row["id"] + ".wav")
        if fx_tmp:
            sr_, pcm_ = wavfile.read(path)
            path = os.path.join(fx_tmp, row["id"] + ".wav")
            wavfile.write(path, sr_, render_fx(pcm_, sr_, args.fx_mix))
            fx_scale[0] = fx_pitch_ratio(live_fx(args.fx_mix))
        return mixer.Sound(path)

    def play(sound):
        return play_and_report(sound, fx_scale[0])

    if args.fx:
        print("Playing clips with the live voice effect, same settings as the TTS (what you edit and cut is still the original audio).")
    print(f"{len(todo)} clip(s) to review.  Enter = text is right | t <new text> = replace the text | e = edit it")
    print("                                clips play with a live clock: Enter/space marks a time, q stops | r = replay")
    print("                                cut | keep (use your last two marks) | cut 1.2 2.0 | keep 0.5 3.0 | w = waveform")
    print("                                split = cut at the marks you left (or split 1.2 3.4) into several clips")
    print("                                u = undo this clip's text change | d / remove = delete clip | q = quit")
    print("                                anything else does nothing -- the text only changes with t, e or u\n")
    try:
        for i, r in enumerate(todo, 1):
            sound = load_sound(r)
            print(f"[{i}/{len(todo)}] {r['id']} ({r['duration']}s)  \"{r['text'] or '(no text)'}\"")
            marks = play(sound)
            orig_text = r["text"]
            while True:
                print(f"[{i}/{len(todo)}] {r['id']} ({r['duration']}s)\n  {r['text'] or '(no text)'}")
                ans = input("  > ").strip()
                words = ans.split()
                cmd = words[0].lower() if words else ""
                if cmd == "r" and len(words) == 1:
                    marks = play(sound)
                    continue
                if cmd in ("w", "wave") and len(words) == 1:
                    sr_, pcm_ = wavfile.read(os.path.join(ds.wav_dir, r["id"] + ".wav"))
                    timeline(pcm_.astype(np.float32) / 32768.0, sr_)
                    continue
                if cmd in ("cut", "keep"):
                    if len(words) not in (1, 3):
                        print("  usage: cut | keep (your last two marks), or cut 1.2 2.0 | keep 0.5 3.0 -- nothing changed")
                        continue
                    try:
                        if len(words) == 1:
                            if len(marks) < 2:
                                raise ValueError("press `r` and mark two times first (or give them: cut 1.2 2.0)")
                            a, b = sorted(marks[-2:])
                        else:
                            a, b = float(words[1]), float(words[2])
                        cut_clip(ds, r, remove=[(a, b)] if cmd == "cut" else (),
                                 keep=(a, b) if cmd == "keep" else None)
                    except ValueError as e:
                        print(f"  not cut: {e}")
                        continue
                    mixer.stop()
                    sound = load_sound(r)
                    marks = play(sound)
                    continue
                if cmd == "split":
                    try:
                        typed_times = [float(w) for w in words[1:]]
                    except ValueError:
                        print("  usage: split (at the marks you left while it played) or split 1.2 3.4 -- nothing changed")
                        continue
                    if not typed_times and not marks:
                        print("  no marks yet -- press `r`, then Enter/space at each place to split while it plays "
                              "(or: split 1.2 3.4)")
                        continue
                    sr_, pcm_ = wavfile.read(os.path.join(ds.wav_dir, r["id"] + ".wav"))
                    x_ = pcm_.astype(np.float32) / 32768.0
                    points = typed_times or snap_to_pauses(x_, sr_, marks)
                    try:
                        cuts, pieces = make_pieces(x_, sr_, points)
                    except ValueError as e:
                        print(f"  not split: {e}")
                        continue
                    old_text = r["text"]
                    new_rows = split_dataset_clip(ds, r, pieces, sr_)
                    todo[i:i] = new_rows                  # review the new clips right after this one
                    mixer.stop()
                    print(f"  split at {', '.join(f'{c:.2f}s' for c in cuts)} -> {len(pieces)} clips "
                          f"({r['id']}, {', '.join(n['id'] for n in new_rows)}), all blank now. Old text was:\n"
                          f"  \"{old_text}\"\n  set each clip's text with `t <text>`; this is the first one.")
                    sound = load_sound(r)
                    orig_text = r["text"]
                    marks = play(sound)
                    continue
                if cmd in ("d", "remove", "rm", "delete") and len(words) == 1:
                    mixer.stop()
                    os.remove(os.path.join(ds.wav_dir, r["id"] + ".wav"))
                    ds.rows.remove(r)
                    ds.save()
                    print("  deleted\n")
                    break
                if cmd == "q" and len(words) == 1:
                    ds.save()
                    return
                if cmd == "u" and len(words) == 1:
                    if r["text"] == orig_text:
                        print("  nothing to undo")
                    else:
                        set_text(ds, r, orig_text)
                        ds.save()
                        print("  text restored")
                    continue
                new_text = None
                if cmd == "e" and len(words) == 1:
                    if readline:
                        readline.set_startup_hook(lambda: readline.insert_text(r["text"]))
                    try:
                        typed = input("  edit> ").strip()
                    finally:
                        if readline:
                            readline.set_startup_hook()
                    if not typed:
                        continue
                    new_text = typed
                elif cmd == "t" and len(words) > 1:
                    new_text = ans[1:].strip()
                elif ans:
                    print("  not a command -- nothing changed.  Enter = text is right | t <new text> | e = edit | "
                          "r w cut keep u d q")
                    continue
                if new_text is not None:
                    set_text(ds, r, new_text)
                r["reviewed"] = True
                ds.save()
                print()
                break
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
    finally:
        ds.save()
        mixer.quit()
        if fx_tmp:
            shutil.rmtree(fx_tmp, ignore_errors=True)


def cmd_sfx(args):
    """Cut a non-speech sound (laugh, cough, sigh...) out of a clip into sfx/, and keep it out of the
    speech dataset: trimmed off if it sits at the clip's edge, otherwise the clip is dropped."""
    ds = Dataset(args.out, args.sr)
    cid = args.clip if args.clip.startswith("voice_") else f"voice_{int(args.clip):06d}"
    row = next((r for r in ds.rows if r["id"] == cid), None)
    if not row:
        sys.exit(f"{cid} not in the dataset.")
    wav = os.path.join(ds.wav_dir, cid + ".wav")
    sr, pcm = wavfile.read(wav)
    dur = len(pcm) / sr
    start, end = max(0.0, args.start), min(dur, args.end if args.end is not None else dur)
    if end - start < 0.2:
        sys.exit(f"Range {start:.2f}-{end:.2f}s is empty (clip is {dur:.2f}s long).")
    sfx_dir = os.path.join(ds.dir, "sfx")
    os.makedirs(sfx_dir, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(sfx_dir, f"{args.name}_{n:02d}.wav")):
        n += 1
    out = os.path.join(sfx_dir, f"{args.name}_{n:02d}.wav")
    wavfile.write(out, sr, pcm[int(start * sr):int(end * sr)])
    print(f"Saved {end - start:.1f}s -> {out}")

    edge = 0.15
    if start <= edge:
        rest = pcm[int(end * sr):]
    elif end >= dur - edge:
        rest = pcm[:int(start * sr)]
    else:
        rest = None
    if rest is not None and len(rest) / sr >= args.min_dur:
        wavfile.write(wav, sr, rest)
        row.update(duration=round(len(rest) / sr, 2), reviewed=False)
        print(f"Trimmed {cid} to {row['duration']}s -- its text may no longer match; run `review` to fix it.")
    else:
        os.remove(wav)
        ds.rows.remove(row)
        print(f"Removed {cid} from the speech dataset (nothing usable left, or the sound was mid-clip).")
    ds.save()


def splice(x, sr, cuts):
    """Remove (start, end) second ranges from x, crossfading each join over 30 ms."""
    cuts = sorted((max(0.0, a), min(len(x) / sr, b)) for a, b in cuts)
    pieces, pos = [], 0.0
    for a, b in cuts:
        if a > pos:
            pieces.append(x[int(pos * sr):int(a * sr)])
        pos = max(pos, b)
    if pos < len(x) / sr:
        pieces.append(x[int(pos * sr):])
    if not pieces or sum(len(p) for p in pieces) == 0:
        raise ValueError("nothing left after the cuts")
    xf = int(0.03 * sr)
    out = pieces[0]
    for piece in pieces[1:]:
        k = min(xf, len(out), len(piece))
        ramp = np.linspace(0, 1, k, dtype=np.float32)
        out = np.concatenate([out[:-k], out[-k:] * (1 - ramp) + piece[:k] * ramp, piece[k:]])
    return out


def timeline(x, sr):
    """Text loudness strip (one character per 0.1 s, second marks underneath) to find cut times by eye."""
    n = sr // 10
    db = [20 * math.log10(float(np.sqrt(np.mean(x[i:i + n] ** 2))) + 1e-9) for i in range(0, len(x), n)]
    bars = "".join(" ▁▂▃▄▅▆▇█"[int(np.clip((d + 50) / 50, 0, 1) * 8)] for d in db)
    for row in range(0, len(bars), 60):
        print("  " + bars[row:row + 60])
        print("  " + "".join(f"{(row + c) // 10:<10d}" if (row + c) % 10 == 0 else "" for c in range(0, min(60, len(bars) - row), 10)))


def cut_clip(ds, row, remove=(), keep=None):
    """Cut part of a speech clip in place: drop `remove` ranges, or keep only the `keep` range (seconds)."""
    path = os.path.join(ds.wav_dir, row["id"] + ".wav")
    sr, pcm = wavfile.read(path)
    x = pcm.astype(np.float32) / 32768.0
    cuts = list(remove)
    if keep:
        cuts += [(0.0, keep[0]), (keep[1], len(x) / sr)]
    out = splice(x, sr, cuts)
    if len(out) / sr < 0.3:
        raise ValueError("less than 0.3 s would be left")
    wavfile.write(path, sr, (np.clip(out, -1, 1) * 32767).astype(np.int16))
    row["duration"] = round(len(out) / sr, 2)
    ds.__dict__.setdefault("_envs", {}).pop(row["id"], None)
    ds.save()
    return out


def cmd_cut(args):
    """Cut part of a clip: --remove START END (repeatable) or --keep START END, in seconds."""
    ds = Dataset(args.out, args.sr)
    (cid,) = parse_ids([args.clip])
    row = next((r for r in ds.rows if r["id"] == cid), None)
    if not row:
        sys.exit(f"{cid} not in the dataset.")
    if not (args.remove or args.keep):
        sr, pcm = wavfile.read(os.path.join(ds.wav_dir, cid + ".wav"))
        print(f"{cid} ({row['duration']}s)  \"{row['text']}\"")
        timeline(pcm.astype(np.float32) / 32768.0, sr)
        return
    old = row["duration"]
    try:
        cut_clip(ds, row, args.remove or (), args.keep)
    except ValueError as e:
        sys.exit(f"Not cut: {e}.")
    row["reviewed"] = False
    ds.save()
    print(f"{cid}: {old}s -> {row['duration']}s. Its text may no longer match -- fix it with `review --ids {int(cid[-6:])}`.")


def find_pause(x, sr, margin=0.3):
    """Time (s) of the centre of the quietest stretch, leaning towards the middle of the audio, at least
    `margin` s from either end. None if the audio is too short to split."""
    n = int(0.02 * sr)
    m = len(x) // n
    edge = int(margin / 0.02)
    if m < 2 * edge + 3:
        return None
    db = 20 * np.log10(np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1)) + 1e-9)
    db = uniform_filter1d(db, 7)                       # ~140 ms, so a single dip doesn't win
    idx = np.arange(edge, m - edge)
    score = db[idx] + 6 * np.abs(idx - m / 2) / (m / 2)   # up to 6 dB penalty for being far from the middle
    return (int(idx[np.argmin(score)]) + 0.5) * 0.02


def read_marks(path):
    """Split points (seconds) from a marks file: an Audacity label export ("start<TAB>end<TAB>label" -- both
    ends of a region count, so each region becomes its own piece) or plain numbers, one or more per line.
    Blank lines, '#' comments and Audacity's '\\' spectral lines are ignored."""
    points = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", "\\")):
                continue
            nums = []
            for tok in re.split(r"[\t ,;]+", line):
                try:
                    nums.append(float(tok))
                except ValueError:
                    break                                   # the label text starts here
                if len(nums) == 2:
                    break
            points += nums
    return points


def to_pcm(a):
    return (np.clip(a, -1, 1) * 32767).astype(np.int16)


def make_pieces(x, sr, points):
    """Cut `x` at `points` (seconds). Returns (cut times, pieces). Marks at the very start/end and duplicates
    are dropped; ValueError if nothing is left to cut at or a piece would be shorter than 0.3 s."""
    dur = len(x) / sr
    cuts = []
    for t in sorted(points):
        if t <= 0.05 or t >= dur - 0.05 or (cuts and t - cuts[-1] < 0.05):
            continue
        cuts.append(t)
    if not cuts:
        raise ValueError("every mark is at the start or end of the audio")
    bounds = [0.0] + cuts + [dur]
    short = [f"{a:.2f}-{b:.2f}s" for a, b in zip(bounds[:-1], bounds[1:]) if b - a < 0.3]
    if short:
        raise ValueError(f"piece(s) shorter than 0.3 s ({', '.join(short)}) -- move or drop those marks "
                         f"(the audio is {dur:.2f}s long)")
    fade = int(0.01 * sr)                              # 10 ms fades at each cut, so a cut inside speech doesn't click
    ramp = np.linspace(1, 0, fade, dtype=np.float32)
    ramp = ramp if x.ndim == 1 else ramp[:, None]
    pieces = []
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        part = x[int(round(a * sr)):int(round(b * sr))].copy()
        if i > 0:
            part[:fade] *= ramp[::-1]
        if i < len(bounds) - 2:
            part[-fade:] *= ramp
        pieces.append(part)
    return cuts, pieces


def snap_to_pauses(x, sr, times, window=0.25):
    """Move each time to the quietest moment within `window` s of it. Marks pressed while listening lag
    the audio, and a cut should land in a pause rather than mid-word."""
    n = int(0.01 * sr)
    m = len(x) // n
    db = 20 * np.log10(np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1)) + 1e-9)
    db = uniform_filter1d(db, 5)
    out = []
    for t in times:
        lo, hi = max(0, int((t - window) / 0.01)), min(m, int((t + window) / 0.01) + 1)
        out.append((lo + int(np.argmin(db[lo:hi])) + 0.5) * 0.01 if hi > lo else t)
    return out


def split_dataset_clip(ds, row, pieces, sr):
    """Write `pieces` back: the first keeps `row`'s id, the rest become new clips right after it. Every piece
    starts with blank text and unreviewed (the old text covers all of them). Returns the new rows."""
    old_id = row["id"]
    wavfile.write(os.path.join(ds.wav_dir, old_id + ".wav"), sr, to_pcm(pieces[0]))
    set_text(ds, row, "")
    row.update(duration=round(len(pieces[0]) / sr, 2), reviewed=False)
    new_rows, after = [], row
    for pc in pieces[1:]:
        clip = {"audio": pc, "snr_db": row.get("snr_db", 0.0)}
        if "speaker_sim" in row:
            clip["speaker_sim"] = row["speaker_sim"]
        new = ds.add(clip, row["source"])
        ds.rows.remove(new)
        ds.rows.insert(ds.rows.index(after) + 1, new)  # keep the pieces next to each other, in order
        new_rows.append(new)
        after = new
    ds.__dict__.setdefault("_envs", {}).pop(old_id, None)
    ds.save()
    return new_rows


def cmd_split(args):
    """Split one clip (id) or one WAV file (path) at --at SECONDS [SECONDS ...], at the times in a --marks
    file, or at its longest pause (--auto). N split points give N+1 pieces."""
    if os.path.isfile(args.clip):
        ds, row = None, None
        sr, pcm = wavfile.read(args.clip)
        label = args.clip
    else:
        ds = Dataset(args.out, args.sr)
        (cid,) = parse_ids([args.clip])
        row = next((r for r in ds.rows if r["id"] == cid), None)
        if not row:
            sys.exit(f"{cid} not in the dataset (and no such file).")
        sr, pcm = wavfile.read(os.path.join(ds.wav_dir, cid + ".wav"))
        label = f"{cid}  \"{row['text']}\""
    x = pcm.astype(np.float32) / (np.iinfo(pcm.dtype).max + 1.0 if np.issubdtype(pcm.dtype, np.integer) else 1.0)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    dur = len(x) / sr

    guess = find_pause(mono, sr)
    points = list(args.at or [])
    if args.marks:
        points += read_marks(args.marks)
    if not points and args.auto and guess is not None:
        points = [guess]
    if not points:
        print(f"{label}  ({dur:.2f}s)")
        timeline(mono, sr)
        print(f"Suggested split: {guess:.2f}s -- run again with --at SECONDS [SECONDS ...], --marks FILE, or --auto."
              if guess else "Too short to split.")
        return
    try:
        cuts, pieces = make_pieces(x, sr, points)
    except ValueError as e:
        sys.exit(f"Not split: {e}")
    lens = " + ".join(f"{len(pc) / sr:.2f}s" for pc in pieces)

    if ds is None:
        stem, _ = os.path.splitext(args.clip)
        names = []
        for n, pc in enumerate(pieces, 1):
            wavfile.write(f"{stem}_part{n}.wav", sr, to_pcm(pc))
            names.append(f"{stem}_part{n}.wav")
        print(f"{dur:.2f}s -> {lens}\nsaved " + ", ".join(names))
        return

    old_text, old_id = row["text"], row["id"]
    ids = [old_id] + [n["id"] for n in split_dataset_clip(ds, row, pieces, sr)]
    print(f"{old_id}: {dur:.2f}s -> {lens}  ({', '.join(ids)})")
    if old_text:
        print(f"Old text was: \"{old_text}\" -- the pieces are blank now; "
              f"`review --ids {' '.join(str(int(i[-6:])) for i in ids)}` or add --transcribe to fill them in.")
    if args.transcribe:
        transcribe_missing(ds, args)


def cmd_merge(args):
    """Join clips back into one (e.g. to undo a split): the first clip in the dataset keeps its id and gets the
    joined audio, the others are removed. Nothing is deleted: the first clip's old audio and the removed clips go to
    merged_backup/ in the dataset folder. The 10 ms fades a split put at each cut are undone, so the joint is clean.
    Text: --text if given; else, when every piece is blank (a split leaves them blank), the text the clip had
    before the split, from text_history.jsonl; else the pieces' texts joined with spaces."""
    ds = Dataset(args.out, args.sr)
    if args.next:
        (cid,) = parse_ids(args.clips[:1])
        first = next((r for r in ds.rows if r["id"] == cid), None)
        if not first or len(args.clips) != 1:
            sys.exit("With --next, give exactly one clip that is in the dataset.")
        i = ds.rows.index(first)
        rows = ds.rows[i:i + 1 + args.next]
        if len(rows) < 1 + args.next:
            sys.exit(f"Only {len(rows) - 1} clip(s) follow {cid} in the dataset.")
    else:
        want = parse_ids(args.clips)
        missing = sorted(want - {r["id"] for r in ds.rows})
        if missing:
            sys.exit(f"Not in the dataset: {', '.join(m[-3:].lstrip('0') or '0' for m in missing)}")
        rows = [r for r in ds.rows if r["id"] in want]            # dataset order
        if len(rows) < 2:
            sys.exit("Give at least two clips (or one clip and --next N).")

    parts, sr0 = [], None
    for k, r in enumerate(rows):
        sr, pcm = wavfile.read(os.path.join(ds.wav_dir, r["id"] + ".wav"))
        if sr0 not in (None, sr):
            sys.exit("The clips have different sample rates.")
        sr0 = sr
        x = pcm.astype(np.float32) / 32768.0
        n = min(int(0.01 * sr), len(x) // 2)
        if k > 0:                                                  # undo the fade-in a split put on this piece
            x[:n] /= np.maximum(np.linspace(0, 1, n, dtype=np.float32), 0.1)
        if k < len(rows) - 1:                                      # ... and the fade-out on the one before the joint
            x[len(x) - n:] /= np.maximum(np.linspace(1, 0, n, dtype=np.float32), 0.1)
        parts.append(x)
        if args.gap > 0 and k < len(rows) - 1:
            parts.append(np.zeros(int(args.gap * sr), np.float32))
    joined = np.concatenate(parts)
    peak = float(np.abs(joined).max())
    if peak > 0.97:
        joined *= 0.97 / peak

    target, others = rows[0], rows[1:]
    if args.text is not None:
        text, how = args.text, "from --text"
    elif all(not r["text"].strip() for r in rows):
        text, how = "", "blank (no earlier text found)"
        hist = os.path.join(ds.dir, "text_history.jsonl")
        if os.path.exists(hist):
            with open(hist, encoding="utf-8") as f:
                for line in reversed([ln for ln in f if ln.strip()]):
                    h = json.loads(line)
                    if h["id"] == target["id"] and h["new"] == "" and h["old"]:
                        text, how = h["old"], "restored from before the split (text_history.jsonl)"
                        break
    else:
        text, how = " ".join(r["text"].strip() for r in rows if r["text"].strip()), "the pieces' texts joined"

    bak = os.path.join(ds.dir, "merged_backup")
    os.makedirs(bak, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(os.path.join(ds.wav_dir, target["id"] + ".wav"), os.path.join(bak, f"{target['id']}.before_{stamp}.wav"))
    with open(os.path.join(bak, "merged_backup.jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "merged_into": target["id"], "time": stamp}, ensure_ascii=False) + "\n")
    wavfile.write(os.path.join(ds.wav_dir, target["id"] + ".wav"), sr0, to_pcm(joined))
    for r in others:
        shutil.move(os.path.join(ds.wav_dir, r["id"] + ".wav"), os.path.join(bak, f"{r['id']}.wav"))
        ds.rows.remove(r)
    set_text(ds, target, text)
    target.update(duration=round(len(joined) / sr0, 2), reviewed=False)
    ds.__dict__.setdefault("_envs", {}).pop(target["id"], None)
    ds.save()
    print(f"Merged {', '.join(r['id'] for r in rows)} -> {target['id']} ({target['duration']}s). Text {how}.")
    if text:
        print(f"  text: \"{text}\"")
    print(f"  originals saved in {bak}")
    if target["duration"] > args.max_dur:
        print(f"  note: that is longer than {args.max_dur:g}s, the usual maximum for training clips.")


def cmd_trim(args):
    """Cut ranges out of an sfx file (e.g. a stray voice line inside a laugh), crossfading the joins.
    The original is kept; the result goes next to it as <name>_trimmed.wav."""
    sr, pcm = wavfile.read(args.file)
    x = pcm.astype(np.float32) / 32768.0
    try:
        out = splice(x, sr, args.cut)
    except ValueError as e:
        sys.exit(f"Not trimmed: {e}.")
    stem, ext = os.path.splitext(args.file)
    dst = f"{stem}_trimmed{ext}"
    wavfile.write(dst, sr, (np.clip(out, -1, 1) * 32767).astype(np.int16))
    print(f"{len(x) / sr:.1f}s -> {len(out) / sr:.1f}s  saved {dst}")


def cmd_dedupe(args):
    """Find clips that repeat other clips' audio. Of each pair the shorter one goes (the longer
    covers it); equal lengths keep the cleaner one. Dry run unless --apply."""
    ds = Dataset(args.out, args.sr)
    pairs = []
    for i, a in enumerate(ds.rows):
        for b in ds.rows[i + 1:]:
            corr, frames = overlap_score(ds.env(a), ds.env(b))
            if corr >= DUP_CORR:
                pairs.append((corr, frames, a, b))
    gone = {}
    for corr, frames, a, b in sorted(pairs, key=lambda p: -p[0]):
        if a["id"] in gone or b["id"] in gone:
            continue
        keep, drop = sorted((a, b), key=lambda r: (-r["duration"], -r["snr_db"], r["id"]))
        gone[drop["id"]] = (keep, drop, corr, frames)
    if not gone:
        print("No repeated clips found.")
        return
    for keep, drop, corr, frames in gone.values():
        print(f"remove {drop['id']} ({drop['duration']}s) -- repeated in {keep['id']} ({keep['duration']}s), "
              f"match {corr:.2f} over {frames * 0.04:.1f}s\n    \"{drop['text']}\"")
    if not args.apply:
        print(f"\n{len(gone)} clip(s) would be removed. Run again with --apply to do it.")
        return
    for keep, drop, _, _ in gone.values():
        ds.remove(drop)
    ds.save()
    print(f"\nRemoved {len(gone)} repeated clip(s).")


def cmd_enroll(args):
    """Build the voiceprint the capture/import filter compares against: the embeddings of the dataset's clips
    (by default minus strays -- clips with no close neighbour, e.g. another speaker -- which are left out so they
    don't vouch for each other's kind). --ids pins it to exactly the clips you know are the right voice."""
    ds = Dataset(args.out, args.sr)
    if args.ids:
        rows = [r for r in ds.rows if r["id"] in parse_ids(args.ids)]
    else:
        rows = ds.rows
    if len(rows) < 4:
        sys.exit("Need at least 4 clips to build a voiceprint.")
    print(f"Embedding {len(rows)} clip(s)...")
    E = np.array([ds.embedding(r) for r in rows])
    S = E @ E.T
    np.fill_diagonal(S, -1)

    def loo_scores(mask):
        sub = S[np.ix_(mask, mask)]
        k = min(SPEAKER_K, int(mask.sum()) - 1)
        return np.sort(sub, axis=1)[:, -k:].mean(axis=1)

    keep = np.ones(len(rows), bool)
    if not args.ids:
        for _ in range(4):                     # drop strays, re-score, repeat
            sc = loo_scores(keep)
            med = np.median(sc)
            mad = 1.4826 * np.median(np.abs(sc - med))
            bad = sc < max(0.4, med - 3.5 * mad)
            if not bad.any():
                break
            keep[np.where(keep)[0][bad]] = False
    sc = loo_scores(keep)
    med = float(np.median(sc))
    mad = float(1.4826 * np.median(np.abs(sc - med)))
    threshold = float(np.clip(med - 2.5 * mad, 0.45, 0.7))
    centroid = E[keep].mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    np.savez(os.path.join(ds.dir, VOICEPRINT_NAME), centroid=centroid, threshold=threshold,
             embeddings=E[keep].astype(np.float32), knn_threshold=threshold)
    left_out = [r["id"][-3:].lstrip("0") for r, k in zip(rows, keep) if not k]
    print(f"Voiceprint saved from {int(keep.sum())}/{len(rows)} clips. Typical match (nearest {SPEAKER_K} clips) "
          f"{med:.2f}, threshold {threshold:.2f} (override with --speaker-threshold).")
    if left_out:
        print(f"Left out as strays (no close neighbour): {', '.join(left_out)}")


def cmd_filter(args):
    """Score every clip against the voiceprint and move the ones that sound like someone else out of
    the dataset (into rejected_speaker/, nothing is deleted). Dry run unless --apply."""
    ds = Dataset(args.out, args.sr)
    vp = load_voiceprint(ds.dir)
    if not vp:
        sys.exit("No voiceprint yet -- run `enroll` first.")
    threshold = vp["threshold"]
    if args.speaker_threshold is not None:
        threshold = args.speaker_threshold
    bad = []
    for r in ds.rows:
        r["speaker_sim"] = round(speaker_score(vp, ds.embedding(r)), 3)
        if r["speaker_sim"] < threshold:
            bad.append(r)
    for r in sorted(bad, key=lambda r: r["speaker_sim"]):
        print(f"{r['id']}  match {r['speaker_sim']:.2f}  {r['duration']}s  \"{r['text']}\"")
    print(f"\n{len(bad)} of {len(ds.rows)} clip(s) below {threshold:.2f}.")
    if not args.apply:
        ds.save()
        print("Dry run. Run again with --apply to move them to rejected_speaker/ (or lower --speaker-threshold).")
        return
    rej_dir = os.path.join(ds.dir, "rejected_speaker")
    os.makedirs(rej_dir, exist_ok=True)
    with open(os.path.join(ds.dir, "rejected_speaker.jsonl"), "a", encoding="utf-8") as f:
        for r in bad:
            os.replace(os.path.join(ds.wav_dir, r["id"] + ".wav"), os.path.join(rej_dir, r["id"] + ".wav"))
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            ds.rows.remove(r)
    ds.save()
    print(f"Moved {len(bad)} clip(s) to {rej_dir}.")


def cmd_move(args):
    """Move clips out of the dataset into another dataset folder (same layout: wavs/, manifest.jsonl,
    metadata.csv), keeping their ids. enroll/filter/dedupe/export only look at the main dataset, so what you
    move never influences them. Nothing is deleted: move them back with `move ... --out <that folder> --to <main>`."""
    ds = Dataset(args.out, args.sr)
    dest_dir = args.to if os.sep in args.to or os.path.isabs(args.to) else os.path.join(ROOT, "training", "data", args.to)
    if os.path.abspath(dest_dir) == os.path.abspath(ds.dir):
        print("Source and destination are the same folder.")
        return
    dest = Dataset(dest_dir, args.sr)
    want = parse_ids(args.clips)
    rows = [r for r in ds.rows if r["id"] in want]
    missing = sorted(want - {r["id"] for r in rows})
    if missing:
        print(f"Not in the dataset: {', '.join(str(int(m[-6:])) for m in missing)}")
    clash = sorted({r["id"] for r in rows} & {r["id"] for r in dest.rows})
    if clash:
        print(f"{dest_dir} already has clip(s) with the same id: {', '.join(str(int(c[-6:])) for c in clash)} -- nothing moved.")
        return
    if not rows:
        print("Nothing to move.")
        return
    for r in rows:
        os.replace(os.path.join(ds.wav_dir, r["id"] + ".wav"), os.path.join(dest.wav_dir, r["id"] + ".wav"))
        dest.rows.append(r)
        ds.rows.remove(r)
    dest.save()
    ds.save()
    print(f"Moved {len(rows)} clip(s), {sum(r['duration'] for r in rows) / 60:.1f} min -> {dest_dir}"
          f"  (review them there with `review --out {os.path.relpath(dest_dir, ROOT)}`)")


def cmd_transcribe(args):
    transcribe_missing(Dataset(args.out, args.sr), args)


def cmd_stats(args):
    ds = Dataset(args.out, args.sr)
    if not ds.rows:
        print("Dataset is empty.")
        return
    total = sum(r["duration"] for r in ds.rows)
    texted = sum(1 for r in ds.rows if r["text"])
    print(f"{len(ds.rows)} clips, {total / 60:.1f} min total, {texted} with text, "
          f"mean SNR {np.mean([r['snr_db'] for r in ds.rows]):.1f} dB")
    print("Rough guide: ~1 min for zero-shot cloning (XTTS); 30+ min to fine-tune; 2+ h for a robust voice.")


def cmd_list_devices(args):
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                print(f"{i:3d}  {info['name']}  ({int(info['defaultSampleRate'])} Hz)")
    finally:
        pa.terminate()


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=DEFAULT_OUT, help="dataset directory")
    common.add_argument("--sr", type=int, default=22050, help="output sample rate (22050 suits Piper/XTTS)")
    common.add_argument("--min-dur", type=float, default=1.0, help="drop clips shorter than this (s)")
    common.add_argument("--max-dur", type=float, default=15.0, help="split clips longer than this (s)")
    common.add_argument("--min-silence", type=float, default=0.45, help="pause length that splits clips (s)")
    common.add_argument("--min-snr", type=float, default=15.0, help="reject clips below this speech-to-noise ratio (dB)")
    common.add_argument("--no-dedupe", action="store_true", help="keep new clips even if their audio is already in the dataset")
    common.add_argument("--no-speaker-filter", action="store_true", help="ignore the voiceprint when capturing/importing")
    common.add_argument("--fx", action="store_true", help="review: play the clips with the voice effect applied (originals untouched)")
    common.add_argument("--fx-mix", type=float, default=None, help="override only the vocoder level, 0-1 (default: the live TTS settings)")
    common.add_argument("--fx-dir", default=DEFAULT_FX_DIR, help="where the effect versions go (default: voice_ref/fit_preview/mix30)")
    common.add_argument("--fx-sync", action="store_true",
                        help="after the command, render the effect version of every clip missing from --fx-dir")
    common.add_argument("--any-audio", action="store_true",
                        help="keep any audio: no speaker filter, no noise/clipping/length rejection (for reference "
                             "samples such as processed voices or audio over music; clips are still split at pauses)")
    common.add_argument("--speaker-threshold", type=float, default=None, help="min voice match 0-1 (default: calibrated by enroll)")
    common.add_argument("--denoise", action="store_true", help="apply mild spectral noise gating first")
    common.add_argument("--transcribe", action="store_true", help="transcribe clips without text via Whisper afterwards")
    common.add_argument("--whisper-model", default="small")
    common.add_argument("--language", default=None, help="Whisper language code (default: auto-detect)")
    common.add_argument("--whisper-device", default="cpu", choices=["cpu", "cuda"])

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", parents=[common], help="record from the microphone")
    p.add_argument("--free", action="store_true", help="open-ended talking instead of prompt sentences")
    p.add_argument("--prompts", help="text file with one prompt sentence per line")
    p.add_argument("--mic", type=int, default=None, help="input device index (see list-devices)")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("capture", parents=[common], help="record what the PC is playing (system audio)")
    p.add_argument("--source", help="Linux: PulseAudio/PipeWire source name (default: default output's monitor; see list-sources)")
    p.set_defaults(fn=cmd_capture)
    p = sub.add_parser("list-sources", help="Linux: list PulseAudio/PipeWire sources, incl. output monitors")
    p.set_defaults(fn=cmd_list_sources)

    p = sub.add_parser("import", parents=[common], help="extract clips from audio/video files")
    p.add_argument("paths", nargs="+", help="files or folders (searched recursively)")
    p.add_argument("--force", action="store_true", help="re-import files already in the dataset")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("transcribe", parents=[common], help="Whisper-transcribe clips that have no text")
    p.set_defaults(fn=cmd_transcribe)
    p = sub.add_parser("review", parents=[common], help="listen to clips and correct their text")
    p.add_argument("--all", action="store_true", help="include clips you already reviewed")
    p.add_argument("--ids", nargs="+", help="review only these clips, even if reviewed before: 8 12 15-18")
    p.add_argument("--from", dest="first", metavar="ID", help="start at this clip (skips reviewed ones unless --all)")
    p.add_argument("--to", dest="last", metavar="ID", help="stop after this clip (with --from, or alone for 'up to')")
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("move", parents=[common], help="move clips into another dataset folder (e.g. movie lines kept apart)")
    p.add_argument("clips", nargs="+", help="clip numbers/ids: 377-416 420")
    p.add_argument("--to", required=True, help="destination folder (a name goes under training/data/)")
    p.set_defaults(fn=cmd_move)
    p = sub.add_parser("cut", parents=[common], help="cut part of a speech clip (no range = show its waveform)")
    p.add_argument("clip", help="clip number or id")
    p.add_argument("--remove", nargs=2, type=float, action="append", metavar=("START", "END"),
                   help="drop this range (seconds); repeat for several")
    p.add_argument("--keep", nargs=2, type=float, metavar=("START", "END"), help="keep only this range")
    p.set_defaults(fn=cmd_cut)
    p = sub.add_parser("fx-sync", parents=[common], help="render the effect version of every clip missing from the fx folder")
    p.set_defaults(fn=cmd_fx_sync)
    p = sub.add_parser("merge", parents=[common], help="join clips back into one (undo a split)")
    p.add_argument("clips", nargs="+", help="clip numbers/ids to join, or one clip with --next")
    p.add_argument("--next", type=int, default=0, metavar="N", help="join the clip with the N clips that follow it")
    p.add_argument("--text", default=None, help="text for the joined clip (default: restore / join the pieces' texts)")
    p.add_argument("--gap", type=float, default=0.0, help="seconds of silence between pieces (for clips that were not adjacent)")
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("split", parents=[common], help="split a clip (or a WAV file) in two")
    p.add_argument("clip", help="clip number or id, or the path of a WAV file")
    p.add_argument("--at", type=float, nargs="+", action="extend", metavar="SECONDS",
                   help="where to split; several times make several pieces")
    p.add_argument("--marks", metavar="FILE",
                   help="split at the times in a file: an Audacity label export, or plain times (one or more per line)")
    p.add_argument("--auto", action="store_true", help="split at the longest pause near the middle")
    p.set_defaults(fn=cmd_split)
    p = sub.add_parser("sfx", parents=[common], help="cut a laugh/cough/etc. out of a clip into sfx/")
    p.add_argument("clip", help="clip id or number, e.g. 8 or voice_000008")
    p.add_argument("--start", type=float, default=0.0, help="start of the sound within the clip (s)")
    p.add_argument("--end", type=float, default=None, help="end of the sound (s); default: end of clip")
    p.add_argument("--name", default="laugh", help="file prefix in sfx/")
    p.set_defaults(fn=cmd_sfx)
    p = sub.add_parser("trim", help="cut time ranges out of an sfx file (crossfaded)")
    p.add_argument("file")
    p.add_argument("--cut", nargs=2, type=float, action="append", required=True, metavar=("START", "END"),
                   help="range to remove in seconds; repeat for several")
    p.set_defaults(fn=cmd_trim)
    p = sub.add_parser("dedupe", parents=[common], help="remove clips whose audio repeats another clip")
    p.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    p.set_defaults(fn=cmd_dedupe)
    p = sub.add_parser("enroll", parents=[common], help="learn the target voice from the current clips")
    p.add_argument("--ids", nargs="+", help="clip numbers/ids known to be the right voice (default: auto)")
    p.set_defaults(fn=cmd_enroll)
    p = sub.add_parser("filter", parents=[common], help="move clips that don't match the voiceprint out of the dataset")
    p.add_argument("--apply", action="store_true", help="actually move them (default is a dry run)")
    p.set_defaults(fn=cmd_filter)
    p = sub.add_parser("stats", parents=[common], help="dataset size summary")
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("list-devices", help="list microphone input devices")
    p.set_defaults(fn=cmd_list_devices)

    args = ap.parse_args()
    if getattr(args, "any_audio", False):
        args.no_speaker_filter = True
    args.fn(args)
    if getattr(args, "fx_sync", False) and args.fn is not cmd_fx_sync:
        cmd_fx_sync(args)


if __name__ == "__main__":
    main()
