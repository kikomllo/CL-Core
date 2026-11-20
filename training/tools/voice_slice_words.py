"""Builds a v2 Piper training set from an existing one: every clip denoised, plus SHORT clips (1-3 words) sliced out of
the longer ones. The v1 set has almost nothing under two seconds, so the trained voice cannot say "Good evening." or
"Done." (it drags, swaps or eats the word); short lines are exactly what a terse persona says most.

How a slice is made: Whisper (GPU) gives word timestamps; a 1-3 word window is kept only when it is a trustworthy
fragment (every word confident, the clip's transcript matches what Whisper hears) and sits between two real pauses
(or the clip's edges), so no word is ever cut mid-sound. The cut goes at the quietest 10 ms inside each pause, with a
little digital silence on both sides. Nothing is invented: the label is the words Whisper heard (capitalised, ending
in a full stop), and the audio is the actor's own.

    python training/tools/voice_slice_words.py build training/data/voice_export_ultron      # -> ..._v2 + outputs/voice_v2_preview
    python training/tools/voice_slice_words.py play                                          # listen to the previews, label first

The source set is never modified. Review the previews by ear before training on the result."""
import argparse
import csv
import os
import random
import re
import shutil
import sys
import time
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "src", "utils"), os.path.dirname(os.path.abspath(__file__))]
PREVIEW = os.path.join(ROOT, "outputs", "voice_v2_preview")
FRAME = 0.01
DANGLING = {"the", "a", "an", "but", "and", "or", "so", "to", "of", "in", "on", "at", "for", "with", "that", "if", "when", "as",
            "than", "can", "will", "would", "could", "couldn't", "should", "is", "are", "was", "am", "be", "i", "i'm", "mean",
            "see", "your", "my", "his", "her", "their", "its", "our"}   # a fragment that ends on one of these hangs mid-sentence: unfinished intonation as a standalone line
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "jr", "sr", "vs", "mt", "lt", "gen", "col", "sgt", "capt", "prof"}   # espeak reads "Dr." as "doctor": not a faithful label for a fragment


def write_wav(path, x, sr):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def clean_word(w):
    return re.sub(r"[^A-Za-z']", "", w).strip("'")


def quietest_point(db, t0, t1):
    """Time of the quietest 10 ms frame between t0 and t1 (seconds)."""
    a, b = int(max(0, t0) / FRAME), max(int(t1 / FRAME), int(max(0, t0) / FRAME) + 1)
    seg = db[a:b]
    return (a + int(np.argmin(seg))) * FRAME if len(seg) else t0


def candidates(words, db, lo, hi, min_prob, min_gap, max_words, total):
    """1..max_words consecutive words that sit between pauses of at least min_gap seconds (or the run's edges).
    `lo`/`hi` bound the run in the clip (a removed sentence next to it, or the clip's own edges 0 and `total`)."""
    out = []
    n = len(words)
    for i in range(n):
        gap_before = words[i][1] - (words[i - 1][2] if i else lo)
        if i and gap_before < min_gap:
            continue
        for k in range(1, max_words + 1):
            j = i + k - 1
            if j >= n:
                break
            gap_after = (words[j + 1][1] if j + 1 < n else hi) - words[j][2]
            if j + 1 < n and gap_after < min_gap:
                continue
            toks = [clean_word(w[0]) for w in words[i:j + 1]]
            if not all(toks) or min(w[3] for w in words[i:j + 1]) < min_prob:
                continue
            if any(tok.lower() in ABBREVIATIONS or len(tok) == 1 and tok.lower() not in ("i", "a") for tok in toks):
                continue
            if i:
                start = quietest_point(db, words[i - 1][2], words[i][1])
            else:
                start = quietest_point(db, lo, words[i][1]) if lo > 0 else max(0.0, words[i][1] - 0.04)
            if j + 1 < n:
                end = quietest_point(db, words[j][2], words[j + 1][1])
            else:
                end = quietest_point(db, words[j][2], hi) if hi < total else min(total, words[j][2] + 0.04)
            if end - start < 0.3:
                continue
            if toks[-1].lower() in DANGLING and j + 1 < n:      # only the clip's own ending can end on one of these
                continue
            text = " ".join(toks)
            out.append(dict(text=text[0].upper() + text[1:] + ".", start=start, end=end, k=k,
                            prob=min(w[3] for w in words[i:j + 1])))
    return out


def load_removals(a):
    """clip name -> [(start, end)] of the sentences that are somebody else's, from voice_speaker_check.py's
    flagged.json, limited to the flag numbers the owner confirmed by ear as NOT the actor (--drop-flags)."""
    if not a.speaker_report or not a.drop_flags:
        return {}
    import json
    drop = {int(x) for x in a.drop_flags.split(",") if x.strip()}
    out = {}
    for r in json.load(open(a.speaker_report, encoding="utf-8"))["flagged"]:
        if int(r["preview"][5:8]) in drop:
            out.setdefault(r["clip"], []).append((r["start"], r["end"]))
    return out


