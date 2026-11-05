#!/bin/bash
# Fine-tune a Piper base checkpoint on a clip set (WSL). $1 = max_epochs, $2 = run name; RESUME=<ckpt> continues a run, DATA=<dir in ~/voice/data>, WARM=<ckpt to start from>, SNAP=<epochs between snapshots>,
# VOICE=<voice name>, ESPEAK=<espeak voice>, LR / LR_D=<generator / discriminator learning rate>, BATCH, WORKERS, VAL (validation share, default 0.05). Defaults are the original Alan/Ryan-clips run.
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$HOME/voice/piper1-gpl/src${PYTHONPATH:+:$PYTHONPATH}"       # piper1-gpl is used from its source tree, not pip-installed (voice_setup_wsl.sh)
EPOCHS="${1:-5}"; RUN="${2:-smoke}"; DATA="${DATA:-ryan}"; WARM="${WARM:-$HOME/voice/base/alan-medium.ckpt}"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
mkdir -p "$HOME/voice/runs/$RUN"
python "$(dirname "$0")/voice_run_train.py" fit \
  --data.voice_name "${VOICE:-ryan}" \
  --data.csv_path "$HOME/voice/data/$DATA/metadata.csv" \
  --data.audio_dir "$HOME/voice/data/$DATA/wavs" \
  --model.sample_rate 22050 \
  --data.espeak_voice "${ESPEAK:-en-gb-x-rp}" \
  --data.cache_dir "$HOME/voice/runs/$RUN/cache" \
  --data.config_path "$HOME/voice/runs/$RUN/config.json" \
  --data.batch_size "${BATCH:-8}" --data.num_workers "${WORKERS:-8}" \
  ${LR:+--model.learning_rate "$LR"} ${LR_D:+--model.learning_rate_d "$LR_D"} \
  --model.warmstart_ckpt "$WARM" \
  --model.use_mrd false \
  --model.mos_metric none --data.validation_split "${VAL:-0.05}" \
  --trainer.accelerator gpu --trainer.devices 1 \
  --trainer.max_epochs "$EPOCHS" \
  --trainer.default_root_dir "$HOME/voice/runs/$RUN" \
  --trainer.log_every_n_steps 5 \
  ${RESUME:+--ckpt_path "$RESUME"}
echo "=== TRAIN_EXIT $? ==="
