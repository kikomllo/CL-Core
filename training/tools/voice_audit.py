"""Audits a Piper training set (dir with metadata.csv `file.wav|text` and wavs/) for the things that make a trained voice
mumble or swallow letters: a transcript that does not match what is said (Whisper re-transcription), a clip cut right at
a word edge (no silence before/after), very fast speech, a low signal-to-noise ratio, clipping, very short clips.

    python training/tools/voice_audit.py training/data/voice_export_ultron            # report + outputs/voice_audit.csv
    python training/tools/voice_audit.py training/data/voice_export_ultron --no-whisper # signal checks only (fast)
    python training/tools/voice_audit.py DIR --drop-list outputs/drop.txt             # write the flagged ids, one per line

Nothing is deleted or edited: the report is a list to review (`collect_voice.py review` to fix a transcript or re-cut)."""
import argparse
import csv
import os
import re
import sys
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def read_wav(path):
    with wave.open(path) as w:
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), np.int16).astype(np.float32) / 32768
    return (x.reshape(-1, ch).mean(1) if ch > 1 else x), sr


def frame_db(x, sr, ms=10):
    n = int(sr * ms / 1000)
    frames = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)


def edge_silence_ms(db, ms=10):
    """Time before the first and after the last frame clearly above the clip's own noise floor (10th percentile
    + 8 dB, never below -50 dBFS): a fixed distance under the peak counts the noise itself as speech on noisy clips."""
    floor = max(np.percentile(db, 10) + 8.0, -50.0)
    active = np.where(db > floor)[0]
    if len(active) == 0:
        return 0, 0
    return int(active[0] * ms), int((len(db) - 1 - active[-1]) * ms)


def norm_words(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def wer(ref, hyp):
    r, h = norm_words(ref), norm_words(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / len(r)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dataset")
    p.add_argument("--no-whisper", action="store_true")
    p.add_argument("--whisper-model", default="small.en", help="bigger than the live one: this runs once, accuracy matters")
    p.add_argument("--device", default="cuda", choices=["cuda", "cpu"], help="Whisper device (cuda falls back to cpu if unavailable)")
    p.add_argument("--drop-list")
    a = p.parse_args()

    base = os.path.abspath(a.dataset)
    rows = [line.rstrip("\n").split("|", 1) for line in open(os.path.join(base, "metadata.csv"), encoding="utf-8") if "|" in line]
    model = None
    if not a.no_whisper:
        if sys.platform == "win32":       # CTranslate2 needs the CUDA DLLs the venv's nvidia wheels ship (same fix as clWhisper.py)
            for base_dir in sys.path:
                for sub in ("cublas", "cudnn"):
                    d = os.path.join(base_dir, "nvidia", sub, "bin")
                    if os.path.isdir(d):
                        os.add_dll_directory(d)
                        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        from faster_whisper import WhisperModel
        try:
            if a.device != "cuda":
                raise RuntimeError("cpu requested")
            model = WhisperModel(a.whisper_model, device="cuda", compute_type="float16")
            print("Whisper on the GPU", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"Whisper on the CPU ({e})", file=sys.stderr, flush=True)
            model = WhisperModel(a.whisper_model, device="cpu", compute_type="int8")

    out = []
    for n, (name, text) in enumerate(rows, 1):
        x, sr = read_wav(os.path.join(base, "wavs", name))
        db = frame_db(x, sr)
        dur = len(x) / sr
        lead, trail = edge_silence_ms(db)
        noise_db = np.percentile(db, 10)
        snr = np.percentile(db, 95) - noise_db
        cps = len(text) / max(dur, 0.1)
        clip = float((np.abs(x) > 0.99).mean())
        w = None
        if model is not None:
            segs, _ = model.transcribe(os.path.join(base, "wavs", name), language="en", beam_size=5, vad_filter=False)
            w = wer(text, " ".join(s.text for s in segs))
        flags = []
        if w is not None and w > 0.35:
            flags.append(f"transcript mismatch (WER {w:.2f})")
        if lead < 40 or trail < 40:
            flags.append(f"cut tight (lead {lead} ms, tail {trail} ms)")
        if cps > 18:
            flags.append(f"very fast ({cps:.0f} chars/s)")
        if snr < 25:
            flags.append(f"noisy (SNR {snr:.0f} dB)")
        if clip > 0.001:
            flags.append(f"clipping ({clip * 100:.2f}%)")
        if dur < 1.0:
            flags.append(f"very short ({dur:.1f}s)")
        out.append(dict(file=name, text=text, dur=round(dur, 2), lead_ms=lead, tail_ms=trail, snr_db=round(float(snr), 1),
                        chars_per_s=round(cps, 1), wer="" if w is None else round(w, 2), flags="; ".join(flags)))
        if n % 40 == 0:
            print(f"  ... {n}/{len(rows)}", file=sys.stderr, flush=True)

    os.makedirs(os.path.join(ROOT, "outputs"), exist_ok=True)
    report = os.path.join(ROOT, "outputs", "voice_audit.csv")
    with open(report, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0]))
        wr.writeheader()
        wr.writerows(out)

    flagged = [r for r in out if r["flags"]]
    total_min = sum(r["dur"] for r in out) / 60
    print(f"{len(out)} clips, {total_min:.1f} min; {len(flagged)} flagged. Full table: {report}")
    kinds = {}
    for r in flagged:
        for fl in r["flags"].split("; "):
            kinds[fl.split(" (")[0]] = kinds.get(fl.split(" (")[0], 0) + 1
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    print("\nWorst first:")
    for r in sorted(flagged, key=lambda r: -len(r["flags"].split("; ")))[:25]:
        print(f"  {r['file']}  {r['flags']}\n      \"{r['text'][:90]}\"")
    if a.drop_list:
        with open(a.drop_list, "w") as f:
            f.write("\n".join(r["file"] for r in flagged) + "\n")


if __name__ == "__main__":
    main()
