"""
Kaggle Training Script for the custom Piper voice (fine-tune)
=============================================================

Fine-tunes a stock Piper voice checkpoint on the clips made by collect_voice.py, then exports every
good checkpoint to ONNX with a test sentence rendered from each, so you can pick the best one by ear
(the loss alone doesn't tell you which sounds best, especially on a few minutes of audio).

UNTESTED ON KAGGLE: written against Piper's training docs and source (OHF-Voice/piper1-gpl, as of
2026-10). If a step fails, the error will name the flag or package; the commands below are the same ones
the Piper docs show, so they're the first place to compare.

SETUP (before running):
-----------------------
1. Locally:  python training/tools/export_piper_dataset.py
   -> training/data/voice_export/piper_voice_dataset.zip
2. kaggle.com -> New Dataset -> upload that zip (Kaggle unzips it; you get metadata.csv + wavs/).
3. New Notebook -> Settings: Accelerator GPU T4 (ONE GPU is enough), Internet ON.
4. "+ Add data" -> your dataset. Paste this whole file into one code cell and run it.
5. When it finishes, download /kaggle/working/piper_voice_out.zip (ONNX per checkpoint + test wavs).
6. Put the best .onnx and its .onnx.json in models/piper/ and set
   config/core.json -> settings.tts_settings.piper_model to it.

Train on the CLEAN clips (as exported): the voice effect is applied after synthesis in src/utils/clVoice.py.
"""

import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import json
import zipfile

# =============================================================================
# SETTINGS
# =============================================================================
BASE_VOICE = "en/en_US/ryan/medium"   # stock checkpoint to fine-tune (must be a *medium* voice, 22050 Hz)
EXTRA_EPOCHS = 300                    # epochs of fine-tuning (the counter starts at 0: see the warm start in CELL 3)
LEARNING_RATE = 1e-4                  # generator; half Piper's default 2e-4, since this is fine-tuning on a few minutes
LEARNING_RATE_D = 5e-5                # discriminator
USE_MOS = False                       # val_mos (perceptual score): off, see the note in CELL 3
SNAPSHOT_EVERY = 25                   # also save a weights-only checkpoint every N epochs, so you can compare early vs late
CONTINUE_FROM = None                  # continue from your own weights instead of the stock voice: a .ckpt path or glob, e.g.
                                      # "/kaggle/working/lightning_logs/snapshots/epoch=300-snapshot.ckpt" (same session) or
                                      # "/kaggle/input/**/epoch=300-snapshot.ckpt" (uploaded as a dataset). See CELL 2.
CONTINUE_EPOCHS = 120                 # epochs when continuing (the voice is already adapted: a short, gentle refinement)
CONTINUE_LR, CONTINUE_LR_D = 5e-5, 2.5e-5   # half of the first run's rates
SKIP_TRAINING = False                 # True = skip CELL 3 and just export what is in lightning_logs/ (after an interrupted run)
BATCH_SIZE = 8                        # small, so a few minutes of audio still gives several steps per epoch
VOICE_NAME = "actor_voice"
ESPEAK_VOICE = "en-us"
VALIDATION_SPLIT = 0.06               # about 4 clips held out of 72: only used to rank checkpoints (+3 test sentences)
TEST_SENTENCES = [
    "I have been waiting for you. The lights are off, and the house is quiet.",
    "Good evening. Shall I do anything else for you?",
    "There are no strings on me.",
]

WORK = "/kaggle/working"
REPO = f"{WORK}/piper1-gpl"
CACHE = f"{WORK}/cache"
CONFIG = f"{WORK}/{VOICE_NAME}.json"
OUT = f"{WORK}/piper_voice_out"
PREVIEW_DIR = f"{WORK}/previews"
HF = "https://huggingface.co"


