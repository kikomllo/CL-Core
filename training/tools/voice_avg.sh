#!/bin/bash
# Average the weights of several checkpoints from one run (smooths snapshot-to-snapshot GAN noise) (WSL).
# $1 = run, $2 = output ckpt name, $3... = checkpoint files inside the run (a resumed run keeps them in several version_N folders: found in any; the output goes to the newest).
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$HOME/voice/piper1-gpl/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
RUN="$1"; OUT="$2"; shift 2
DIR=$(ls -d "$HOME/voice/runs/$RUN"/lightning_logs/*/checkpoints | tail -n1)
python - "$DIR" "$OUT" "$@" <<'PY'
import pathlib, sys, torch
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
d, out, files = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
found = {p.name: p for v in sorted(d.parent.parent.glob('*/checkpoints')) for p in v.glob('*.ckpt')}
acc = None
for f in files:
    ck = torch.load(found[f], map_location="cpu", weights_only=False)
    if acc is None:
        acc, base = {k: v.clone().double() if v.is_floating_point() else v for k, v in ck["state_dict"].items()}, ck
    else:
        for k, v in ck["state_dict"].items():
            if v.is_floating_point(): acc[k] += v.double()
for k, v in acc.items():
    if v.is_floating_point(): base["state_dict"][k] = (v / len(files)).float()
torch.save(base, d / out)
print(f"averaged {len(files)} checkpoints -> {d / out}")
PY
