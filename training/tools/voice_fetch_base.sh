#!/bin/bash
# Downloads a Piper training checkpoint into ~/voice/base inside WSL. RESUMABLE (curl -C -): re-running it carries
# on from the partial file, so a pause, a dropped connection or a stop never costs progress.
#   voice_fetch_base.sh [name] [path under the checkpoints dataset] [checkpoint file]
# default: the Alan (single-speaker British male, medium) checkpoint.
BASE="https://huggingface.co/datasets/rhasspy/piper-checkpoints/resolve/main"
name="${1:-alan-medium}"
path="${2:-en/en_GB/alan/medium}"
ckpt="${3:-epoch=6339-step=1647790.ckpt}"
mkdir -p "$HOME/voice/base" && cd "$HOME/voice/base" || exit 1
rm -f "$name.done"
curl -C - -L --fail --retry 8 --retry-delay 3 --retry-all-errors -o "$name.ckpt" "$BASE/$path/$ckpt" || exit 1
curl -L --fail -s -o "$name.config.json" "$BASE/$path/config.json" || exit 1
ls -l "$name.ckpt"
echo done > "$name.done"
