import json
import os
import statistics
from collections import deque
from typing import Optional


class EchoGuard:
    """Keeps JARVIS's own voice at a level where interrupting it is still possible, on any hardware.

    Echo cancellation has a ceiling (measured: it rescues the wake word up to a speaker ~3x louder than
    your voice at the mic, not 10x). Beyond that the only variable left is JARVIS's own output level, so:
    learn how loud the user's commands are (`note_voice`) and how loud JARVIS's voice arrives back at the
    mic per unit of TTS gain (`note_echo`), and lower the TTS gain just enough to stay under
    `target_esr_db` (echo-to-voice ratio). Raises it again, gently, once there is room. Pure logic."""

    def __init__(self, target_esr_db: float = 4.0, min_gain: float = 0.1, voice_default: float = 0.03,
                 state_path: Optional[str] = None):
        self.target = 10 ** (target_esr_db / 20.0)
        self.min_gain = min_gain
        self.voice_default = voice_default       # a typical desk-mic command, rms of full scale
        self.state_path = state_path
        self.gain = 1.0
        self.echo_unit: Optional[float] = None   # echo rms at the mic per 1.0 of TTS gain
        self._voices = deque(maxlen=20)
        self._echo_count = 0
        self._load()

    @property
    def voice(self) -> float:
        return statistics.median(self._voices) if len(self._voices) >= 3 else self.voice_default

    def esr_db(self) -> Optional[float]:
        """Echo-to-voice ratio at the mic right now, in dB (None until an echo has been measured)."""
        if self.echo_unit is None:
            return None
        import math
        return 20 * math.log10(max(self.echo_unit * self.gain, 1e-9) / self.voice)

    def note_voice(self, rms: float) -> None:
        if rms > 0:
            self._voices.append(float(rms))
            self._save()

    def note_echo(self, echo_rms: float, gain_used: float) -> Optional[float]:
        """One utterance's echo level (room noise already removed) measured while TTS gain was
        `gain_used`. Returns the new TTS gain when it should change, else None."""
        if echo_rms <= 0 or gain_used <= 0:
            return None
        unit = echo_rms / gain_used
        self.echo_unit = unit if self.echo_unit is None else 0.7 * self.echo_unit + 0.3 * unit
        self._echo_count += 1
        if self._echo_count < 2:                  # one utterance can be contaminated by the user talking
            return None

        wanted = max(self.min_gain, min(1.0, self.target * self.voice / self.echo_unit))
        new = self.gain
        if wanted < self.gain * 0.85:             # too loud for interrupting: step down (max 6 dB at a time)
            new = max(wanted, self.gain * 0.5)
        elif wanted > self.gain * 1.25 and self.gain < 1.0:
            new = min(wanted, self.gain * 1.25)   # room to spare: come back up (max ~2 dB at a time)
        if abs(new - self.gain) < 1e-6:
            self._save()
            return None
        self.gain = new
        self._save()
        return new

    # --- persistence (data/barge_in_state.json: runtime state, gitignored) ---
    def _load(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.gain = min(1.0, max(self.min_gain, float(state.get("gain", 1.0))))
            self.echo_unit = state.get("echo_unit")
            self._voices.extend(float(v) for v in state.get("voices", []))
        except Exception:
            pass

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"gain": self.gain, "echo_unit": self.echo_unit, "voices": list(self._voices)}, f)
            os.replace(tmp, self.state_path)
        except Exception:
            pass
