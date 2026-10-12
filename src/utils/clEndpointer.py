class Endpointer:
    """Decides when a spoken command has started and finished, from per-chunk speech
    probabilities (see clVad.SileroVad). Pure logic, no audio.

    Replaces the old volume-threshold silence window: the end of the command is
    `end_silence_s` of non-speech after real speech, not "quiet for 2+ seconds"."""

    WAITING, SPEAKING, END, TIMEOUT = "waiting", "speaking", "end", "timeout"

    def __init__(self, chunk_s: float, end_silence_s: float = 0.7, wait_s: float = 3.0,
                 speech_on: float = 0.5, speech_off: float = 0.35, min_speech_s: float = 0.16,
                 already_speaking: bool = False):
        self.chunk_s = chunk_s
        self.end_silence_s = end_silence_s
        self.wait_s = wait_s
        self.speech_on = speech_on
        self.speech_off = speech_off      # hysteresis: once speaking, only clearly-low scores count as silence
        self.min_speech_s = min_speech_s  # ignores clicks/coughs shorter than this
        self.started = already_speaking
        self.in_speech = already_speaking
        self._speech_run = 0.0
        self._silence_run = 0.0
        self._waited = 0.0

    def update(self, prob: float) -> str:
        """Feed one chunk's speech probability; returns the state afterwards.
        A negative `prob` means the chunk gave no score yet and is ignored."""
        if prob < 0:
            return self.SPEAKING if self.started else self.WAITING

        threshold = self.speech_off if self.in_speech else self.speech_on
        self.in_speech = prob >= threshold

        if self.in_speech:
            self._speech_run += self.chunk_s
            self._silence_run = 0.0
            if not self.started and self._speech_run >= self.min_speech_s - 1e-9:
                self.started = True
        else:
            self._speech_run = 0.0
            if self.started:
                self._silence_run += self.chunk_s
                if self._silence_run >= self.end_silence_s - 1e-9:   # float sums of chunk lengths
                    return self.END
            else:
                self._waited += self.chunk_s
                if self._waited >= self.wait_s - 1e-9:
                    return self.TIMEOUT

        return self.SPEAKING if self.started else self.WAITING

    def hold(self) -> None:
        """Push-to-talk is down: the user is speaking by definition, never end."""
        self.started = True
        self._silence_run = 0.0
