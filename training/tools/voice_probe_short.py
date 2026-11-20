"""Short-line probe: renders one- to three-word lines (the fault of the first Ultron voices: "Good evening." was never heard right
and rendered ~50% too long) several times per voice through the live clVoice settings, re-heard by Whisper on the GPU.
Per voice and phrase: renders heard exactly right (hits/N) and the mean speech time in seconds.

    python training/tools/voice_probe_short.py outputs/voice_candidates/ultron_e275.onnx outputs/voice_v2_candidates/*.onnx --renders 8
Run with no training job. Writes outputs/voice_probe_short.csv."""
import argparse
import csv
import json
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "src", "utils"), os.path.dirname(os.path.abspath(__file__))]
from voice_eval_assistant import norm, speech_seconds  # noqa: E402

PHRASES = ["Good evening.", "Hello.", "Yes.", "No.", "Done.", "Certainly.", "Good morning.", "Understood.", "Right away.",
           "At once, sir.", "As you wish.", "Lights on.", "Welcome home.", "Goodnight."]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("voices", nargs="+")
    p.add_argument("--renders", type=int, default=8)
    p.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE", help="override tts_settings, e.g. length_scale=1.2 noise_scale=0.4")
    a = p.parse_args()
    if sys.platform == "win32":
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
        whisper = WhisperModel("small.en", device="cuda", compute_type="float16")
    except Exception:
        whisper = WhisperModel("small.en", device="cpu", compute_type="int8")
    with open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8") as f:
        base = json.load(f).get("settings", {}).get("tts_settings", {})
    base = {**base, **{k: float(v) for k, v in (s.split("=", 1) for s in a.set)}}
    if a.set:
        print("overrides:", a.set, flush=True)

    rows, totals = [], {}
    for path in a.voices:
        name = os.path.splitext(os.path.basename(path))[0]
        synth = VoiceSynth({**base, "piper_model": os.path.abspath(path)})
        hits_all, line = 0, []
        for text in PHRASES:
            hits, secs = 0, []
            for _ in range(a.renders):
                audio, sr = synth.render(text)
                heard, _ = whisper.transcribe(resample_poly(audio, 16000, sr).astype(np.float32), language="en", beam_size=5)
                said = " ".join(s.text for s in heard)
                hits += norm(said) == norm(text)
                secs.append(speech_seconds(audio, sr))
            hits_all += hits
            line.append(f"{text.rstrip('.')}={hits}/{a.renders} {np.mean(secs):.2f}s")
            rows.append(dict(voice=name, phrase=text, hits=hits, renders=a.renders, seconds=round(float(np.mean(secs)), 3)))
        totals[name] = hits_all / (len(PHRASES) * a.renders)
        print(f"{name:22s} exact {totals[name]:.2f} | " + "  ".join(line), flush=True)
        del synth
    with open(os.path.join(ROOT, "outputs", "voice_probe_short.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nRanked by exact share:")
    for n, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {n:22s} {v:.2f}")


if __name__ == "__main__":
    main()
