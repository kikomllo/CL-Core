"""
fit_voice_fx.py — Fit the Ultron effect chain (src/utils/clVoice.py) to a reference recording.

Takes clean speech (the voice dataset) and a reference of the same voice with the effects on
(training/data/voice_ref, collected with `collect_voice.py ... --any-audio`), and searches the chain's
parameters so that the clean speech, once processed, has the same long-term spectrum, comb/ring-mod
fine structure, reverb tail and crest factor as the reference. The two recordings don't need to say
the same words -- only statistics are compared.

Usage:
    python training/tools/fit_voice_fx.py                       # fit, write fitted_fx.json + previews
    python training/tools/fit_voice_fx.py --text "Hello."       # also render a stock-Piper sample
    python training/tools/fit_voice_fx.py --clean-sec 30 --maxiter 60

Output goes to training/data/voice_ref/ (gitignored): fitted_fx.json (paste its "fx" block into
config/core.json -> settings.tts_settings) and fit_preview/*.wav to judge by ear.
"""

import argparse
import functools
import glob
import json
import os
import sys
import wave

import numpy as np
from scipy import signal
from scipy.io import wavfile
from scipy.ndimage import uniform_filter1d
from scipy.optimize import differential_evolution

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from utils.clVoice import DEFAULT_SETTINGS, EQ_CENTRES, VoiceSynth, _pitch_down, apply_eq, apply_fx, merge_settings  # noqa: E402

NPERSEG, HOP = 1024, 256

# (name, low, high) -- the free parameters of apply_fx
PARAMS = [
    ("vocoder_hz", 55.0, 110.0),
    ("vocoder_noise_hi", 0.4, 1.0),
    ("ring_mod_hz", 20.0, 160.0),
    ("ring_mod_mix", 0.0, 0.3),
    ("comb_delay_ms", 2.0, 15.0),
    ("comb_feedback", 0.0, 0.8),
    ("comb_mix", 0.0, 0.8),
    ("drive", 1.0, 3.0),
    ("reverb_seconds", 0.1, 1.5),
    ("reverb_mix", 0.0, 0.5),
]


def load_wavs(folder, limit_sec=None, seed=0):
    paths = sorted(glob.glob(os.path.join(folder, "wavs", "*.wav")))
    if not paths:
        sys.exit(f"No wavs in {folder}/wavs")
    rng = np.random.default_rng(seed)
    rng.shuffle(paths)
    clips, total, sr0 = [], 0.0, None
    for p in paths:
        sr, x = wavfile.read(p)
        x = x.astype(np.float32) / (32768.0 if x.dtype == np.int16 else 1.0)
        if x.ndim > 1:
            x = x.mean(1)
        sr0 = sr0 or sr
        if sr != sr0:
            continue
        x = x[: int(6 * sr)]                       # long clips add cost, not information
        clips.append(x)
        total += len(x) / sr
        if limit_sec and total >= limit_sec:
            break
    return clips, sr0


