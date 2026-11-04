"""Local text-to-speech engine for clTTS.py.

The engine yields float32 mono chunks while it synthesizes, so playback can start on the first chunk instead of
waiting for a whole reply. `stream()` is blocking (run it in a thread) and stops early when `stop` is set.

Kokoro (settings.tts_engine "kokoro") is the owner's main voice: livelier and cleaner than Piper, ~400MB while
loaded and about 0.45x real time on a desktop CPU, so it is generated sentence by sentence. Piper (the default
engine, and Kokoro's fallback) is ~300MB while speaking, ~190MB once unloaded. Both are ONNX only, no PyTorch. A heavier
cloned-voice model (Kyutai's Pocket TTS, which sounded closer to the original Ryan voice) was tried and dropped: ~1GB of
RAM that Python cannot hand back is too much for this ecosystem. The film-JARVIS Piper voice, Supertonic, Kitten and
MOSS-TTS-Nano were also tried and rejected. See docs/voice_latency_plan.md.
"""
import logging
import os
import re
import threading
import time
from typing import Iterator, List, Tuple

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


# Kokoro-82M (int8 ONNX, ~120MB on disk). The voice is an equal blend of the British male styles Fable and Lewis,
# picked by ear against the original Ryan voice (Daniel and George measured flat: ~1.7-2 semitones of pitch movement
# against Ryan's 4.2). Speed 1.3 and "!" for sentence-final "." keep it brisk and bright; pitch is left natural.
KOKORO_VOICES = ("bm_fable", "bm_lewis")
KOKORO_SPEED = 1.3
KOKORO_LANG = "en-gb"
KOKORO_RATE = 24000
KOKORO_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
KOKORO_MAX_PIECE = 160       # characters; a longer sentence is split at its commas
KOKORO_MIN_PIECE = 28        # a shorter sentence is spoken together with the next one ("Right away!" alone sounds clipped)
KOKORO_FIRST_SPLIT = 90      # a first sentence longer than this is cut at its first comma, so speech starts sooner
KOKORO_GAIN_DB = 7.0         # Kokoro speaks ~10 dB quieter than Piper (-23 vs -13 dBFS RMS); +7 dB lands at ~-16, the real Ryan was -20
KOKORO_CEILING = 0.95        # the limiter keeps the boosted speech under this, so the gain never clips
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "no", "approx"}


def boost(audio: np.ndarray, gain_db: float = KOKORO_GAIN_DB, ceiling: float = KOKORO_CEILING) -> np.ndarray:
    """Louder speech without clipping: a fixed gain, then a smooth look-ahead peak limiter (the gain dips ahead of a
    peak and recovers after it, over ~20 ms, so only brief peaks such as plosives are trimmed)."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    audio = np.asarray(audio, dtype=np.float32) * np.float32(10 ** (gain_db / 20))
    if len(audio) == 0:
        return audio
    window = int(KOKORO_RATE * 0.02)
    envelope = maximum_filter1d(np.abs(audio), size=2 * window + 1)
    reduction = uniform_filter1d(np.minimum(1.0, ceiling / np.maximum(envelope, 1e-9)), size=window)
    return (audio * reduction).astype(np.float32)


def brighten(text: str) -> str:
    """Sentence-final full stops become exclamation marks, which Kokoro reads in a brighter tone.
    Ellipses, decimals ("9.5") and abbreviations ("Dr. Who") are left alone."""
    def swap(m: "re.Match") -> str:
        before = text[:m.start()].split()
        if before and before[-1].lower().strip("(\"'") in _ABBREVIATIONS:
            return "."
        return "!"
    return re.sub(r"(?<!\.)\.(?![.\w])(?=\s|$)", swap, text)


def split_pieces(text: str) -> List[Tuple[str, float]]:
    """Splits a reply into (text, pause-after-seconds) pieces that are generated one after the other, so the first
    audio is ready after one sentence instead of after the whole reply."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    pieces: List[str] = []
    if sentences and len(sentences[0]) > KOKORO_FIRST_SPLIT:        # the first audio waits for the first piece only
        first = sentences[0]
        cut = first.find(", ", KOKORO_MIN_PIECE)
        if cut != -1 and len(first) - cut - 2 >= KOKORO_MIN_PIECE:
            pieces.append(first[:cut + 1])
            sentences[0] = first[cut + 2:]
    for s in sentences:
        while len(s) > KOKORO_MAX_PIECE:
            cut = s.rfind(", ", 0, KOKORO_MAX_PIECE)
            if cut < KOKORO_MIN_PIECE:
                break
            pieces.append(s[:cut + 1]); s = s[cut + 2:]
        pieces.append(s)
    merged: List[str] = []
    for p in pieces:                                   # fold a short piece into the next one
        if merged and len(merged[-1]) < KOKORO_MIN_PIECE:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return [(p, 0.18 if p[-1:] in ".!?" else 0.08) for p in merged]


