"""Benchmark the local TTS engines without playing anything: first-chunk latency, real-time factor and peak RAM.

    python training/tools/bench_tts.py                      # piper, ultron light, ultron full
    python training/tools/bench_tts.py --engines piper ultron:light --wav-dir outputs/bench_tts

Run it on each machine (and with the ecosystem stopped for clean numbers) and paste the table into docs/ultron_plan.md.
`--wav-dir` also saves every render so the voices can be compared by ear (judge with no training job running).
"""
import argparse
import os
import sys
import threading
import time
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

LINES = [
    "Lights off.",
    "Playing the track you asked for.",
    "I have changed the colour to red, and the brightness to forty percent.",
    "You asked for the impossible. Naturally, I have already done it. Is there anything else?",
    "Humanity is a fragile thing, and yet you keep asking me to turn the lamp on and off.",
]


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2 ** 20
    except Exception:
        return 0.0


def build(spec: str):
    from utils.clTTSEngines import PiperEngine, KokoroEngine, UltronEngine
    from utils.clConfigLoader import ConfigLoader
    name, _, level = spec.partition(":")
    if name == "piper":
        return PiperEngine()
    if name == "kokoro":
        return KokoroEngine()
    settings = dict(ConfigLoader().load_json("core.json").get("settings", {}).get("tts_settings") or {})
    settings["fx_level"] = level or settings.get("fx_level", "full")
    return UltronEngine(settings)


def save(path: str, audio: np.ndarray, rate: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", nargs="+", default=["piper", "ultron:light", "ultron:full"])
    ap.add_argument("--wav-dir", help="save each render here")
    a = ap.parse_args()

    print(f"{'engine':14} {'line':>4} {'first chunk':>12} {'audio s':>8} {'total s':>8} {'RTF':>6} {'RSS MB':>7}")
    for spec in a.engines:
        engine = build(spec)
        t = time.time()
        engine.load()
        print(f"{spec:14} load {time.time() - t:5.1f}s" + (" (stock fallback voice)" if getattr(engine, "using_fallback", False) else ""))
        for n, text in enumerate(LINES):
            stop, chunks, t0, first = threading.Event(), [], time.time(), None
            for chunk in engine.stream(text, stop):
                if first is None:
                    first = time.time() - t0
                chunks.append(chunk)
            total = time.time() - t0
            audio = np.concatenate(chunks)
            secs = len(audio) / engine.sample_rate
            print(f"{spec:14} {n:4d} {first:11.2f}s {secs:8.2f} {total:8.2f} {total / secs:6.2f} {_rss_mb():7.0f}")
            if a.wav_dir:
                save(os.path.join(a.wav_dir, f"{spec.replace(':', '_')}_{n}.wav"), audio, engine.sample_rate)
        engine.unload()


if __name__ == "__main__":
    main()
