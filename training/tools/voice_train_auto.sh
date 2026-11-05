#!/bin/bash
# Runs voice_train.sh and keeps it going unattended: if it dies (CUDA OOM, a crash) it resumes from the run's last.ckpt, dropping
# the batch size to BATCH_FALLBACK after the first failure (LOWER_BATCH_ON_RETRY=0 keeps it); up to MAX_ATTEMPTS (40) tries. The WSL kernel OOM-killer
# (host RAM, exit 137) has killed a run every ~15 min when WSL had 6 GB: WORKERS=2 and MALLOC_ARENA_MAX=2 help, and the resume costs about a minute. Same env as voice_train.sh.
#   bash voice_train_auto.sh <epochs> <run>      e.g. DATA=ultron VOICE=actor_voice ESPEAK=en-us WARM=... LR=1e-4 LR_D=5e-5 SNAP=25 bash voice_train_auto.sh 400 ultron_v1
here="$(cd "$(dirname "$0")" && pwd)"
EPOCHS="${1:-5}"; RUN="${2:-auto}"
BATCH_FIRST="${BATCH:-8}"; BATCH_FALLBACK="${BATCH_FALLBACK:-6}"
for attempt in $(seq 1 ${MAX_ATTEMPTS:-40}); do
  last=$(ls -t "$HOME"/voice/runs/"$RUN"/lightning_logs/*/checkpoints/last.ckpt 2>/dev/null | head -1)
  b="$BATCH_FIRST"; [ "$attempt" -gt 1 ] && [ "${LOWER_BATCH_ON_RETRY:-1}" = 1 ] && b="$BATCH_FALLBACK"
  echo "=== ATTEMPT $attempt batch $b resume ${last:-none} ($(date +%H:%M:%S)) ==="
  BATCH="$b" RESUME="$last" bash "$here/voice_train.sh" "$EPOCHS" "$RUN"
  code=$?
  # a clean finish: the run reached its last epoch
  if [ "$code" -eq 0 ] && tr '\r' '\n' < "$HOME/voice/$RUN.log" 2>/dev/null | grep -q "Epoch $((EPOCHS - 1)):.*100%"; then
    echo "=== AUTO_DONE ($(date +%H:%M:%S)) ==="; exit 0
  fi
  echo "=== ATTEMPT $attempt ended with code $code, retrying in 20 s ==="; sleep 20
done
echo "=== AUTO_GAVE_UP ==="; exit 1