def sh(cmd, cwd=None, quiet=False):
    """Run a shell command. quiet=True keeps the output in build.log and prints only its tail if the command
    fails (the espeak-ng build prints hundreds of harmless compiler warnings)."""
    print(f"\n$ {cmd}")
    if not quiet:
        subprocess.run(cmd, shell=True, check=True, cwd=cwd)
        return
    log = f"{WORK}/build.log"
    with open(log, "w") as f:
        code = subprocess.run(cmd, shell=True, cwd=cwd, stdout=f, stderr=subprocess.STDOUT).returncode
    if code:
        # In the exception text itself, so a pasted traceback carries the cause.
        raise RuntimeError(f"'{cmd}' failed (exit code {code}). Last lines of {log}:\n{open(log).read()[-3500:]}")


# PyTorch >= 2.6 loads checkpoints with weights_only=True, which refuses Piper's published checkpoints (they
# store a pathlib.PosixPath), and Lightning asks for weights_only=True explicitly. So Piper's modules are run
# through this tiny launcher, which turns that restriction off in the training process only. The checkpoints
# come from Piper's own repository (rhasspy/piper-checkpoints); don't point this at files you don't trust.
LAUNCHER = f"{WORK}/run_module.py"
LAUNCHER_SRC = '''import os, runpy, sys, torch
_orig_load = torch.load
def _load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _orig_load(*args, **kwargs)
torch.load = _load
# Piper's export_onnx.py was written for the TorchScript ONNX exporter (opset 15, dynamic_axes). Recent PyTorch
# defaults to the "dynamo" exporter, which needs the onnxscript package and may not trace this model. Ask for the
# older exporter first; if that fails (or the PyTorch version no longer has it) fall back to the default one.
import torch.onnx
_orig_export = torch.onnx.export
def _export(*args, **kwargs):
    if "dynamo" not in kwargs:
        try:
            return _orig_export(*args, dynamo=False, **kwargs)
        except Exception as e:
            print(f"[onnx] older exporter failed ({type(e).__name__}: {str(e)[:200]}); retrying with the default one", flush=True)
    return _orig_export(*args, **kwargs)
torch.onnx.export = _export
from lightning.pytorch.callbacks import ModelCheckpoint
_orig_topk = ModelCheckpoint._save_topk_checkpoint
_printed = {"epoch": -1}
def _topk(self, trainer, monitor_candidates):
    # 1) One "[epoch]" line per epoch. Lightning's default progress bar (rich) prints nothing when its output is
    #    a pipe, which makes a healthy run look frozen. Done here, in a method that is already wrapped, and NOT
    #    by adding a callback or replacing Trainer.__init__: LightningCLI reads the Trainer's signature to build
    #    its options, and a wrapped __init__ made it fail with 'No action for key "trainer.max_epochs"'.
    try:
        if trainer.current_epoch != _printed["epoch"]:
            _printed["epoch"] = trainer.current_epoch
            vals = " ".join(f"{k}={float(monitor_candidates[k]):.3f}"
                            for k in ("loss_g", "loss_d", "val_mel", "val_mos") if k in monitor_candidates)
            print(f"[epoch] {trainer.current_epoch + 1}/{trainer.max_epochs} step={trainer.global_step} {vals}", flush=True)
    except Exception:
        pass                                   # status output must never be able to stop the training
    # 1b) Piper's own checkpoints are only the best few by val_mel (the latest epochs, as it keeps improving) and
    #     the last one, so there is nothing to compare an early epoch with a late one. Save a weights-only snapshot
    #     every SNAPSHOT_EVERY epochs under lightning_logs/snapshots/ (CELL 4 exports it with the rest).
    try:
        every = int(os.environ.get("SNAPSHOT_EVERY", "0"))
        if every and (trainer.current_epoch + 1) % every == 0 and self.monitor == "val_mel":
            snap_dir = os.path.join(os.getcwd(), "lightning_logs", "snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            snap = os.path.join(snap_dir, f"epoch={trainer.current_epoch + 1}-snapshot.ckpt")
            trainer.save_checkpoint(snap, weights_only=True)
            if os.environ.get("PREVIEW_SCRIPT"):
                import subprocess
                subprocess.Popen([sys.executable, os.environ["PREVIEW_SCRIPT"], snap, os.environ["PREVIEW_DIR"],
                                  os.environ["PREVIEW_CONFIG"], os.environ["PREVIEW_TEXT"], os.path.abspath(__file__)],
                                 stdout=subprocess.DEVNULL, stderr=open(os.path.join(os.getcwd(), "preview.log"), "a"))
                print(f"[snapshot] epoch {trainer.current_epoch + 1} saved; preview wav -> {os.environ['PREVIEW_DIR']}/ in a minute", flush=True)
    except Exception as e:
        print(f"[snapshot] skipped: {e}", flush=True)
    # 2) Piper also keeps the checkpoints with the best val_mos (a perceptual score from a downloaded quality
    #    model). If that score is never logged (model failed to load, no test sentences), Lightning raises and
    #    kills the run; skip just that ranking. The val_mel and last checkpoints are unaffected.
    if self.monitor == "val_mos" and "val_mos" not in monitor_candidates:
        return
    return _orig_topk(self, trainer, monitor_candidates)
ModelCheckpoint._save_topk_checkpoint = _topk
# 3) Piper loads its val_mos quality model (UTMOS, via torch.hub) on the CPU and never moves it, so every
#    validation epoch ran a wav2vec2-sized model on the CPU (GPU idle at ~6%, ~20 s per 8-batch epoch). Move it to
#    the GPU once it is loaded; score() already sends the audio to wherever the model lives.
try:
    import piper.train.vits.mos as _mos
    _orig_ensure = _mos.MosPredictor._ensure_model
    def _ensure(self):
        _orig_ensure(self)
        try:
            if self._model is not None and torch.cuda.is_available() and next(self._model.parameters()).device.type != "cuda":
                self._model.to("cuda")
        except Exception:
            pass                               # stays on the CPU: slower, still correct
    _mos.MosPredictor._ensure_model = _ensure
except Exception:
    pass
module = sys.argv[1]
sys.argv = [module] + sys.argv[2:]
runpy.run_module(module, run_name="__main__", alter_sys=True)
'''
os.makedirs(WORK, exist_ok=True)
with open(LAUNCHER, "w") as f:
    f.write(LAUNCHER_SRC)

