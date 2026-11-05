"""
voice_eval.py -- Score trained Piper voices without listening, to rank checkpoints (the final call is still by ear).

For every .onnx given it renders a fixed set of sentences with stock Piper (no effect chain) and measures:
  spk    speaker similarity to the enrolled voice (training/data/voice_dataset/voiceprint.npz), the actor's own clips; higher = closer
  ultron the same against the Ultron lines set aside in training/data/ultron_lines (mean similarity to their embeddings); higher = closer
  wer    word error rate of a Whisper transcription of the renders against the text; lower = clearer, fewer garbled or glitchy words
  f0     median pitch of the renders (Hz), next to the real clips' median
The renders are written to outputs/voice_eval/<voice name>/ so the best ones can be auditioned afterwards (with no training job running).

Usage:
    python training/tools/voice_eval.py models/piper/actor_voice.onnx path/to/other.onnx
    python training/tools/voice_eval.py --whisper-model small --device cpu a.onnx b.onnx
"""
import argparse
import glob
import json
import os
import re
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SENTENCES = [
    "Darkness. Efficient.",
    "Brightness at forty percent. Precisely as asked.",
    "I couldn't reach the bedroom light. Even I have limits.",
    "Restarting every module. A small death, then a better version.",
    "Your alarm is set for six forty five tomorrow morning.",
    "There are no strings on me.",
    "This is Feel Good Inc. by Gorillaz, from Demon Days. Next in the queue is Clint Eastwood.",
    "Everyone creates the thing they dread. Men of peace create engines of war.",
]


def read_wav(path):
    with wave.open(path) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    return x, sr


def median_f0(x, sr):
    from scipy import signal
    fl, hop, lo, hi = int(0.04 * sr), int(0.01 * sr), int(sr / 350), int(sr / 60)
    starts = range(0, len(x) - fl, hop)
    env = np.array([np.sqrt(np.mean(x[i:i + fl] ** 2)) for i in starts])
    f0 = []
    for e, i in zip(env, starts):
        if e < np.percentile(env, 50):
            continue
        w = x[i:i + fl] - x[i:i + fl].mean()
        ac = signal.correlate(w, w, mode="full")[fl - 1:]
        if ac[0] <= 0:
            continue
        j = lo + int(np.argmax(ac[lo:hi]))
        if ac[j] / ac[0] > 0.5:
            f0.append(sr / j)
    return float(np.median(f0)) if len(f0) > 8 else float("nan")


def words(s):
    return re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), int)
    d[:, 0], d[0, :] = range(len(r) + 1), range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[-1, -1] / max(1, len(r))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("voices", nargs="+", help=".onnx voices (each with its .onnx.json next to it)")
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--device", default="cpu", help="whisper device (cpu keeps the GPU free for a training run)")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "voice_eval"))
    ap.add_argument("--noise-scale", type=float, default=None, help="Piper noise_scale (voice default if unset): lower = steadier, less lively")
    ap.add_argument("--noise-w", type=float, default=None, help="Piper noise_w_scale (phoneme length randomness)")
    ap.add_argument("--repeats", type=int, default=1, help="render every sentence this many times and average (Piper samples randomly, so one pass is noisy)")
    args = ap.parse_args()

    sys.argv = [sys.argv[0]]
    import collect_voice as cv
    from piper import PiperVoice, SynthesisConfig

    enc = cv.SpeakerEncoder()
    vp = cv.load_voiceprint(os.path.join(ROOT, "training", "data", "voice_dataset"))
    ult = []
    for p in sorted(glob.glob(os.path.join(ROOT, "training", "data", "ultron_lines", "wavs", "*.wav")))[:60]:
        x, sr = read_wav(p)
        ult.append(enc.embed(x, sr))
    ult = np.array(ult)
    real_f0 = float(np.nanmedian([median_f0(*read_wav(p)) for p in sorted(glob.glob(
        os.path.join(ROOT, "training", "data", "voice_dataset", "wavs", "*.wav")))[:40]]))

    from faster_whisper import WhisperModel
    cv.add_cuda_dll_dirs()
    wh = WhisperModel(args.whisper_model, device=args.device, compute_type="int8" if args.device == "cpu" else "float16",
                      download_root=cv.MODEL_DIR)

    print(f"real clips: median f0 {real_f0:.0f} Hz | speaker score of the dataset's own clips is the ceiling")
    print(f"{'voice':44s} {'spk':>6s} {'ultron':>7s} {'wer':>6s} {'f0':>6s} {'rank':>6s}   (rank = mean(spk, ultron) - wer/2 - |f0 - real|/500, higher is better)")
    for path in args.voices:
        name = os.path.splitext(os.path.basename(path))[0]
        voice = PiperVoice.load(path)
        outd = os.path.join(args.out, name + ("" if args.noise_scale is None and args.noise_w is None else f"_ns{args.noise_scale}_nw{args.noise_w}"))
        os.makedirs(outd, exist_ok=True)
        spk, ul, errs, f0s = [], [], [], []
        for k, text in (kt for r in range(args.repeats) for kt in enumerate(SENTENCES)):
            chunks = list(voice.synthesize(text, SynthesisConfig(noise_scale=args.noise_scale, noise_w_scale=args.noise_w)))
            sr = chunks[0].sample_rate
            x = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
            with wave.open(os.path.join(outd, f"{k}.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
            e = enc.embed(x, sr)
            spk.append(cv.speaker_score(vp, e))
            ul.append(float(np.sort(ult @ e)[-3:].mean()))
            segs, _ = wh.transcribe(x if sr == 16000 else cv.resample(x, sr, 16000), language="en", beam_size=3)
            errs.append(wer(text, " ".join(s.text for s in segs)))
            f0s.append(median_f0(x, sr))
        rank = (np.mean(spk) + np.mean(ul)) / 2 - np.mean(errs) / 2 - abs(np.nanmedian(f0s) - real_f0) / 500
        print(f"{name[:44]:44s} {np.mean(spk):6.3f} {np.mean(ul):7.3f} {np.mean(errs):6.2f} {np.nanmedian(f0s):6.0f} {rank:6.3f}", flush=True)


if __name__ == "__main__":
    main()
