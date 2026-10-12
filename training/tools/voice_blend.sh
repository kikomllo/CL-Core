#!/bin/bash
# Blend a fine-tuned checkpoint with the base voice to recover phoneme coverage (WSL).
# $1 = run, $2 = ckpt file in the run, $3 = weight of the fine-tuned model (0..1), $4 = output ckpt name.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
DIR=$(ls -d "$HOME/voice/runs/$1"/lightning_logs/*/checkpoints | tail -n1)
python - "$DIR/$2" "$HOME/voice/base/alan-medium.ckpt" "$3" "$DIR/$4" <<'PY'
import pathlib, sys, torch
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
fine, base, a, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
f = torch.load(fine, map_location="cpu", weights_only=False)
b = torch.load(base, map_location="cpu", weights_only=False)["state_dict"]
n = 0
for k, v in f["state_dict"].items():
    if k in b and b[k].shape == v.shape and v.is_floating_point():
        f["state_dict"][k] = a * v + (1 - a) * b[k]; n += 1
torch.save(f, out)
print(f"blended {n} tensors at {a:.2f} fine-tuned -> {out}")
PY