# Run in the background after each snapshot (on the CPU, so it doesn't compete with the training for the GPU):
# exports that snapshot to ONNX, renders the first test sentence, keeps only the .wav in previews/. Open the
# Output/files panel in Kaggle while the training runs and download the wavs to hear how the voice is coming along.
PREVIEW_SCRIPT = f"{WORK}/preview_snapshot.py"
PREVIEW_SRC = '''import os, shutil, subprocess, sys, wave
ckpt, out_dir, config, text, launcher = sys.argv[1:6]
os.makedirs(out_dir, exist_ok=True)
name = os.path.basename(ckpt)[:-5]
onnx = os.path.join(out_dir, name + ".onnx")
subprocess.run([sys.executable, launcher, "piper.train.export_onnx", "--checkpoint", ckpt, "--output-file", onnx],
               check=True, env=dict(os.environ, CUDA_VISIBLE_DEVICES=""))
shutil.copy(config, onnx + ".json")
from piper import PiperVoice
with wave.open(os.path.join(out_dir, name + ".wav"), "wb") as w:
    PiperVoice.load(onnx).synthesize_wav(text, w)
os.remove(onnx)
os.remove(onnx + ".json")
'''
with open(PREVIEW_SCRIPT, "w") as f:
    f.write(PREVIEW_SRC)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROGRESS = re.compile(r"\d(it/s|s/it)")             # Lightning's progress bar lines ("4.1it/s, v_num=0, loss_g=...]")


def gpu_status():
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                                      "--format=csv,noheader,nounits"], text=True, timeout=5).splitlines()[0]
        util, mem = [x.strip() for x in out.split(",")]
        return f"gpu {util}% {int(mem) / 1024:.1f} GB"
    except Exception:
        return "gpu ?"