def build(a):
    import voice_audit as audit
    from utils.clVoice import denoise
    if sys.platform == "win32":          # CTranslate2 needs the CUDA DLLs the venv's nvidia wheels ship (same fix as clWhisper.py)
        for base_dir in sys.path:
            for sub in ("cublas", "cudnn"):
                d = os.path.join(base_dir, "nvidia", sub, "bin")
                if os.path.isdir(d):
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(a.whisper_model, device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel(a.whisper_model, device="cpu", compute_type="int8")

    src = os.path.abspath(a.dataset)
    dst = src.rstrip("\\/") + "_v2"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.join(dst, "wavs"))
    rows = [line.rstrip("\n").split("|", 1) for line in open(os.path.join(src, "metadata.csv"), encoding="utf-8") if "|" in line]
    removals = load_removals(a)
    pad = np.zeros(int(0.08 * 22050), dtype=np.float32)
    fade = int(0.008 * 22050)
    all_cands, meta, cut_clips, dropped_s = [], [], 0, 0.0
    for n, (name, text) in enumerate(rows, 1):
        x, sr = audit.read_wav(os.path.join(src, "wavs", name))
        clean = denoise(x, sr, a.denoise) if a.denoise > 0 else x
        total = len(x) / sr
        db = audit.frame_db(clean, sr)
        segs, _ = model.transcribe(os.path.join(src, "wavs", name), language="en", beam_size=5, word_timestamps=True)
        words = [(w.word, w.start, w.end, w.probability) for s in segs for w in (s.words or [])]
        cuts = removals.get(name, [])
        if not cuts:
            write_wav(os.path.join(dst, "wavs", name), clean, sr)
            meta.append([name, text])
            if not words or audit.wer(text, " ".join(w[0] for w in words)) > a.max_wer:
                continue                                            # a transcript Whisper disagrees with: no slices from it
            runs = [(words, 0.0, total)]
        else:
            # somebody else talks inside this clip: cut those sentences out, keep the actor's runs as their own clips
            cut_clips += 1
            dropped_s += sum(c[1] - c[0] for c in cuts)

            def inside(w, c):
                mid = (w[1] + w[2]) / 2
                share = max(0.0, min(w[2], c[1]) - max(w[1], c[0])) / max(w[2] - w[1], 1e-3)
                return share > 0.5 or c[0] - 0.15 <= mid <= c[1] + 0.15

            gone = [w for w in words if any(inside(w, c) for c in cuts)]
            runs, cur = [], []
            for w in words:
                if w in gone:
                    if cur:
                        runs.append(cur)
                    cur = []
                else:
                    cur.append(w)
            if cur:
                runs.append(cur)
            bounded = []
            for r in runs:
                before = [w for w in gone if w[2] <= r[0][1] + 1e-6]
                after = [w for w in gone if w[1] >= r[-1][2] - 1e-6]
                while after and r and clean_word(r[-1][0]).lower() in DANGLING:      # the cut left the start of the removed sentence
                    r = r[:-1]
                while before and len(r) > 1 and r[0][0].strip()[:1].islower() and not before[-1][0].strip().endswith((".", "?", "!")):
                    r = r[1:]                                                     # the cut left the tail of the removed sentence
                if r:
                    bounded.append((r, before[-1][2] if before else 0.0, after[0][1] if after else total))
            runs = bounded
            for k, (r, lo, hi) in enumerate(runs, 1):
                s0 = quietest_point(db, lo, r[0][1]) if lo > 0 else max(0.0, r[0][1] - 0.04)
                s1 = quietest_point(db, r[-1][2], hi) if hi < total else min(total, r[-1][2] + 0.04)
                if s1 - s0 >= 1.0:
                    piece = f"{os.path.splitext(name)[0]}_p{k}.wav"
                    write_wav(os.path.join(dst, "wavs", piece), clean[int(s0 * sr):int(s1 * sr)], sr)
                    meta.append([piece, " ".join(w[0].strip() for w in r)])
        snr = float(np.percentile(db, 95) - np.percentile(db, 10))
        for r, lo, hi in runs:
            if not r:
                continue
            for c in candidates(r, db, lo, hi, a.min_prob, a.min_gap, 3, total):
                c.update(src=name, snr=snr, audio=clean[int(c["start"] * sr):int(c["end"] * sr)].copy(), sr=sr)
                all_cands.append(c)
        if n % 40 == 0:
            print(f"  ... {n}/{len(rows)} clips, {len(all_cands)} candidate fragments", flush=True)
    kept_main = len(meta)

    # pick: best-confidence first, at most `per_clip` from one source clip, no repeated text, quotas per length
    rng = random.Random(1)
    rng.shuffle(all_cands)
    all_cands.sort(key=lambda c: (-round(c["prob"], 1), -c["snr"]))
    quotas, taken, seen, per_src = {1: a.max_1, 2: a.max_2, 3: a.max_3}, {1: 0, 2: 0, 3: 0}, set(), {}
    picked = []
    for c in all_cands:
        key = c["text"].lower()
        if key in seen or taken[c["k"]] >= quotas[c["k"]] or per_src.get(c["src"], 0) >= a.per_clip:
            continue
        seen.add(key)
        taken[c["k"]] += 1
        per_src[c["src"]] = per_src.get(c["src"], 0) + 1
        picked.append(c)

    os.makedirs(PREVIEW, exist_ok=True)
    for f in os.listdir(PREVIEW):
        os.remove(os.path.join(PREVIEW, f))
    preview = []
    for i, c in enumerate(picked, 1):
        au = c["audio"].astype(np.float32).copy()
        au[:fade] *= np.linspace(0, 1, fade)
        au[-fade:] *= np.linspace(1, 0, fade)
        au = np.concatenate([pad, au, pad])
        name = f"slice_{i:04d}.wav"
        write_wav(os.path.join(dst, "wavs", name), au, c["sr"])
        meta.append([name, c["text"]])
        preview.append((name, c["text"], c["src"]))
    with open(os.path.join(dst, "metadata.csv"), "w", encoding="utf-8", newline="\n") as f:
        for name, text in meta:
            f.write(f"{name}|{text}\n")
    for name, text, source in random.Random(2).sample(preview, min(len(preview), a.preview)):
        shutil.copy(os.path.join(dst, "wavs", name), os.path.join(PREVIEW, name))
    with open(os.path.join(PREVIEW, "labels.csv"), "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([(n, t, s) for n, t, s in preview if os.path.exists(os.path.join(PREVIEW, n))])

    dur = sum(len(c["audio"]) / c["sr"] + 0.16 for c in picked)
    if removals:
        print(f"\n{sum(len(v) for v in removals.values())} other-speaker sentences cut out of {cut_clips} clips ({dropped_s:.0f} s removed)")
    print(f"\nWrote {dst}\n  {kept_main} denoised clips (denoise {a.denoise}) + {len(picked)} short slices "
          f"({taken[1]} one-word, {taken[2]} two-word, {taken[3]} three-word; {dur:.0f} s)")
    print(f"  {len(all_cands)} candidate fragments were found; slices are {100 * len(picked) / (len(picked) + kept_main):.0f}% of the clips.")
    print(f"  {min(len(preview), a.preview)} previews in {PREVIEW}: python training/tools/voice_slice_words.py play")


def play(a):
    from pygame import mixer
    import json
    core = json.load(open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8"))
    dev = core["settings"].get("audio_settings", {}).get("output_device")
    kw = dict(frequency=22050, size=-16, channels=1, buffer=1024)
    if dev and dev != "System Default":
        kw["devicename"] = dev
    mixer.init(**kw)
    with open(os.path.join(PREVIEW, "labels.csv"), encoding="utf-8") as f:
        rows = list(csv.reader(f))
    time.sleep(1)
    for name, text, source in rows:
        print(f"{name}  \"{text}\"   (from {source})", flush=True)
        s = mixer.Sound(os.path.join(PREVIEW, name))
        s.play()
        time.sleep(s.get_length() + 0.9)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("dataset")
    b.add_argument("--denoise", type=float, default=0.6, help="spectral-gate strength 0-1 applied to every clip (0 = leave the audio as is)")
    b.add_argument("--whisper-model", default="small.en")
    b.add_argument("--max-wer", type=float, default=0.15, help="skip slicing a clip whose transcript Whisper disagrees with")
    b.add_argument("--min-prob", type=float, default=0.7, help="every word of a fragment must have at least this Whisper confidence")
    b.add_argument("--min-gap", type=float, default=0.10, help="seconds of pause required on both sides of a fragment")
    b.add_argument("--max-1", type=int, default=30)
    b.add_argument("--max-2", type=int, default=30)
    b.add_argument("--max-3", type=int, default=25)
    b.add_argument("--per-clip", type=int, default=2, help="at most this many fragments from one source clip")
    b.add_argument("--preview", type=int, default=24)
    b.add_argument("--speaker-report", help="outputs/voice_speaker_check/flagged.json from voice_speaker_check.py")
    b.add_argument("--drop-flags", help="comma-separated flag numbers (the flag_NNN.wav previews) confirmed by ear as another speaker: cut out")
    sub.add_parser("play")
    a = p.parse_args()
    {"build": build, "play": play}[a.cmd](a)


if __name__ == "__main__":
    main()
