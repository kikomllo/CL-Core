"""
export_piper_dataset.py — Package the voice dataset for Piper fine-tuning (training/tools/kaggle_piper_train.py).

Reads training/data/voice_dataset (made by collect_voice.py) and writes training/data/voice_export/:
    metadata.csv   two columns, "file.wav|text" (a third column would make Piper read it as multi-speaker)
    wavs/          the clips that passed the filters
    piper_voice_dataset.zip   metadata.csv + wavs/, ready to upload as a Kaggle dataset

Both folders are gitignored. Train on the CLEAN clips: the voice effect is applied after synthesis.

Usage:
    python training/tools/export_piper_dataset.py
    python training/tools/export_piper_dataset.py --exclude 8 11 17 --min-snr 18
    python training/tools/export_piper_dataset.py --deep 75      # only the deepest 75% of clips (by median pitch)
    python training/tools/export_piper_dataset.py --denoise 0.4  # mild spectral noise gate on the exported copies
"""

import argparse
import json
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def clean_text(text):
    """One line, no '|' (the CSV delimiter), single spaces."""
    return re.sub(r"\s+", " ", text.replace("|", " ")).strip()


def write_denoised(src, dst, strength):
    import numpy as np
    from scipy.io import wavfile
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from utils.clVoice import denoise
    sr, pcm = wavfile.read(src)
    y = denoise(pcm.astype(np.float32) / 32768.0, sr, strength, keep_below_hz=250.0)
    wavfile.write(dst, sr, (np.clip(y, -1, 1) * 32767).astype(np.int16))


def median_f0(wav_path):
    """Median pitch (Hz) over the louder half of a clip's voiced frames (autocorrelation), or None if there is no
    clear pitch (laughs, whispers, noise)."""
    import numpy as np
    from scipy import signal
    from scipy.io import wavfile
    sr, pcm = wavfile.read(wav_path)
    x = pcm.astype(np.float32) / 32768.0
    fl, hop, lo, hi = int(0.04 * sr), int(0.01 * sr), int(sr / 350), int(sr / 60)
    starts = range(0, len(x) - fl, hop)
    env = np.array([np.sqrt(np.mean(x[i:i + fl] ** 2)) for i in starts])
    if not len(env):
        return None
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
    return float(np.median(f0)) if len(f0) > 8 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=os.path.join(ROOT, "training", "data", "voice_dataset"))
    ap.add_argument("--out", default=os.path.join(ROOT, "training", "data", "voice_export"))
    ap.add_argument("--min-snr", type=float, default=0.0, help="leave out clips below this SNR (dB)")
    ap.add_argument("--min-dur", type=float, default=0.8, help="leave out clips shorter than this (s)")
    ap.add_argument("--max-dur", type=float, default=15.0, help="leave out clips longer than this (s)")
    ap.add_argument("--exclude", nargs="*", default=[], help="clip numbers to leave out, e.g. 8 11 17")
    ap.add_argument("--denoise", type=float, default=0.0, metavar="0-1",
                    help="noise-gate the exported copies (the dataset itself is untouched); the low end below 250 Hz is "
                         "left alone so a deep voice keeps its weight")
    ap.add_argument("--deep", type=float, metavar="PERCENT",
                    help="keep only the deepest PERCENT%% of the clips by median pitch (leaves out the high, excited ones "
                         "and clips with no clear pitch)")
    args = ap.parse_args()

    manifest = os.path.join(args.src, "manifest.jsonl")
    if not os.path.exists(manifest):
        sys.exit(f"No manifest at {manifest}")
    with open(manifest, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    excluded = {f"voice_{int(n):06d}" for n in args.exclude}
    keep, skipped = [], {}
    for r in rows:
        text = clean_text(r.get("text", ""))
        wav = os.path.join(args.src, "wavs", r["id"] + ".wav")
        why = None
        if r["id"] in excluded:
            why = "excluded by you"
        elif not text:
            why = "no text"
        elif not os.path.exists(wav):
            why = "wav missing"
        elif r["duration"] < args.min_dur:
            why = "too short"
        elif r["duration"] > args.max_dur:
            why = "too long"
        elif r.get("snr_db", 99) < args.min_snr:
            why = "below min SNR"
        if why:
            skipped.setdefault(why, []).append(r["id"][-3:].lstrip("0") or "0")
        else:
            keep.append((r["id"], text, wav, r["duration"]))
    if not keep:
        sys.exit("Nothing to export.")

    if args.deep:
        pitch = {cid: median_f0(wav) for cid, _, wav, _ in keep}
        known = sorted(v for v in pitch.values() if v)
        import numpy as np
        cut = float(np.percentile(known, args.deep))
        kept = []
        for row in keep:
            v = pitch[row[0]]
            if v is None:
                skipped.setdefault("no clear pitch", []).append(row[0][-3:].lstrip("0") or "0")
            elif v > cut:
                skipped.setdefault(f"median pitch above {cut:.0f} Hz", []).append(row[0][-3:].lstrip("0") or "0")
            else:
                kept.append(row)
        keep = kept
        print(f"--deep {args.deep:g}: keeping clips with a median pitch up to {cut:.0f} Hz")

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(os.path.join(args.out, "wavs"))
    with open(os.path.join(args.out, "metadata.csv"), "w", encoding="utf-8") as f:
        for cid, text, wav, _ in keep:
            if args.denoise > 0:
                write_denoised(wav, os.path.join(args.out, "wavs", cid + ".wav"), args.denoise)
            else:
                shutil.copy2(wav, os.path.join(args.out, "wavs", cid + ".wav"))
            f.write(f"{cid}.wav|{text}\n")

    zip_path = os.path.join(args.out, "piper_voice_dataset.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:       # wavs don't compress; store them
        z.write(os.path.join(args.out, "metadata.csv"), "metadata.csv")
        for cid, *_ in keep:
            z.write(os.path.join(args.out, "wavs", cid + ".wav"), f"wavs/{cid}.wav")

    total = sum(d for *_, d in keep)
    print(f"Exported {len(keep)} clips, {total / 60:.1f} min -> {args.out}")
    for why, ids in skipped.items():
        print(f"  skipped ({why}): {len(ids)}  [{', '.join(ids[:15])}{', ...' if len(ids) > 15 else ''}]")
    print(f"Upload {zip_path} as a Kaggle dataset, then run training/tools/kaggle_piper_train.py there.")
    if total < 20 * 60:
        print(f"Note: {total / 60:.1f} min is a smoke test; ~30+ min is where fine-tuning starts to sound dependable.")


if __name__ == "__main__":
    main()