def train(cmd, base_epoch, max_epochs, every=20):
    """Run the training command and print one status line every `every` seconds -- on a timer, so it keeps
    coming even while the run prints nothing -- with the epoch, elapsed time, checkpoints saved, GPU use and the
    latest metrics. The launcher prints an "[epoch] N/M ..." line per epoch; those update the status instead of
    being printed one by one. Anything else (warnings, errors, validation messages) is printed as it comes.
    The full output is kept in train.log."""
    print(f"\n$ {cmd}")
    t0 = time.time()
    proc = subprocess.Popen(cmd, shell=True, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    state = {"latest": "", "epoch": base_epoch}

    def handle(text):
        text = ANSI.sub("", text).strip()
        if not text:
            return
        m = re.match(r"\[epoch\] (\d+)/", text)
        if m:
            state["epoch"] = int(m.group(1))
            state["latest"] = text.split(" ", 2)[-1]
        elif not PROGRESS.search(text):
            print(text, flush=True)

    log = open(f"{WORK}/train.log", "w")

    def reader():
        buf = ""
        for ch in iter(lambda: proc.stdout.read(1), ""):
            log.write(ch)
            if ch in "\r\n":
                handle(buf)
                buf = ""
            else:
                buf += ch
        handle(buf)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    def status():
        n_ckpt = len(glob.glob(f"{WORK}/lightning_logs/**/*.ckpt", recursive=True))
        done = max(0, state["epoch"] - base_epoch)
        total = max(1, max_epochs - base_epoch)
        print(f"[{int(time.time() - t0) // 60:3d} min] epoch {state['epoch']}/{max_epochs} ({100 * done // total}%)  "
              f"checkpoints: {n_ckpt}  {gpu_status()}  | {state['latest']}", flush=True)

    last = time.time()
    while proc.poll() is None:
        time.sleep(1)
        if time.time() - last >= every:
            status()
            last = time.time()
    t.join(timeout=10)
    log.close()
    status()
    if proc.returncode:
        # In the exception text itself, so a pasted traceback carries the cause (not only the exit code).
        raise RuntimeError(f"Training stopped with exit code {proc.returncode}. Last lines of {WORK}/train.log:\n"
                           f"{open(f'{WORK}/train.log').read()[-3500:]}")


def archive(path):
    """Rename the previous run's folder (kept for download) so a new run's files don't mix with it."""
    if os.path.isdir(path) and os.listdir(path):
        os.rename(path, f"{path}_prev_{time.strftime('%H%M%S')}")


def download(url, dest, every_pct=10):
    """urlretrieve with a progress line every `every_pct` percent."""
    state = {"next": 0}

    def hook(blocks, size, total):
        if total > 0:
            pct = min(100, blocks * size * 100 // total)
            if pct >= state["next"]:
                print(f"  downloaded {pct}%  ({blocks * size / 1e6:.0f} / {total / 1e6:.0f} MB)", flush=True)
                state["next"] = pct + every_pct
    urllib.request.urlretrieve(url, dest, hook)


# =============================================================================
# CELL 1: Install Piper's training code
# =============================================================================
def installed(module):
    return subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, cwd=WORK).returncode == 0


if SKIP_TRAINING and installed("piper.train.export_onnx"):
    print("Piper is already installed in this session: skipping the install/build steps.")
else:
    sh("apt-get install -y -qq build-essential cmake ninja-build")
    if not os.path.isdir(REPO):
        sh(f"git clone https://github.com/OHF-voice/piper1-gpl.git {REPO}")
    # Piper's setup.py imports skbuild, and build_monotonic_align.sh calls cythonize. pip only provides the
    # build requirements inside its own isolated build, so the later dev build needs them in this Python too.
    sh("python3 -m pip install -q scikit-build setuptools wheel cmake ninja cython")
    sh("python3 -m pip install -q -e '.[train]'", cwd=REPO, quiet=True)
    sh("./build_monotonic_align.sh", cwd=REPO, quiet=True)
    sh("python3 setup.py build_ext --inplace", cwd=REPO, quiet=True)      # compiles espeak-ng: a few minutes

# onnxscript is deliberately NOT installed here: it is only the fallback of the ONNX export (the launcher asks for
# the older exporter first), and installing it hung the cell. If CELL 4 reports "skipped ..." errors that mention
# onnxscript, run `pip install onnx onnxscript` in its own cell, then set SKIP_TRAINING = True and run this again.

# =============================================================================
# CELL 2: Find the dataset and download the base checkpoint
# =============================================================================
csvs = glob.glob("/kaggle/input/**/metadata.csv", recursive=True)
assert csvs, "metadata.csv not found under /kaggle/input -- did you add the dataset to the notebook?"
CSV_PATH = csvs[0]
AUDIO_DIR = os.path.join(os.path.dirname(CSV_PATH), "wavs")
print(f"Dataset: {CSV_PATH}  ({sum(1 for _ in open(CSV_PATH, encoding='utf-8'))} clips)")

BASE_CKPT = f"{WORK}/base.ckpt"
if CONTINUE_FROM:
    found_start = sorted(glob.glob(CONTINUE_FROM, recursive=True))
    assert found_start, f"CONTINUE_FROM matched no file: {CONTINUE_FROM}"
    # CELL 3 deletes lightning_logs/ before training, so a start checkpoint inside it is copied out first.
    BASE_CKPT = f"{WORK}/start.ckpt"
    shutil.copy(found_start[-1], BASE_CKPT)
    start_name = os.path.basename(found_start[-1])
    LEARNING_RATE, LEARNING_RATE_D, EXTRA_EPOCHS = CONTINUE_LR, CONTINUE_LR_D, CONTINUE_EPOCHS
else:
    tree = json.load(urllib.request.urlopen(f"{HF}/api/datasets/rhasspy/piper-checkpoints/tree/main/{BASE_VOICE}"))
    ckpts = [f["path"] for f in tree if f["path"].endswith(".ckpt")]
    assert ckpts, f"no checkpoint found for {BASE_VOICE}"
    start_name = ckpts[0].split("/")[-1]
    if not os.path.exists(BASE_CKPT):
        url = f"{HF}/datasets/rhasspy/piper-checkpoints/resolve/main/{urllib.parse.quote(ckpts[0])}"
        print(f"Downloading {url} (~850 MB)")
        download(url, BASE_CKPT)
# Warm start instead of resuming: `--ckpt_path` also re-reads the hyperparameters saved in the checkpoint, and
# the published (older-format) checkpoints contain keys the current Piper rejects ("model.sample_bytes").
# `--model.warmstart_ckpt` copies every weight whose name and shape match, starts a fresh optimizer, and the
# epoch counter at 0, so --trainer.max_epochs is simply the number of fine-tuning epochs.
BASE_EPOCH = 0
MAX_EPOCHS = EXTRA_EPOCHS
print(f"Fine-tuning for {MAX_EPOCHS} epochs from {start_name}.")

# =============================================================================
# CELL 3: Fine-tune
# =============================================================================
# Checkpoints land in lightning_logs/ next to where this runs: Piper keeps the 5 best by val_mel, the 5
# best by val_mos (a perceptual score), and last.ckpt.
# USE_MOS = False on purpose. In a real run val_mos was highest at epoch 1 (3.8, the untouched base voice) and fell to
# ~1.3 as the model adapted to the clips: UTMOS scores clean speech, and a faithful copy of noisy TV audio scores low.
# Its top-5 checkpoints would therefore be the EARLIEST, least adapted epochs, the opposite of what you want. It also
# cost CPU time per epoch (Piper leaves the model on the CPU; the launcher moves it to the GPU when USE_MOS is True).
# Without it the val_mel checkpoints, the last one and the snapshots are saved; you pick by ear.
# Failed earlier attempts leave checkpoints in lightning_logs/; they would be exported as if they were from this run.
if not SKIP_TRAINING:
  shutil.rmtree(f"{WORK}/lightning_logs", ignore_errors=True)
  archive(PREVIEW_DIR)
  train(
      f"SNAPSHOT_EVERY={SNAPSHOT_EVERY} PREVIEW_SCRIPT={PREVIEW_SCRIPT} PREVIEW_DIR={PREVIEW_DIR} "
      f"PREVIEW_CONFIG={CONFIG} PREVIEW_TEXT={shlex.quote(TEST_SENTENCES[0])} python3 {LAUNCHER} piper.train fit"
      f" --data.voice_name {VOICE_NAME}"
      f" --data.csv_path {CSV_PATH}"
      f" --data.audio_dir {AUDIO_DIR}"
      " --model.sample_rate 22050"
      f" --data.espeak_voice {ESPEAK_VOICE}"
      f" --data.cache_dir {CACHE}"
      f" --data.config_path {CONFIG}"
      f" --data.batch_size {BATCH_SIZE}"
      f" --data.validation_split {VALIDATION_SPLIT}"
      " --data.num_test_examples 3"            # also what val_mos is computed on: 0 would mean it is never logged
      " --data.num_workers 2"
      " --trainer.accelerator gpu --trainer.devices 1"
      " --trainer.enable_progress_bar false --trainer.log_every_n_steps 1"
      f" --trainer.max_epochs {MAX_EPOCHS}"
      f" --model.learning_rate {LEARNING_RATE} --model.learning_rate_d {LEARNING_RATE_D}"
      f" --model.mos_metric {'utmos' if USE_MOS else 'none'}"
      f" --model.warmstart_ckpt {BASE_CKPT}",
      BASE_EPOCH, MAX_EPOCHS,
  )

# =============================================================================
# CELL 4: Export every saved checkpoint to ONNX and render test sentences
# =============================================================================
if not SKIP_TRAINING:
    archive(OUT)
os.makedirs(OUT, exist_ok=True)
found = sorted(glob.glob(f"{WORK}/lightning_logs/**/*.ckpt", recursive=True))
print(f"{len(found)} checkpoint(s) to export")
sys.path.insert(0, REPO + "/src")
from piper import PiperVoice  # noqa: E402  (available once the editable install above has run)
import wave  # noqa: E402

for i, ckpt in enumerate(found):
    tag = re.sub(r"[^A-Za-z0-9=._-]", "_", os.path.basename(ckpt)[:-5])
    onnx = f"{OUT}/{VOICE_NAME}_{tag}.onnx"
    try:
        sh(f"python3 {LAUNCHER} piper.train.export_onnx --checkpoint '{ckpt}' --output-file '{onnx}'", cwd=REPO)
        shutil.copy(CONFIG, onnx + ".json")
        voice = PiperVoice.load(onnx)
        for n, text in enumerate(TEST_SENTENCES, 1):
            with wave.open(f"{OUT}/{VOICE_NAME}_{tag}_s{n}.wav", "wb") as w:
                voice.synthesize_wav(text, w)
    except Exception as e:  # one bad checkpoint shouldn't lose the others
        print(f"  skipped {ckpt}: {e}")

# =============================================================================
# CELL 5: Zip for download
# =============================================================================
zip_path = f"{WORK}/piper_voice_out.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(glob.glob(f"{OUT}/*")):
        z.write(p, os.path.basename(p))
print(f"\nDone -> {zip_path}  ({os.path.getsize(zip_path) / 1e6:.0f} MB)")

# The ONNX can't be trained further; the weights checkpoint can (CONTINUE_FROM). /kaggle/working is wiped when the
# session ends, so put the last snapshot where it is easy to download (with FileLink, like the .onnx) and keep it.
snaps = sorted(glob.glob(f"{WORK}/lightning_logs/snapshots/epoch=*-snapshot.ckpt"),
               key=lambda p: int(re.search(r"epoch=(\d+)", p).group(1)))
if snaps:
    os.makedirs(f"{WORK}/dl", exist_ok=True)
    resume = f"{WORK}/dl/resume_{os.path.basename(snaps[-1]).replace('=', '')}"
    shutil.copy(snaps[-1], resume)
    print(f"Weights to continue training later: {resume}  ({os.path.getsize(resume) / 1e6:.0f} MB) -- download it before the session ends.")
print("Listen to the *_s1.wav files: 'snapshot' files are every SNAPSHOT_EVERY epochs (compare early vs late), "
      "'val_mel' ones are the best by reconstruction accuracy, 'last' is the final epoch.")
