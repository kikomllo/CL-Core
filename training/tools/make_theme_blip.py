"""Generates the "I heard you" chirp of a theme: python training/tools/make_theme_blip.py ultron
Writes assets/themes/<theme>/blip.wav (clTTS prefers it over assets/blip.mp3 when settings.theme matches).

ultron: a colder, lower, slightly metallic descending tone (the JARVIS one is a ~0.3 s tone around 420 Hz)."""
import os
import sys
import wave

import numpy as np

RATE = 48000
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def ultron() -> np.ndarray:
    n = int(RATE * 0.34)
    t = np.arange(n) / RATE
    glide = 560.0 * np.exp(-3.2 * t / t[-1]) + 150.0              # 710 Hz falling to ~175 Hz
    phase = 2 * np.pi * np.cumsum(glide) / RATE
    tone = np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.2 * np.sin(0.5 * phase)       # a body plus a hollow sub
    metal = tone * (0.65 + 0.35 * np.sin(2 * np.pi * 62.0 * t))                       # ring modulation
    env = np.minimum(1.0, t / 0.012) * np.exp(-5.5 * t / t[-1])                       # hard attack, long cold decay
    x = metal * env
    echo = np.zeros(n + int(RATE * 0.12))
    echo[:n] += x
    echo[int(RATE * 0.06):int(RATE * 0.06) + n] += 0.28 * x                            # one short reflection
    return (0.9 * echo / np.abs(echo).max()).astype(np.float32)


def main() -> None:
    theme = sys.argv[1] if len(sys.argv) > 1 else "ultron"
    if theme != "ultron":
        raise SystemExit(f"no generator for theme '{theme}'")
    x = ultron()
    out = os.path.join(ROOT, "assets", "themes", theme, "blip.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    print(out, f"{len(x) / RATE:.2f}s")


if __name__ == "__main__":
    main()
