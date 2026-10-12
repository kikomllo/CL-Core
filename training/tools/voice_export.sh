#!/bin/bash
# Export a run checkpoint to ONNX and synthesise a test line (WSL). $1 = run, $2 = ckpt file, $3 = output voice name.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
CK=$(ls -d "$HOME/voice/runs/$1"/lightning_logs/*/checkpoints | tail -n1)/$2
OUT="$HOME/voice/out/$3"; mkdir -p "$OUT"
python - "$CK" "$OUT/$3.onnx" <<'PY'
import pathlib, sys, torch, runpy
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
import functools
_orig = torch.onnx.export
torch.onnx.export = functools.partial(_orig, dynamo=False)
sys.argv = ["export_onnx", "--checkpoint", sys.argv[1], "--output-file", sys.argv[2]]
runpy.run_module("piper.train.export_onnx", run_name="__main__", alter_sys=True)
PY
cp "$HOME/voice/runs/$1/config.json" "$OUT/$3.onnx.json"
python - "$OUT/$3.onnx" "$OUT/test.wav" <<'PY'
import sys, wave
from piper import PiperVoice
v = PiperVoice.load(sys.argv[1])
with wave.open(sys.argv[2], "wb") as w:
    v.synthesize_wav("Good evening sir. The lights are now set and all systems are online.", w)
print("synth ok", sys.argv[2])
PY
ls -la "$OUT"
echo "=== EXPORT_DONE ==="