def features(clips, sr):
    """Level-independent statistics of a set of clips."""
    cols, decays, crests = [], [], []
    for x in clips:
        if len(x) < NPERSEG * 2:
            continue
        _, _, Z = signal.stft(x, sr, nperseg=NPERSEG, noverlap=NPERSEG - HOP, boundary=None)
        P = np.abs(Z) ** 2
        env = 10 * np.log10(P.sum(0) + 1e-12)
        loud = env > np.percentile(env, 60)
        cols.append(P[:, loud])
        # reverb tail: level 100-300 ms after each loud->quiet transition, relative to speech level
        speech = np.percentile(env, 90)
        quiet = env < speech - 20
        a, b = int(0.1 * sr / HOP), int(0.3 * sr / HOP)
        for i in np.where(~quiet[:-1] & quiet[1:])[0]:
            if i + b < len(env):
                decays.append(env[i + a:i + b].mean() - speech)
        fl = int(0.02 * sr)
        fr = x[: len(x) // fl * fl].reshape(-1, fl)
        rms = np.sqrt((fr ** 2).mean(1)) + 1e-9
        keep = rms > np.percentile(rms, 50)
        crests.append(20 * np.log10(np.abs(fr).max(1)[keep] / rms[keep]))
    ltas_db = 10 * np.log10(np.concatenate(cols, 1).mean(1) + 1e-12)
    freqs = np.fft.rfftfreq(NPERSEG, 1 / sr)
    # Log-spaced bands; the lowest start at 130 Hz so every band holds at least one FFT bin (21.5 Hz apart).
    edges = np.geomspace(130, min(10000, sr / 2 - 1), 25)
    bands = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    assert all(b.any() for b in bands), "empty analysis band"
    coarse = np.array([ltas_db[b].mean() for b in bands])
    fine = ltas_db[freqs < 4000]
    fine = fine - uniform_filter1d(fine, 15)
    return {
        "coarse": coarse - coarse.mean(),
        "fine": fine,
        "decay": float(np.median(decays)) if decays else 0.0,
        "crest": float(np.median(np.concatenate(crests))),
    }


def distance(a, b):
    parts = {
        "spectrum": float(np.abs(a["coarse"] - b["coarse"]).mean()),
        "fine": 1.5 * float(np.sqrt(((a["fine"] - b["fine"]) ** 2).mean())),
        "tail": 0.15 * abs(a["decay"] - b["decay"]),
        "crest": 0.5 * abs(a["crest"] - b["crest"]),
    }
    return sum(parts.values()), parts


def ltas_centres(clips, sr):
    """Mean-removed long-term level (dB) at each match-EQ centre, over the loud frames of the clips."""
    cols = []
    for x in clips:
        _, _, Z = signal.stft(x, sr, nperseg=2048, noverlap=1536, boundary=None)
        A = np.abs(Z) ** 2
        e = A.sum(0)
        cols.append(A[:, e > np.percentile(e, 60)])
    L = 10 * np.log10(np.concatenate(cols, 1).mean(1) + 1e-12)
    fr = np.fft.rfftfreq(2048, 1 / sr)
    out = np.array([L[(fr >= c / 2 ** (1 / 6)) & (fr < c * 2 ** (1 / 6))].mean() for c in EQ_CENTRES])
    return out - out.mean()


def to_fx(vec):
    fx = dict(DEFAULT_SETTINGS["fx"])
    fx.update({name: float(v) for (name, _, _), v in zip(PARAMS, vec)})
    fx["enabled"] = True
    return fx


def process(clips, sr, fx):
    return [apply_fx(_pitch_down(x, fx["pitch_semitones"]), sr, fx) for x in clips]


def cost(vec, clips, sr, ref, target):
    """Distance after a one-step match-EQ: the EQ takes care of the spectrum, so the search is free to
    spend its parameters on the vocoder, comb, reverb and saturation."""
    try:
        out = process(clips, sr, to_fx(vec))
        eq = np.clip(target - ltas_centres(out, sr), -30, 30)
        d = distance(features([apply_eq(o, sr, eq) for o in out], sr), ref)[0]
        return d if np.isfinite(d) else 1e3
    except Exception:
        return 1e3


def write_wav(path, x, sr):
    pcm = (np.clip(x / max(1.0, float(np.abs(x).max())), -1, 1) * 32767 * 0.9).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", default=os.path.join(ROOT, "training/data/voice_dataset"), help="clean voice dataset dir")
    ap.add_argument("--ref", default=os.path.join(ROOT, "training/data/voice_ref"), help="effected reference dataset dir")
    ap.add_argument("--clean-sec", type=float, default=20.0, help="seconds of clean speech used per evaluation")
    ap.add_argument("--maxiter", type=int, default=40)
    ap.add_argument("--popsize", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--text", help="also render this sentence with stock Piper + the fitted chain")
    args = ap.parse_args()

    clean, sr = load_wavs(args.clean, args.clean_sec, args.seed)
    ref_clips, sr_ref = load_wavs(args.ref)
    if sr != sr_ref:
        sys.exit(f"Sample rates differ (clean {sr}, reference {sr_ref}); recollect one of them with --sr {sr}.")
    ref = features(ref_clips, sr)
    target = ltas_centres(ref_clips, sr)
    print(f"Clean: {sum(len(c) for c in clean) / sr:.0f}s in {len(clean)} clips | "
          f"Reference: {sum(len(c) for c in ref_clips) / sr:.0f}s in {len(ref_clips)} clips")
    if sum(len(c) for c in ref_clips) / sr < 30:
        print("  note: under 30 s of reference -- the fit will be rough; more reference audio helps.")

    dry = distance(features(clean, sr), ref)
    default = distance(features(process(clean, sr, DEFAULT_SETTINGS["fx"]), sr), ref)
    print(f"Distance to reference  (lower is closer)\n  clean voice: {dry[0]:.2f}   current defaults: {default[0]:.2f}")

    bounds = [(lo, hi) for _, lo, hi in PARAMS]
    print(f"Searching {len(PARAMS)} parameters (maxiter={args.maxiter}, popsize={args.popsize}) ...")
    res = differential_evolution(
        functools.partial(cost, clips=clean, sr=sr, ref=ref, target=target), bounds,
        maxiter=args.maxiter, popsize=args.popsize, seed=args.seed, tol=0.005,
        workers=-1, updating="deferred", polish=False,
    )
    fx = to_fx(res.x)
    eq = np.zeros(len(EQ_CENTRES))
    for _ in range(4):                      # settle the match-EQ on the final chain
        fx["eq_db"] = [float(v) for v in eq]
        eq = np.clip(eq + 0.9 * (target - ltas_centres(process(clean, sr, fx), sr)), -30, 30)
    fx["eq_db"] = [round(float(v), 2) for v in eq]
    fit = distance(features(process(clean, sr, fx), sr), ref)
    print(f"  fitted: {fit[0]:.2f}  ({', '.join(f'{k} {v:.2f}' for k, v in fit[1].items())})")
    print("Fitted fx:")
    for k in [n for n, _, _ in PARAMS]:
        print(f"  {k:16s} {fx[k]:8.2f}")
    print(f"  eq_db            {np.round(fx['eq_db'], 1).tolist()}")

    out_json = os.path.join(args.ref, "fitted_fx.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"fx": {k: round(v, 3) if isinstance(v, float) else v for k, v in fx.items()},
                   "distance": {"clean": dry[0], "defaults": default[0], "fitted": fit[0]}}, f, indent=2)
    prev = os.path.join(args.ref, "fit_preview")
    os.makedirs(prev, exist_ok=True)
    for i, x in enumerate(clean[:3]):
        write_wav(os.path.join(prev, f"clip{i}_1_clean.wav"), x, sr)
        write_wav(os.path.join(prev, f"clip{i}_2_defaults.wav"), process([x], sr, DEFAULT_SETTINGS["fx"])[0], sr)
        write_wav(os.path.join(prev, f"clip{i}_3_fitted.wav"), process([x], sr, fx)[0], sr)
    for i, x in enumerate(ref_clips[:3]):
        write_wav(os.path.join(prev, f"ref{i}.wav"), x, sr)
    if args.text:
        settings = merge_settings({"fx": fx})
        VoiceSynth(settings).synthesize_to_file(args.text, os.path.join(prev, "piper_fitted.wav"))
    print(f"\nSaved {out_json}\nPreviews in {prev}  (clipN_1_clean -> _2_defaults -> _3_fitted; refN = the target)")


if __name__ == "__main__":
    main()
