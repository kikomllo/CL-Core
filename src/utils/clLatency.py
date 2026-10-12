import time
from typing import Dict, Optional

# Voice-command stages in pipeline order, with the label for the gap that
# ends at each one. Services stamp wall-clock time.time() (same machine, so
# comparable across processes) and the Daemon joins them into one line.
STAGES = [
    ("t_speech_end", None),
    ("t_dispatch", "endpoint"),     # silence wait before the mic sends the clip
    ("t_stt_recv", "wire"),
    ("t_stt_start", "queue"),
    ("t_stt_infer", "wav"),
    ("t_stt_done", "whisper"),
    ("t_daemon_recv", "mqtt"),
    ("t_routed", "route"),
    ("t_action", "action"),
    ("t_reply_ready", "reply_gen"),
    ("t_tts_req", "to_tts"),
    ("t_synth_done", "tts_synth"),
    ("t_audio", "duck+blip"),
]


class LatencyTrace:
    """Timestamps for one voice command, carried mic -> Whisper -> Daemon -> TTS."""

    def __init__(self, carried: Optional[dict] = None):
        self.t: Dict[str, float] = {
            k: float(v) for k, v in (carried or {}).items()
            if k in dict(STAGES) and isinstance(v, (int, float))
        }
        self.created: float = time.time()
        self.closed: bool = False
        self.cached: Optional[bool] = None

    def mark(self, stage: str, when: Optional[float] = None) -> None:
        """First stamp wins, so a repeated stage never rewrites history."""
        self.t.setdefault(stage, when if when is not None else time.time())

    def summary(self) -> str:
        present = [(key, label) for key, label in STAGES if key in self.t]
        if len(present) < 2:
            return ""
        parts = []
        prev = present[0][0]
        for key, label in present[1:]:
            parts.append(f"{label}={self.t[key] - self.t[prev]:.2f}s")
            prev = key
        if self.cached is not None:
            parts.append("(tts cached)" if self.cached else "(tts synthesized)")
        total = self.t[present[-1][0]] - self.t[present[0][0]]
        first, last = present[0][0][2:], present[-1][0][2:]
        return f"{' | '.join(parts)} | TOTAL {first}->{last}: {total:.2f}s"
