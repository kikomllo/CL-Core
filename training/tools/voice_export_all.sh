#!/bin/bash
# Exports every snapshot (and the best-val_mel checkpoints) of a run to ONNX, one after another (WSL). $1 = run, $2 = name prefix.
# Run after training: exporting during it starves WSL of RAM.   python training/tools/voice_job.py start export_v2 bash /mnt/c/GitHub/CL-Core/training/tools/voice_export_all.sh ultron_v2 ultron_v2
RUN=$1; PREFIX=$2; HERE=$(dirname "$0")
CKDIR=$(ls -d "$HOME/voice/runs/$RUN"/lightning_logs/*/checkpoints | tail -n1)
for f in "$CKDIR"/snap-epoch=*.ckpt "$CKDIR"/epoch=*-val_mel=*.ckpt; do
    b=$(basename "$f" .ckpt)
    if [[ $b == snap-epoch=* ]]; then n=$(( ${b#snap-epoch=} + 1 )); name="${PREFIX}_e$(printf %03d $n)"
    else e=${b#epoch=}; name="${PREFIX}_best$(printf %03d $(( ${e%%-*} + 1 )))"; fi
    bash "$HERE/voice_export.sh" "$RUN" "$(basename "$f")" "$name" > /dev/null 2>&1
    echo "exported $name: $(ls "$HOME/voice/out/$name/$name.onnx" 2>&1 | tail -n1)"
done
echo ALL_EXPORTS_DONE
