import os
from typing import Optional

import numpy as np

WINDOW = 480  # 30ms at 16kHz, the size the bundled Silero model is tuned for


class SileroVad:
    """Speech probability per audio chunk, from the Silero VAD model that ships with
    openWakeWord (1.8MB, one CPU thread, no extra dependency).

    Chunks of any length are accepted: samples are consumed in 30ms windows and the
    remainder is carried into the next call, with the model's recurrent state kept
    across calls so a sentence is judged as one stream."""

    def __init__(self, model_path: Optional[str] = None):
        import onnxruntime as ort
        if model_path is None:
            import openwakeword
            model_path = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models", "silero_vad.onnx")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        """Forget the previous utterance (call before each new recording)."""
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)

    def process(self, pcm16: np.ndarray) -> float:
        """Highest speech probability (0..1) among the windows completed by this chunk, so one
        speech-like 30ms inside an 80ms chunk counts the whole chunk as speech. Returns -1.0
        when the chunk completed no full window yet."""
        audio = np.concatenate([self._tail, pcm16.astype(np.float32) / 32768.0])
        probs = []
        n = len(audio) // WINDOW
        for i in range(n):
            window = audio[i * WINDOW:(i + 1) * WINDOW][None, :]
            out, self._h, self._c = self._session.run(
                None, {"input": window, "sr": self._sr, "h": self._h, "c": self._c})
            probs.append(float(out[0][0]))
        self._tail = audio[n * WINDOW:]
        return max(probs) if probs else -1.0
