"""Echo cancellation so JARVIS can be interrupted while it speaks.

The TTS service publishes what it plays (`jarvis/sys/tts_ref`); `ReferenceBuffer` keeps that by wall-clock
time, and `EchoCanceller` subtracts it from the microphone so the wake word can be heard over JARVIS's own
voice. Pure numpy + pyaec (a ~100KB Speex-based canceller with wheels for Windows and Linux).
"""
import threading
import time
from typing import Optional

import numpy as np

RATE = 16000
FRAME = 160  # 10ms, the canceller's frame size


class ReferenceBuffer:
    """Ring of the audio JARVIS has played/queued, indexed by absolute sample time (16kHz, wall clock).
    Slots remember which absolute sample they hold, so a read never returns stale audio from an
    earlier lap of the ring."""

    def __init__(self, seconds: int = 30):
        self.size = seconds * RATE
        self._pcm = np.zeros(self.size, dtype=np.int16)
        self._stamp = np.full(self.size, -1, dtype=np.int64)
        self._lock = threading.Lock()
        self._newest = 0  # absolute index just past the newest sample written

    def add(self, t0: float, offset: int, pcm: np.ndarray) -> None:
        """`pcm` (int16, 16kHz) starts `offset` samples after wall-clock time `t0`."""
        start = int(round(t0 * RATE)) + int(offset)
        idx = np.arange(start, start + len(pcm), dtype=np.int64)
        slots = idx % self.size
        with self._lock:
            self._pcm[slots] = pcm
            self._stamp[slots] = idx
            self._newest = max(self._newest, start + len(pcm))

    def get(self, t_start: float, n: int) -> np.ndarray:
        """The `n` reference samples that were playing from wall-clock time `t_start` (zeros where none)."""
        start = int(round(t_start * RATE))
        idx = np.arange(start, start + n, dtype=np.int64)
        slots = idx % self.size
        with self._lock:
            return np.where(self._stamp[slots] == idx, self._pcm[slots], 0).astype(np.int16)

    def active(self, now: Optional[float] = None, tail: float = 0.6) -> bool:
        """True while something is playing or finished playing less than `tail` seconds ago (echo rings on)."""
        now = time.time() if now is None else now
        with self._lock:
            return self._newest > 0 and self._newest / RATE + tail > now


class EchoCanceller:
    """Speex echo cancellation (~0.45ms of CPU per 10ms of audio). One pass: a second pass removes a bit
    more echo but starts distorting the user's voice, which measured worse for the wake word at low echo.
    Without pyaec it passes the microphone through unchanged, so barge-in then relies on the wake word alone."""

    def __init__(self, taps: int = 4096, passes: int = 1):
        self._stages = []
        self.available = False
        try:
            from pyaec import Aec
            self._stages = [Aec(FRAME, taps, RATE, True) for _ in range(passes)]
            self.available = True
        except Exception:
            pass

    def process(self, mic: np.ndarray, far: np.ndarray) -> np.ndarray:
        """`mic` and `far` are int16 arrays of equal length at 16kHz; returns the cleaned microphone."""
        if not self.available:
            return mic
        out = mic.copy()
        for i in range(0, len(out) - FRAME + 1, FRAME):
            frame = out[i:i + FRAME]
            ref = np.ascontiguousarray(far[i:i + FRAME])
            for stage in self._stages:
                frame = np.asarray(stage.cancel_echo(np.ascontiguousarray(frame), ref), dtype=np.int16)[:FRAME]
            out[i:i + FRAME] = frame
        return out


class StreamResampler24to16:
    """24kHz -> 16kHz for a stream of chunks, identical to resampling the whole signal at once.

    resample_poly zero-pads past the end of its input, so the last few outputs of any chunk are wrong;
    those are held back until the next chunk supplies the real audio (a ~2ms delay), and the input is
    trimmed on multiples of 3 so the filter phase never shifts."""

    LOOK = 32      # output samples held back (the filter's lookahead, with margin)
    KEEP = 720     # input samples of context carried to the next call

    def __init__(self):
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0   # absolute input index of _buf[0]; always a multiple of 3
        self._emitted = 0     # absolute output index already handed out

    def process(self, chunk: np.ndarray) -> np.ndarray:
        from scipy.signal import resample_poly
        self._buf = np.concatenate([self._buf, chunk.astype(np.float32)])
        out = resample_poly(self._buf, 2, 3)
        out_start = self._buf_start * 2 // 3
        safe_end = out_start + len(out) - self.LOOK
        lo = max(self._emitted, out_start)
        result = out[lo - out_start:safe_end - out_start] if safe_end > lo else np.zeros(0, dtype=np.float32)
        self._emitted = max(self._emitted, safe_end)

        end_abs = self._buf_start + len(self._buf)
        new_start = ((end_abs - self.KEEP) // 3) * 3
        if new_start > self._buf_start:
            self._buf = self._buf[new_start - self._buf_start:]
            self._buf_start = new_start
        return result.astype(np.float32)
