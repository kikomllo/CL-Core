"""Rebuild the Ryan training clips from the cached edge-tts mp3s with the codec damage removed.

The 48 kbps mp3s have unstable highs (the encoder punches different holes every frame) and long runs of exact digital
silence; a fine-tuned Piper reproduces both as glitchy noise. This low-passes below the unstable region, fills the
digital silence with faint noise, caps long pauses and levels the clips.

    python training/tools/voice_clean_data.py [--cutoff 7000] [--out data/voice_training/ryan_v2]
"""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly, sosfiltfilt, butter

ROOT = Path(__file__).resolve().parents[2]
SRC_SR, OUT_SR = 24000, 22050
FRAME = 480  # 20 ms at 24 kHz
SILENCE_DB = -55  # frames below this count as silence
MAX_PAUSE_S = 0.30
EDGE_PAD_S = 0.10
TARGET_RMS_DB = -20


def decode(mp3: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(SRC_SR), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768


def tidy_silence(x: np.ndarray) -> np.ndarray:
    """Trim the edges, cap internal pauses and replace digital silence with faint noise."""
    n = len(x) // FRAME
    frames = x[: n * FRAME].reshape(n, FRAME)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    voiced = db > SILENCE_DB
    if not voiced.any():
        return x
    first, last = np.argmax(voiced), n - 1 - np.argmax(voiced[::-1])
    pad = int(EDGE_PAD_S * SRC_SR / FRAME)
    keep = []
    run = 0
    for i in range(max(0, first - pad), min(n, last + pad + 1)):
        run = 0 if voiced[i] else run + 1
        if run * FRAME / SRC_SR <= MAX_PAUSE_S:
            keep.append(frames[i])
    y = np.concatenate(keep)
    rng = np.random.default_rng(0)
    quiet = np.abs(y) < 1e-4
    y[quiet] = rng.normal(0, 3e-4, quiet.sum())  # about -70 dBFS
    return y


def clean(x: np.ndarray, cutoff: float) -> np.ndarray:
    y = tidy_silence(x)
    y = sosfiltfilt(butter(8, cutoff, btype="low", fs=SRC_SR, output="sos"), y)
    y = resample_poly(y, 147, 160)  # 24000 -> 22050
    rms = np.sqrt((y ** 2).mean())
    y = y * (10 ** (TARGET_RMS_DB / 20) / rms)
    peak = np.abs(y).max()
    if peak > 0.9:
        y = y * (0.9 / peak)
    return y


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=float, default=7000)
    ap.add_argument("--out", default="data/voice_training/ryan_v2")
    args = ap.parse_args()

    base = ROOT / "data/voice_training/ryan"
    out = ROOT / args.out
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    source = json.loads((base / "summary.json").read_text(encoding="utf-8"))["source"]
    for name, mp3 in source:
        y = clean(decode(ROOT / "assets" / mp3), args.cutoff)
        wavfile.write(out / "wavs" / f"{name}.wav", OUT_SR, (y * 32767).astype(np.int16))
    shutil.copy(base / "metadata_piper.csv", out / "metadata.csv")  # `file.wav|text`, the format Piper reads
    print(f"{len(source)} clips -> {out} (low-pass {args.cutoff:.0f} Hz)")


if __name__ == "__main__":
    main()
