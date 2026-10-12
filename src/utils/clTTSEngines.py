"""Local text-to-speech engine for clTTS.py.

The engine yields float32 mono chunks while it synthesizes, so playback can start on the first chunk instead of
waiting for a whole reply. `stream()` is blocking (run it in a thread) and stops early when `stop` is set.

Piper is the only engine: ~300MB while speaking, ~190MB once unloaded, no PyTorch. A heavier cloned-voice model
(Kyutai's Pocket TTS, which sounded closer to the original Ryan voice) was tried and dropped: ~1GB of RAM that
Python cannot hand back is too much for this ecosystem. Kokoro, the film-JARVIS Piper voice, Supertonic, Kitten and
MOSS-TTS-Nano were also tried and rejected by ear or on RAM. See docs/voice_latency_plan.md.
"""
import logging
import os
import threading
import time
from typing import Iterator

import numpy as np

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))

# `en_GB-aru-medium` speaker 2 was picked by ear against the original Ryan voice, at Ryan's pace and its natural
# pitch. A pitch lift was tried (+5%, then +2%) and dropped on the owner's call; PIPER_PITCH stays as a knob.
PIPER_VOICE = "en_GB-aru-medium"
PIPER_SPEAKER = 2
PIPER_SPEED = 0.8            # length_scale; ~ the old edge-tts rate="+27%"
PIPER_PITCH = 1.0            # 1.0 = the voice's natural pitch (1.05 = +5%, same pace)


class PiperEngine:
    name = "piper"
    voice_tag = f"{PIPER_VOICE}:{PIPER_SPEAKER}:{PIPER_PITCH}"

    def __init__(self, model_dir: str = os.path.join(MODELS_DIR, "piper")):
        self.model_dir = model_dir
        self.voice = None
        self.sample_rate = 22050
        self.last_used = time.time()
        self._pitch_ratio = 1.0
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self.voice is not None

    def load(self) -> None:
        with self._lock:
            self.last_used = time.time()
            if self.voice is not None:
                return
            from piper import PiperVoice
            model = os.path.join(self.model_dir, f"{PIPER_VOICE}.onnx")
            if not os.path.exists(model):
                from pathlib import Path
                from piper.download_voices import download_voice
                logging.info(f"[TTS] Downloading local voice '{PIPER_VOICE}'...")
                download_voice(PIPER_VOICE, Path(self.model_dir))
            self.voice = PiperVoice.load(model)
            # Pitch is lifted by declaring a slightly higher sample rate for the audio (and generating it
            # proportionally slower, so the pace is unchanged). Rounded to a multiple of 150 Hz so the later
            # resample to the mixer's 24kHz uses small integer ratios (e.g. 22500 -> 24000 is 15:16; 1.0 leaves the native 22050).
            native = int(self.voice.config.sample_rate)
            self.sample_rate = int(round(native * PIPER_PITCH / 150.0)) * 150
            self._pitch_ratio = self.sample_rate / native

    def unload(self) -> bool:
        """Releases the voice (~100MB comes back). False if a sentence is being generated."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self.voice = None
        finally:
            self._lock.release()
        import gc
        gc.collect()
        return True

    def unload_if_idle(self, idle_seconds: float) -> bool:
        if self.voice is None or time.time() - self.last_used < idle_seconds:
            return False
        return self.unload()

    def shutdown(self) -> None:
        self.unload()

    def stream(self, text: str, stop: threading.Event) -> Iterator[np.ndarray]:
        from piper import SynthesisConfig
        self.load()
        with self._lock:
            self.last_used = time.time()
            config = SynthesisConfig(length_scale=PIPER_SPEED * self._pitch_ratio, speaker_id=PIPER_SPEAKER)
            for chunk in self.voice.synthesize(text, syn_config=config):
                if stop.is_set():
                    break
                yield chunk.audio_int16_array.astype(np.float32) / 32768.0
            self.last_used = time.time()


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Whole-sentence resample (Piper hands back complete sentences, so there is no chunk-boundary filter
    transient to worry about)."""
    if src_rate == dst_rate:
        return audio
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(src_rate, dst_rate)
    return resample_poly(audio, dst_rate // g, src_rate // g).astype(np.float32)
