"""Local text-to-speech: Piper (ONNX, CPU) plus an optional "Ultron" effect chain.

Used by clTTS.py (spoken replies) and clUtilities.py (reminder audio). Pure library, no MQTT.

    python src/utils/clVoice.py "Good evening." --out /tmp/test.wav
    python src/utils/clVoice.py "Good evening." --play --no-fx
"""

import argparse
import logging
import os
import threading
import wave
from typing import Any, Dict, Optional

import numpy as np
from scipy import signal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DEFAULT_SETTINGS: Dict[str, Any] = {
    "backend": "piper",
    "piper_model": "models/piper/en_US-ryan-medium.onnx",
    "length_scale": 0.9,
    "denoise": 0.6,                  # 0 = off, 1 = strongest: spectral gate on the dry Piper audio (the trained voice inherits its clips' noise)
    "fx": {
        "enabled": True,
        "vocoder_mix": 0.35,         # the actor's own voice stays in front, with a light robotic layer
        "vocoder_hz": 72.0,
        "vocoder_noise_hi": 0.8,
        "eq_db": [0.0] * 22,
        "pitch_semitones": -1.0,
        "ring_mod_hz": 55.0,
        "ring_mod_mix": 0.0,
        "comb_delay_ms": 7.0,
        "comb_feedback": 0.45,
        "comb_mix": 0.0,
        "drive": 1.0,
        "reverb_seconds": 0.45,
        "reverb_mix": 0.1,
    },
}


