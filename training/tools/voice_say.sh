#!/bin/bash
# Synthesise a line with an exported voice (WSL). $1 = voice name in ~/voice/out, $2 = text, $3 = output wav, $4 = noise_scale (0.667), $5 = noise_w (0.8), $6 = length_scale (1.0, higher = slower).
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
python - "$HOME/voice/out/$1/$1.onnx" "$2" "$3" "${4:-0.667}" "${5:-0.8}" "${6:-1.0}" <<'PY'
import sys, wave
from piper import PiperVoice, SynthesisConfig
v = PiperVoice.load(sys.argv[1])
cfg = SynthesisConfig(noise_scale=float(sys.argv[4]), noise_w_scale=float(sys.argv[5]), length_scale=float(sys.argv[6]))
with wave.open(sys.argv[3], "wb") as w:
    v.synthesize_wav(sys.argv[2], w, syn_config=cfg)
print("ok", sys.argv[3])
PY