class KokoroEngine:
    name = "kokoro"
    _warmed = False                  # the first synthesis in a process is ~1.5s slower than the ones after it

    def __init__(self, model_dir: str = os.path.join(MODELS_DIR, "kokoro"), bright: bool = True):
        self.model_dir = model_dir
        self.bright = bright
        self.kokoro = None
        self._style = None
        self.sample_rate = KOKORO_RATE
        self.last_used = time.time()
        self._lock = threading.Lock()

    @property
    def voice_tag(self) -> str:
        return f"{'+'.join(KOKORO_VOICES)}:{KOKORO_SPEED}:{'bright' if self.bright else 'plain'}:{KOKORO_GAIN_DB:g}dB"

    def ready(self) -> bool:
        return self.kokoro is not None

    def _ensure_files(self) -> Tuple[str, str]:
        import urllib.request
        os.makedirs(self.model_dir, exist_ok=True)
        paths = []
        for name in (KOKORO_MODEL_FILE, KOKORO_VOICES_FILE):
            path = os.path.join(self.model_dir, name)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                logging.info(f"[TTS] Downloading Kokoro file '{name}'...")
                urllib.request.urlretrieve(KOKORO_BASE_URL + name, path + ".part")
                os.replace(path + ".part", path)
            paths.append(path)
        return paths[0], paths[1]

    def load(self) -> None:
        with self._lock:
            self.last_used = time.time()
            if self.kokoro is not None:
                return
            from kokoro_onnx import Kokoro
            model, voices = self._ensure_files()
            kokoro = Kokoro(model, voices)
            self._style = np.mean([kokoro.get_voice_style(v) for v in KOKORO_VOICES], axis=0)
            self.kokoro = kokoro

    def warm_up(self) -> None:
        """One throwaway sentence, once per process, so the first real reply is not the slow one."""
        if KokoroEngine._warmed:
            return
        self.load()
        with self._lock:
            if self.kokoro is None:
                return
            try:
                self.kokoro.create("Ready.", voice=self._style, speed=KOKORO_SPEED, lang=KOKORO_LANG)
            except Exception as e:
                logging.warning(f"[TTS] Kokoro warm-up failed: {e}")
            KokoroEngine._warmed = True

    def unload(self) -> bool:
        """Releases the model (~300MB comes back). False if a sentence is being generated."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self.kokoro = None
            self._style = None
        finally:
            self._lock.release()
        import gc
        gc.collect()
        return True

    def unload_if_idle(self, idle_seconds: float) -> bool:
        if self.kokoro is None or time.time() - self.last_used < idle_seconds:
            return False
        return self.unload()

    def shutdown(self) -> None:
        self.unload()

    def stream(self, text: str, stop: threading.Event) -> Iterator[np.ndarray]:
        self.load()
        if self.bright:
            text = brighten(text)
        pieces = split_pieces(text)
        with self._lock:
            self.last_used = time.time()
            for i, (piece, pause) in enumerate(pieces):
                if stop.is_set():
                    break
                audio, rate = self.kokoro.create(piece, voice=self._style, speed=KOKORO_SPEED, lang=KOKORO_LANG)
                audio = np.asarray(audio, dtype=np.float32)
                if rate != KOKORO_RATE:
                    audio = resample(audio, int(rate), KOKORO_RATE)
                audio = boost(audio)
                if i < len(pieces) - 1:
                    audio = np.concatenate([audio, np.zeros(int(KOKORO_RATE * pause), dtype=np.float32)])
                yield audio
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
