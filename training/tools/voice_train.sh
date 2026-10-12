#!/bin/bash
# Fine-tune the Alan base on the Ryan clips (WSL). $1 = max_epochs, $2 = run name; RESUME=<ckpt> continues a run, DATA=<dir in ~/voice/data>, WARM=<ckpt to start from>, SNAP=<epochs between snapshots>, LR=<learning rate>.
export PATH="$HOME/.local/bin:$PATH"
EPOCHS="${1:-5}"; RUN="${2:-smoke}"; DATA="${DATA:-ryan}"; WARM="${WARM:-$HOME/voice/base/alan-medium.ckpt}"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
mkdir -p "$HOME/voice/runs/$RUN"
python "$(dirname "$0")/voice_run_train.py" fit \
  --data.voice_name ryan \
  --data.csv_path "$HOME/voice/data/$DATA/metadata.csv" \
  --data.audio_dir "$HOME/voice/data/$DATA/wavs" \
  --model.sample_rate 22050 \
  --data.espeak_voice en-gb-x-rp \
  --data.cache_dir "$HOME/voice/runs/$RUN/cache" \
  --data.config_path "$HOME/voice/runs/$RUN/config.json" \
  --data.batch_size 8 --data.num_workers 8 \
  --model.warmstart_ckpt "$WARM" \
  --model.use_mrd false \
  --trainer.accelerator gpu --trainer.devices 1 \
  --trainer.max_epochs "$EPOCHS" \
  --trainer.default_root_dir "$HOME/voice/runs/$RUN" \
  --trainer.log_every_n_steps 5 \
  ${RESUME:+--ckpt_path "$RESUME"}
echo "=== TRAIN_EXIT $? ==="
