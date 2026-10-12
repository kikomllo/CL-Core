#!/bin/bash
# Synthesise straight from a training checkpoint in PyTorch (no ONNX), to tell model faults from export faults (WSL).
# $1 = run, $2 = ckpt file, $3 = text, $4 = output wav, $5 = noise_scale, $6 = noise_w, $7 = length_scale, $8 = min frames per phoneme (0 = off), $9 = seed
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/voice/piper1-gpl" && source .venv/bin/activate
DIR=$(ls -d "$HOME/voice/runs/$1"/lightning_logs/*/checkpoints | tail -n1)
python - "$DIR/$2" "$HOME/voice/runs/$1/config.json" "$3" "$4" "${5:-0.667}" "${6:-0.8}" "${7:-1.0}" "${8:-0}" "${9:-7}" <<'PY'
import os, pathlib, sys, wave, numpy as np, torch
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
from piper.train.vits.lightning import VitsModel
from piper.config import PiperConfig
from piper.phoneme_ids import phonemes_to_ids
from piper.phonemize_espeak import EspeakPhonemizer
ckpt, cfg_path, text, out, ns, nw, ls, minf, seed = sys.argv[1:10]
cfg = PiperConfig.from_dict(__import__("json").load(open(cfg_path)))
CFG = cfg
model = VitsModel.load_from_checkpoint(ckpt, map_location="cpu").eval()
g = model.model_g
with torch.no_grad():
    g.dec.remove_weight_norm()
from piper.train.vits import commons
def infer(g, x, x_lengths, noise_scale, length_scale, noise_scale_w, min_frames):
    """VitsModel.infer with a floor on every real phoneme's duration (pads stay free)."""
    xe, m_p, logs_p, x_mask = g.enc_p(x, x_lengths)
    logw = g.dp(xe, x_mask, g=None, reverse=True, noise_scale=noise_scale_w)
    w_ceil = torch.ceil(torch.exp(logw) * x_mask * length_scale)
    if min_frames:
        real = (x != 0).unsqueeze(1).type_as(w_ceil)
        w_ceil = torch.where(real.bool(), torch.clamp_min(w_ceil, min_frames), w_ceil) * x_mask
    if os.environ.get('DUMP'):
        inv = {v[0]: k for k, v in CFG.phoneme_id_map.items()}
        print('DUR', ' '.join(f"{inv.get(int(t), '?')}:{int(d)}" for t, d in zip(x[0].tolist(), w_ceil[0, 0].tolist()) if int(t) != 0))
    y_lengths = torch.clamp_min(torch.sum(w_ceil, [1, 2]), 1).long()
    y_mask = torch.unsqueeze(commons.sequence_mask(y_lengths, y_lengths.max()), 1).type_as(x_mask)
    attn = commons.generate_path(w_ceil, torch.unsqueeze(x_mask, 2) * torch.unsqueeze(y_mask, -1))
    m_p = torch.matmul(attn.squeeze(1), m_p.transpose(1, 2)).transpose(1, 2)
    logs_p = torch.matmul(attn.squeeze(1), logs_p.transpose(1, 2)).transpose(1, 2)
    z_p = m_p + torch.randn_like(m_p) * torch.exp(logs_p) * noise_scale
    z = g.flow(z_p, y_mask, g=None, reverse=True)
    return g.dec(z * y_mask).squeeze().numpy()
ph = EspeakPhonemizer()
audio = []
torch.manual_seed(int(seed))
sents = ph.phonemize(cfg.espeak_voice, text)
if os.environ.get('MERGE'):
    sents = [[p for i, s_ in enumerate(sents) for p in ([' '] if i else []) + list(s_)]]
print('SENTENCES', [len(s_) for s_ in sents])
for sent in sents:
    ids = phonemes_to_ids(sent, cfg.phoneme_id_map) if False else None
    ids = [i for p in sent for i in cfg.phoneme_id_map.get(p, [])] if False else None
    # same id layout as PiperVoice: BOS, then each phoneme followed by pad, then EOS
    m = cfg.phoneme_id_map; seq = list(m["^"]);
    for p in sent:
        if p in m: seq += m[p] + m["_"]
    seq += m["$"]
    x = torch.LongTensor([seq]); xl = torch.LongTensor([len(seq)])
    with torch.no_grad():
        a = infer(g, x, xl, float(ns), float(ls), float(nw), int(minf))
    audio.append(a); audio.append(np.zeros(int(0.2*cfg.sample_rate), dtype=np.float32))
y = np.concatenate(audio[:-1]); y = y / max(1.0, np.abs(y).max()/0.95)
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(cfg.sample_rate); w.writeframes((y*32767).astype(np.int16).tobytes())
print("ok", out, f"{len(y)/cfg.sample_rate:.2f}s")
PY