def merge_settings(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """core.json -> settings.tts_settings over DEFAULT_SETTINGS (fx merged key by key)."""
    out = {**DEFAULT_SETTINGS, "fx": dict(DEFAULT_SETTINGS["fx"])}
    for k, v in (user or {}).items():
        if k == "fx" and isinstance(v, dict):
            out["fx"].update(v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- effects

def _pitch_down(x: np.ndarray, semitones: float) -> np.ndarray:
    """Lower pitch AND formants by resampling (the clip gets longer; synthesize() compensates
    with Piper's length_scale so the final speaking rate is unchanged)."""
    if abs(semitones) < 0.05:
        return x
    ratio = 2.0 ** (semitones / 12.0)          # < 1 for a lower pitch
    return signal.resample(x, int(round(len(x) / ratio))).astype(np.float32)


EQ_CENTRES = np.geomspace(60, 8000, 22)     # centres of the match-EQ bands (about 1/3 octave)
VOCODER_BANDS = 28


def vocode(x: np.ndarray, sr: int, f0: float, noise_hi: float = 0.8) -> np.ndarray:
    """Channel vocoder with a fixed-pitch carrier: the speech keeps its words and formants but the
    pitch is locked to f0, which is what makes it sound robotic. The carrier is a pulse train at f0
    below ~450 Hz and fades to noise above ~1.8 kHz (a pure pulse train leaves harmonic stripes up
    there that the real thing doesn't have)."""
    n = len(x)
    x = x.astype(np.float32)
    phase = np.cumsum(np.full(n, f0 / sr))
    pulses = np.zeros(n, np.float32)
    pulses[np.where(np.diff(np.floor(phase)) > 0)[0] + 1] = 1.0
    noise = np.random.default_rng(3).standard_normal(n).astype(np.float32)   # fixed seed: same every phrase
    pulses /= np.sqrt(np.mean(pulses ** 2)) + 1e-9
    noise /= np.sqrt(np.mean(noise ** 2)) + 1e-9
    edges = np.geomspace(50.0, min(9000.0, sr / 2 - 100), VOCODER_BANDS + 1)
    env_lp = signal.butter(2, 60.0 / (sr / 2), "low", output="sos")
    out = np.zeros(n, np.float32)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sos = signal.butter(3, [lo / (sr / 2), hi / (sr / 2)], "band", output="sos")
        env = signal.sosfilt(env_lp, np.abs(signal.sosfilt(sos, x))) * (np.pi / 2)
        w = 0.03 + (noise_hi - 0.03) * float(np.clip(np.log(lo / 450.0) / np.log(1800.0 / 450.0), 0, 1))
        out += signal.sosfilt(sos, (1 - w) * pulses + w * noise).astype(np.float32) * env.astype(np.float32)
    return out * (np.sqrt(np.mean(x ** 2)) + 1e-9) / (np.sqrt(np.mean(out ** 2)) + 1e-9)


def apply_eq(x: np.ndarray, sr: int, gains_db) -> np.ndarray:
    """Static EQ with one gain (dB) per EQ_CENTRES band, smooth in between (linear-phase FIR)."""
    g = np.asarray(gains_db, dtype=float)
    if len(g) != len(EQ_CENTRES) or not np.any(g):
        return x
    freqs = np.concatenate([[0.0], EQ_CENTRES, [sr / 2]])
    gains = 10 ** (np.concatenate([[g[0]], g, [g[-1]]]) / 20)
    h = signal.firwin2(2049, freqs / (sr / 2), gains)
    return signal.fftconvolve(x, h)[1024:1024 + len(x)].astype(np.float32)


def _comb(x: np.ndarray, sr: int, delay_ms: float, feedback: float) -> np.ndarray:
    """Feedback comb filter: the hollow, metallic resonance."""
    d = max(1, int(sr * delay_ms / 1000.0))
    a = np.zeros(d + 1)
    a[0], a[d] = 1.0, -feedback
    return signal.lfilter([1.0], a, x).astype(np.float32)


def _reverb_ir(sr: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(7)               # fixed seed -> identical room every phrase
    n = int(sr * seconds)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 6.0, n))
    b, a = signal.butter(2, 3500 / (sr / 2), "low")   # dark room
    ir = signal.lfilter(b, a, ir)
    ir[0] = 0.0
    return (ir / np.abs(ir).max()).astype(np.float32)


def apply_fx(x: np.ndarray, sr: int, fx: Dict[str, Any]) -> np.ndarray:
    """Ultron-style processing for mono float audio in [-1, 1]. Returns audio at the same
    sample rate, peak-limited and loudness-matched to the dry input."""
    if not fx.get("enabled", True) or len(x) == 0:
        return x
    dry_rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    y = x.astype(np.float32)

    voc_mix = float(fx.get("vocoder_mix", 0.0))
    if voc_mix > 0:
        y = (1 - voc_mix) * y + voc_mix * vocode(y, sr, float(fx["vocoder_hz"]), float(fx.get("vocoder_noise_hi", 0.8)))

    ring_mix = float(fx.get("ring_mod_mix", 0.0))
    if ring_mix > 0:
        t = np.arange(len(y)) / sr
        y = (1 - ring_mix) * y + ring_mix * y * np.sin(2 * np.pi * float(fx["ring_mod_hz"]) * t).astype(np.float32)

    comb_mix = float(fx.get("comb_mix", 0.0))
    if comb_mix > 0:
        wet = _comb(y, sr, float(fx["comb_delay_ms"]), float(fx["comb_feedback"]))
        wet *= np.sqrt(np.mean(y ** 2) + 1e-9) / (np.sqrt(np.mean(wet ** 2)) + 1e-9)
        y = (1 - comb_mix) * y + comb_mix * wet

    drive = float(fx.get("drive", 1.0))
    if drive > 1.0:
        y = np.tanh(y * drive) / np.tanh(drive)

    rev_mix = float(fx.get("reverb_mix", 0.0))
    if rev_mix > 0:
        tail = signal.fftconvolve(y, _reverb_ir(sr, float(fx["reverb_seconds"])))
        tail *= np.sqrt(np.mean(y ** 2) + 1e-9) / (np.sqrt(np.mean(tail ** 2)) + 1e-9)
        y = np.concatenate([y, np.zeros(len(tail) - len(y), np.float32)])
        y = (1 - rev_mix) * y + rev_mix * tail

    y = apply_eq(y, sr, fx.get("eq_db", []))
    y *= dry_rms / (float(np.sqrt(np.mean(y ** 2))) + 1e-9)
    peak = float(np.abs(y).max())
    if peak > 0.95:
        y *= 0.95 / peak
    return y.astype(np.float32)


def denoise(x: np.ndarray, sr: int, strength: float, keep_below_hz: float = 0.0) -> np.ndarray:
    """Spectral gate for the dry Piper audio. The noise profile is the quietest frames of this very phrase, so it
    needs no per-voice tuning; if the phrase has no real pauses the strength is scaled down (a profile taken from
    speech would eat the voice). Gain is smoothed over time and frequency to avoid watery 'musical' artefacts.
    `keep_below_hz` leaves the low end untouched, so a deep voice keeps its weight (steady rumble there stays)."""
    if strength <= 0 or len(x) < sr // 4:
        return x
    from scipy.ndimage import uniform_filter
    n = 1024
    freqs, _, Z = signal.stft(x, sr, nperseg=n, noverlap=n * 3 // 4)
    mag = np.abs(Z)
    energy = (mag ** 2).sum(axis=0)
    quiet = np.argsort(energy)[: max(3, mag.shape[1] // 10)]
    noise = mag[:, quiet].mean(axis=1, keepdims=True)
    gap_db = 10 * np.log10((np.median(energy) + 1e-12) / (energy[quiet].mean() + 1e-12))
    strength = min(1.0, strength) * min(1.0, gap_db / 15.0)       # 15 dB between pauses and speech = fully trusted
    if strength <= 0:
        return x
    alpha = 1.0 + 1.5 * strength                                   # over-subtraction
    floor = 10 ** (-(8 + 14 * strength) / 20)                      # never gate fully to silence
    gain = np.clip(1.0 - alpha * noise / (mag + 1e-9), floor, 1.0)
    gain = uniform_filter(gain, size=(3, 5), mode="nearest")
    if keep_below_hz > 0:
        gain[freqs < keep_below_hz, :] = 1.0
    _, y = signal.istft(Z * gain, sr, nperseg=n, noverlap=n * 3 // 4)
    y = np.pad(y[: len(x)], (0, max(0, len(x) - len(y)))).astype(np.float32)
    y *= float(np.sqrt(np.mean(x ** 2))) / (float(np.sqrt(np.mean(y ** 2))) + 1e-9)
    return np.clip(y, -1.0, 1.0)


# --------------------------------------------------------------------------- synthesis

class VoiceSynth:
    """Loads one Piper voice lazily and renders text to a 16-bit mono WAV. Thread-safe."""

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.cfg = merge_settings(settings)
        self._voice = None
        self._lock = threading.Lock()

    @property
    def model_path(self) -> str:
        p = self.cfg["piper_model"]
        return p if os.path.isabs(p) else os.path.join(ROOT, p)

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(
                    f"Piper voice not found: {self.model_path} "
                    f"(python -m piper.download_voices --download-dir models/piper <name>)"
                )
            self._voice = PiperVoice.load(self.model_path)
            logging.info(f"[VOICE] Loaded Piper voice {os.path.basename(self.model_path)}")
        return self._voice

    def render(self, text: str) -> "tuple[np.ndarray, int]":
        from piper import SynthesisConfig
        fx = self.cfg["fx"]
        length_scale = float(self.cfg["length_scale"])
        if fx.get("enabled", True):
            # Pitching down by resampling slows the speech by the same ratio; pre-compensate.
            length_scale *= 2.0 ** (float(fx.get("pitch_semitones", 0.0)) / 12.0)
        with self._lock:
            voice = self._load()
            syn = SynthesisConfig(length_scale=length_scale, noise_scale=self.cfg.get("noise_scale"),
                                  noise_w_scale=self.cfg.get("noise_w_scale"))   # None = the voice's own default
            chunks = list(voice.synthesize(text, syn))
        if not chunks:
            raise RuntimeError("Piper returned no audio")
        sr = chunks[0].sample_rate
        audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        audio = denoise(audio, sr, float(self.cfg.get("denoise", 0.0)))
        if fx.get("enabled", True):
            audio = apply_fx(_pitch_down(audio, float(fx.get("pitch_semitones", 0.0))), sr, fx)
        return audio, sr

    def synthesize_to_file(self, text: str, out_path: str) -> None:
        audio, sr = self.render(text)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        tmp = out_path + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        os.replace(tmp, out_path)


def _main() -> None:
    import json
    import subprocess
    import tempfile

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("text")
    p.add_argument("--out", help="write a WAV here (default: temp file, only with --play)")
    p.add_argument("--play", action="store_true", help="play it (paplay/aplay/ffplay)")
    p.add_argument("--no-fx", action="store_true", help="dry Piper voice, no effects")
    p.add_argument("--model", help="override piper_model")
    p.add_argument("--denoise", type=float, help="noise gate strength 0-1 (0 = off), overrides settings")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                   help="override an fx value, e.g. --set pitch_semitones=-4 (repeatable)")
    a = p.parse_args()

    settings: Dict[str, Any] = {}
    try:
        with open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8") as f:
            settings = json.load(f).get("settings", {}).get("tts_settings", {})
    except Exception:
        pass
    settings = merge_settings(settings)
    if a.model:
        settings["piper_model"] = a.model
    if a.denoise is not None:
        settings["denoise"] = a.denoise
    if a.no_fx:
        settings["fx"]["enabled"] = False
    for kv in a.set:
        k, v = kv.split("=", 1)
        settings["fx"][k] = float(v)

    out = a.out or os.path.join(tempfile.gettempdir(), "clvoice_preview.wav")
    VoiceSynth(settings).synthesize_to_file(a.text, out)
    print(out)
    if a.play:
        for cmd in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
            try:
                subprocess.run(cmd + [out], check=True)
                print(f"played with {cmd[0]}")
                break
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                print(f"{cmd[0]} failed: {e}")
        else:
            print(f"no player worked; open the file instead: {out}")


if __name__ == "__main__":
    _main()
