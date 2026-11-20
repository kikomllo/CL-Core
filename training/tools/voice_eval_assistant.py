"""Scores Piper voices on what JARVIS actually says (the reply-model phrase corpus plus fixed greetings), not on film
dialogue: a voice trained on film lines can score well on those and still drag or mangle "Good evening". For every
voice, each phrase is rendered a few times through the live clVoice settings and re-heard by Whisper on the GPU:
  wer      word error of what Whisper hears vs the text (lower is better; noisy: compare voices, not absolute numbers)
  exact    share of renders heard exactly right
  s/char   speaking time per character on the speech itself (a voice that drags has a higher value than its peers)

    python training/tools/voice_eval_assistant.py models/piper/actor_voice.onnx outputs/voice_candidates/ultron_e275.onnx
    python training/tools/voice_eval_assistant.py outputs/voice_candidates/*.onnx --phrases 30 --renders 2

Writes outputs/voice_eval_assistant.csv. Run with no training job and JARVIS idle (it uses CPU for Piper, GPU for Whisper)."""
import argparse
import csv
import json
import os
import random
import re
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "src", "utils")]

FIXED = ["Good evening.", "Good morning, sir.", "All systems are online and running normally.", "Done.",
         "Certainly, sir.", "I am afraid I cannot do that."]


def phrases(n, seed=1):
    pool = set()
    with open(os.path.join(ROOT, "training", "data", "reply_lora_dataset.jsonl"), encoding="utf-8") as f:
        for line in f:
            text = json.loads(line)["messages"][-1]["content"].strip()
            if 2 <= len(text.split()) <= 12 and "{" not in text and len(text) > 8:
                pool.add(text)
    pool = sorted(pool)
    random.Random(seed).shuffle(pool)
    return FIXED + pool[: max(0, n - len(FIXED))]


def norm(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", " ")).split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / len(r)


def speech_seconds(a, sr):
    act = np.where(np.abs(a) > 0.02)[0]
    return (act[-1] - act[0]) / sr if len(act) else 0.0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("voices", nargs="+")
    p.add_argument("--phrases", type=int, default=40)
    p.add_argument("--renders", type=int, default=2)
    p.add_argument("--whisper-model", default="small.en")
    a = p.parse_args()

    if sys.platform == "win32":          # CTranslate2 needs the CUDA DLLs the venv's nvidia wheels ship (same fix as clWhisper.py)
        for base_dir in sys.path:
            for sub in ("cublas", "cudnn"):
                d = os.path.join(base_dir, "nvidia", sub, "bin")
                if os.path.isdir(d):
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    from scipy.signal import resample_poly
    from utils.clVoice import VoiceSynth

    try:
        whisper = WhisperModel(a.whisper_model, device="cuda", compute_type="float16")
    except Exception:
        whisper = WhisperModel(a.whisper_model, device="cpu", compute_type="int8")
    with open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8") as f:
        base = json.load(f).get("settings", {}).get("tts_settings", {})
    texts = phrases(a.phrases)
    print(f"{len(texts)} phrases x {a.renders} renders per voice, settings from core.json tts_settings", flush=True)

    results = []
    for path in a.voices:
        name = os.path.splitext(os.path.basename(path))[0]
        synth = VoiceSynth({**base, "piper_model": os.path.abspath(path)})
        errs, exact, rate, worst = [], 0, [], []
        for text in texts:
            for _ in range(a.renders):
                audio, sr = synth.render(text)
                heard, _ = whisper.transcribe(resample_poly(audio, 16000, sr).astype(np.float32), language="en", beam_size=5)
                said = " ".join(s.text for s in heard)
                e = wer(text, said)
                errs.append(e)
                exact += e == 0
                rate.append(speech_seconds(audio, sr) / max(1, len(text)))
                if e >= 0.5:
                    worst.append((text, said.strip()))
        row = dict(voice=name, wer=round(float(np.mean(errs)), 3), exact=round(exact / len(errs), 2),
                   s_per_char=round(float(np.mean(rate)), 4), bad_renders=len(worst))
        results.append(row)
        print(f"{name:24s} wer {row['wer']:.3f}  exact {row['exact']:.2f}  s/char {row['s_per_char']:.4f}  bad {row['bad_renders']}", flush=True)
        del synth

    os.makedirs(os.path.join(ROOT, "outputs"), exist_ok=True)
    with open(os.path.join(ROOT, "outputs", "voice_eval_assistant.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    print("\nRanked by wer (lower is better):")
    for r in sorted(results, key=lambda r: r["wer"]):
        print(f"  {r['voice']:24s} wer {r['wer']:.3f}  exact {r['exact']:.2f}  s/char {r['s_per_char']:.4f}")


if __name__ == "__main__":
    main()
