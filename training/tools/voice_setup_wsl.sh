#!/bin/bash
# Rebuilds the local Piper training environment in WSL (Ubuntu) under ~/voice. Safe to re-run: every step skips what exists.
#   bash voice_setup_wsl.sh            (run inside WSL; MSYS_NO_PATHCONV=1 when started from Git Bash)
# What it does (the recipe in training/CLAUDE.md "Local voice training"): uv + a Python 3.11 venv in ~/voice/piper1-gpl,
# torch from the cu128 index, the training dependencies, the Cython alignment extension, and the espeak bridge copied from the
# PyPI piper-tts wheel (the repo's own build needs g++/cmake, which this WSL does not have and there is no sudo).
# piper1-gpl is used straight from src/ (PYTHONPATH=src, set by voice_train.sh / voice_export.sh), not pip-installed.
set -e
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p ~/voice && cd ~/voice

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -d piper1-gpl ] || git clone --depth 1 https://github.com/OHF-Voice/piper1-gpl
cd piper1-gpl

[ -d .venv ] || uv venv --python 3.11 .venv
source .venv/bin/activate

python -c "import torch" 2>/dev/null || uv pip install "torch==2.11.0" --index-url https://download.pytorch.org/whl/cu128
uv pip install "lightning>=2,<3" "tensorboard>=2,<3" "tensorboardX>=2,<3" "jsonargparse[signatures]>=4.27.7" \
    "onnx>=1,<2" onnxscript "onnxruntime>=1,<2" "pathvalidate>=3,<4" "pysilero-vad>=2.1,<3" "cython>=3,<4" "librosa<1" numpy scipy

if ! ls src/piper/espeakbridge*.so >/dev/null 2>&1; then
    rm -rf /tmp/piper_wheel && uv pip install --no-deps --target /tmp/piper_wheel "piper-tts==1.8.0"
    cp /tmp/piper_wheel/piper/espeakbridge*.so src/piper/
    cp -r /tmp/piper_wheel/piper/espeak-ng-data src/piper/
fi

if ! ls src/piper/train/vits/monotonic_align/monotonic_align/core*.so >/dev/null 2>&1; then
    ./build_monotonic_align.sh
fi

export PYTHONPATH="$PWD/src"
python - <<'PY'
import torch, piper.train.vits.monotonic_align as ma, piper.espeakbridge
print("torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
print("piper train imports ok")
PY
echo "=== SETUP_DONE ==="
