"""Finds clips where somebody other than the target speaker talks INSIDE the clip (a film dialogue exchange cut as one clip).

The speaker filter in collect_voice.py gives a whole clip one embedding, so a clip that is mostly the actor with one
line from a second speaker still matches the actor and passes. This scores every sentence (Whisper segment) of every
clip on its own against the other clips (leave-one-clip-out, so a clip cannot vouch for itself) and reports the
sentences that do not sound like the rest, with their pitch (a woman is ~200 Hz, this actor ~90 Hz).

    python training/tools/voice_speaker_check.py training/data/voice_export_ultron      # report + outputs/voice_speaker_check/
    python training/tools/voice_speaker_check.py play                                  # hear the flagged sentences, label first

Nothing is changed: it writes outputs/voice_speaker_check/flagged.json (used by voice_slice_words.py --exclude-flagged)
and short wavs of the flagged sentences so they can be confirmed by ear."""
import argparse
import json
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "training", "tools"), os.path.join(ROOT, "src"), os.path.join(ROOT, "src", "utils")]
OUT = os.path.join(ROOT, "outputs", "voice_speaker_check")
MIN_RELIABLE_S = 1.0            # speaker embeddings of shorter audio are not trustworthy


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()


def write_wav(path, x, sr):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def median_f0(x, sr):
    """Median pitch over voiced 40 ms frames (autocorrelation); 0 when nothing is clearly voiced."""
    n, hop = int(0.04 * sr), int(0.02 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    out = []
    for i in range(0, len(x) - n, hop):
        f = x[i:i + n] - x[i:i + n].mean()
        if np.sqrt((f ** 2).mean()) < 0.01:
            continue
        ac = np.correlate(f, f, "full")[n - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.5:
            out.append(sr / k)
    return float(np.median(out)) if len(out) >= 5 else 0.0


def check(a):
    import collect_voice as cv
    if sys.platform == "win32":          # CTranslate2 needs the CUDA DLLs the venv's nvidia wheels ship (same fix as clWhisper.py)
        for base_dir in sys.path:
            for sub in ("cublas", "cudnn"):
                d = os.path.join(base_dir, "nvidia", sub, "bin")
                if os.path.isdir(d):
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    try:
        whisper = WhisperModel("small.en", device="cuda", compute_type="float16")
    except Exception:
        whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    enc = cv.SpeakerEncoder()

    base = os.path.abspath(a.dataset)
    rows = [line.rstrip("\n").split("|", 1) for line in open(os.path.join(base, "metadata.csv"), encoding="utf-8") if "|" in line]
    clips, whole = {}, []
    for n, (name, text) in enumerate(rows, 1):
        x, sr = read_wav(os.path.join(base, "wavs", name))
        segs, _ = whisper.transcribe(os.path.join(base, "wavs", name), language="en", beam_size=5)
        segs = [(s.start, s.end, s.text.strip()) for s in segs if s.end - s.start > 0.15]
        clips[name] = dict(text=text, x=x, sr=sr, segs=segs, whole=enc.embed(x, sr))
        if n % 60 == 0:
            print(f"  ... {n}/{len(rows)} clips", flush=True)
    names = list(clips)
    bank = np.array([clips[n]["whole"] for n in names])

    results = []
    for i, name in enumerate(names):
        c = clips[name]
        others = np.delete(bank, i, axis=0)                      # leave-one-clip-out
        for (s, e, text) in c["segs"]:
            seg = c["x"][int(s * c["sr"]):int(e * c["sr"])]
            sims = np.sort(others @ enc.embed(seg, c["sr"]))[-3:].mean()
            results.append(dict(clip=name, start=round(s, 2), end=round(e, 2), text=text, score=float(sims),
                                f0=round(median_f0(seg, c["sr"])), reliable=(e - s) >= MIN_RELIABLE_S))
    sc = np.array([r["score"] for r in results if r["reliable"]])
    med = float(np.median(sc))
    mad = float(1.4826 * np.median(np.abs(sc - med)))
    cut = max(med - a.sigma * mad, 0.0)
    flagged = [r for r in results if (r["reliable"] and r["score"] < cut) or (a.max_f0 and r["f0"] > a.max_f0)]
    print(f"\n{len(results)} sentences in {len(names)} clips; typical match {med:.2f}, flag below {cut:.2f} (median - {a.sigma} MAD) "
          + (f"or pitch above {a.max_f0:.0f} Hz" if a.max_f0 else "(pitch is shown, not used: autocorrelation octave-errors on this audio)"))
    print(f"{len(flagged)} sentences flagged, in {len({r['clip'] for r in flagged})} clips:")
    for r in sorted(flagged, key=lambda r: r["score"]):
        print(f"  {r['score']:.2f}  {r['f0']:3d} Hz  {r['clip']} {r['start']:.1f}-{r['end']:.1f}s  \"{r['text'][:70]}\"")

    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    for k, r in enumerate(sorted(flagged, key=lambda r: r["score"]), 1):
        c = clips[r["clip"]]
        r["preview"] = f"flag_{k:03d}.wav"
        write_wav(os.path.join(OUT, r["preview"]), c["x"][int(r["start"] * c["sr"]):int(r["end"] * c["sr"])], c["sr"])
    json.dump(dict(flagged=flagged, cut=cut, median=med, all=results), open(os.path.join(OUT, "flagged.json"), "w"), indent=1)


def play(a):
    from pygame import mixer
    core = json.load(open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8"))
    dev = core["settings"].get("audio_settings", {}).get("output_device")
    kw = dict(frequency=22050, size=-16, channels=1, buffer=1024)
    if dev and dev != "System Default":
        kw["devicename"] = dev
    mixer.init(**kw)
    flagged = json.load(open(os.path.join(OUT, "flagged.json")))["flagged"]
    time.sleep(1)
    for r in flagged[: a.limit]:
        print(f"{r['preview']}  match {r['score']:.2f}  {r['f0']} Hz  from {r['clip']}  \"{r['text'][:70]}\"", flush=True)
        s = mixer.Sound(os.path.join(OUT, r["preview"]))
        s.play()
        time.sleep(s.get_length() + 0.8)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dataset", help="a dataset dir, or the word 'play'")
    p.add_argument("--sigma", type=float, default=3.0, help="flag sentences this many robust SDs below the typical match")
    p.add_argument("--max-f0", type=float, default=0.0, help="also flag sentences with a median pitch above this (Hz); off by default, the pitch tracker octave-errors")
    p.add_argument("--limit", type=int, default=30)
    a = p.parse_args()
    play(a) if a.dataset == "play" else check(a)
