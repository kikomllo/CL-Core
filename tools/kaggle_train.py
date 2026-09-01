"""
Kaggle Training Script for CL-Core Jarvis LoRA Fine-Tuning
===========================================================

SETUP INSTRUCTIONS (before running this notebook on Kaggle):
------------------------------------------------------------
1. Go to kaggle.com → "New Notebook"
2. On the right sidebar → Settings:
   - Accelerator: GPU T4 x2 (or GPU P100)
   - Internet: Enable (required for pip installs + model download)
   - Persistence: Files (keeps output between runs)
3. Add your dataset:
   - Upload synthetic_lora_dataset.jsonl as a new Kaggle dataset
     (e.g. "cl-core-training-data")
   - In the notebook, click "+ Add data" and add your dataset
   - It will appear at: /kaggle/input/cl-core-training-data/synthetic_lora_dataset.jsonl
4. Copy-paste this entire file into a Kaggle notebook code cell
   (or split at the marked section breaks into separate cells)

DIFFERENCES FROM COLAB:
- Dataset path: /kaggle/input/<dataset-name>/ (read-only)
- Output path:  /kaggle/working/ (writable, downloadable)
- GPU: T4 (16GB) or P100 (16GB) — same as Colab free tier
- Kaggle allows bf16 on T4; Colab free T4 does not reliably
- 12h session limit (vs Colab's ~4h GPU quota)
"""

# =============================================================================
# CELL 1: Install Dependencies
# =============================================================================
# Uncomment the block that matches your platform.
#
# --- KAGGLE (fast, ~2 min) ---
# !pip install unsloth trl peft accelerate bitsandbytes
#
# --- COLAB (needs the xformers pin) ---
# %%capture
# !pip install unsloth
# !pip install --force-reinstall "xformers<0.0.27"
# !pip install trl peft accelerate bitsandbytes
#
# WHY the difference:
# Kaggle ships pre-installed torch + xformers that are already compatible.
# The --force-reinstall xformers line reinstalls ALL of PyTorch (~2GB) as a
# side effect, adding 10-15 min of pointless download + install time.
# On Colab, the pre-installed xformers version can conflict, so the pin
# is necessary there but NOT on Kaggle.

# =============================================================================
# CELL 2: Configuration
# =============================================================================
import os
# Force single GPU — unsloth does NOT support multi-GPU (T4 x2 will crash)
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
import glob
import torch

# Auto-detect environment
IS_KAGGLE = os.path.exists("/kaggle")
IS_COLAB = os.path.exists("/content")

DATASET_FILENAME = "synthetic_lora_dataset.jsonl"

if IS_KAGGLE:
    # Kaggle mounts at: /kaggle/input/datasets/<username>/<dataset-name>/
    # Auto-discover the dataset instead of hardcoding the path
    matches = glob.glob(f"/kaggle/input/**/{DATASET_FILENAME}", recursive=True)
    DATASET_PATH = matches[0] if matches else f"/kaggle/input/{DATASET_FILENAME}"
    OUTPUT_DIR = "/kaggle/working/outputs"
    GGUF_OUTPUT_DIR = "/kaggle/working/jarvis-brain-v2"
elif IS_COLAB:
    DATASET_PATH = DATASET_FILENAME
    OUTPUT_DIR = "outputs"
    GGUF_OUTPUT_DIR = "jarvis-brain-v2"
else:
    DATASET_PATH = f"data/{DATASET_FILENAME}"
    OUTPUT_DIR = "outputs"
    GGUF_OUTPUT_DIR = "jarvis-brain-v2"

# Wipe any stale output from a previous run. Kaggle's "Persistence: Files"
# setting (recommended in this file's setup instructions) keeps OUTPUT_DIR
# and GGUF_OUTPUT_DIR on disk *across separate notebook runs*. Cell 7's
# model.save_pretrained_gguf() does not clean GGUF_OUTPUT_DIR before writing
# into it, so a rerun can leave a mix of old and new tokenizer/vocab/config
# files sitting alongside newly merged weights — a mismatched tokenizer and
# model produces exactly the kind of garbled, foreign-character, malformed-
# JSON output seen across repeated benchmark runs here, and it compounds
# with every additional run that writes into the same directory without
# clearing it first. Every run now starts from a guaranteed-clean directory.
import shutil
for _stale_dir in (OUTPUT_DIR, GGUF_OUTPUT_DIR):
    if os.path.exists(_stale_dir):
        print(f"[CLEANUP] Removing stale output directory from a previous run: {_stale_dir}")
        shutil.rmtree(_stale_dir, ignore_errors=True)

# Verify dataset exists before burning GPU time
assert os.path.exists(DATASET_PATH), (
    f"Dataset not found at: {DATASET_PATH}\n"
    f"If on Kaggle, make sure you added your dataset via '+ Add data' in the notebook sidebar."
)

# GPU diagnostics
print(f"Environment: {'Kaggle' if IS_KAGGLE else 'Colab' if IS_COLAB else 'Local'}")
print(f"Dataset: {DATASET_PATH}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    gpu_name = torch.cuda.get_device_name(0)
    gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {gpu_name} ({gpu_mem:.1f} GB)")
    print(f"bf16 support: {torch.cuda.is_bf16_supported()}")
else:
    print("WARNING: No GPU detected! Training will be extremely slow.")

# =============================================================================
# CELL 3: Load Model + LoRA
# =============================================================================
from unsloth import FastLanguageModel, is_bfloat16_supported
from unsloth.chat_templates import get_chat_template, train_on_responses_only
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

max_seq_length = 768  # Sized for compound/correction prompts

# Guard against a corrupted HF cache entry: if a Kaggle session got killed or
# hit a network hiccup mid-download, config.json (or any other cached file)
# for the auto-resolved 4-bit repo can be left truncated/empty, which raises
# "not a valid JSON file" on every subsequent run until the cache is cleared
# (Kaggle's "Persistence: Files" setting keeps this across restarts). Scan
# known cache roots and nuke any snapshot dir for this model whose config.json
# fails to parse, so from_pretrained() below re-downloads it cleanly.
import glob
import json
import shutil


def _clear_corrupted_model_cache(repo_substring: str) -> None:
    cache_roots = [
        "huggingface_tokenizers_cache",
        os.path.expanduser("~/.cache/huggingface/hub"),
        os.environ.get("HF_HOME", ""),
        os.environ.get("TRANSFORMERS_CACHE", ""),
    ]
    for root in filter(None, cache_roots):
        pattern = os.path.join(root, f"models--*{repo_substring}*")
        for model_dir in glob.glob(pattern):
            for config_path in glob.glob(os.path.join(model_dir, "**", "config.json"), recursive=True):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        json.load(f)
                except (json.JSONDecodeError, OSError):
                    print(f"[CACHE] Found corrupted cache entry, clearing: {model_dir}")
                    shutil.rmtree(model_dir, ignore_errors=True)
                    break


_clear_corrupted_model_cache("qwen2.5-0.5b-instruct")

# 1. Load base model
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen2.5-0.5B-Instruct",
    max_seq_length=max_seq_length,
    dtype=None,        # Auto-detect (float16 on T4/P100)
    load_in_4bit=True,
)

# 2. Configure LoRA adapters
# r/alpha raised from 16/32 — benchmark_slm.py runs showed genuine
# misclassifications (not just grammar hallucination) between close
# intents (light on/off, spotify play vs status_queue), which pointed
# at undercapacity more than undertraining.
model = FastLanguageModel.get_peft_model(
    model,
    r=32,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    lora_alpha=64,
    lora_dropout=0.0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# =============================================================================
# CELL 4: Prepare Dataset
# =============================================================================
tokenizer = get_chat_template(tokenizer, chat_template="chatml")


def formatting_prompts_func(examples):
    texts = [
        tokenizer.apply_chat_template(msg, tokenize=False, add_generation_prompt=False)
        for msg in examples["messages"]
    ]
    return {"text": texts}


dataset = load_dataset("json", data_files=DATASET_PATH, split="train")
dataset = dataset.map(formatting_prompts_func, batched=True)

# Held-out eval split. Without this, only training loss is visible during
# fine-tuning — which trends toward zero regardless of whether the model is
# still learning generalizable structure or has started memorizing surface
# noise (exact prefix wording, template phrasing). A training run that hit
# train_loss=0.027 by epoch 7 with no eval signal shipped an overfit
# checkpoint that produced garbled text on the benchmark's non-templated
# phrasing. eval_loss below is what actually tells you when to stop.
split_dataset = dataset.train_test_split(test_size=0.08, seed=3407)
train_dataset = split_dataset["train"]
eval_dataset = split_dataset["test"]

print(f"Dataset loaded: {len(dataset)} samples ({len(train_dataset)} train / {len(eval_dataset)} eval)")
print(f"Sample preview:\n{train_dataset[0]['text'][:300]}...")

# =============================================================================
# CELL 5: Train
# =============================================================================

# Detect bf16 capability — Kaggle T4 supports it, Colab T4 may not
use_bf16 = is_bfloat16_supported()
use_fp16 = not use_bf16

print(f"Training precision: {'bf16' if use_bf16 else 'fp16'}")

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=2,
        warmup_ratio=0.1,
        # Back to 7 — the proven-good run used this. eval_strategy below is
        # for passive visibility only now (see note).
        num_train_epochs=7,
        learning_rate=2e-4,
        fp16=use_fp16,
        bf16=use_bf16,
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir=OUTPUT_DIR,
        # Eval once per epoch purely so eval_loss is visible in the log
        # (compare it against Training Loss to eyeball overfitting) — it does
        # NOT change which weights get exported. load_best_model_at_end was
        # tried here across the last two runs (first with eval_steps=50, then
        # eval_strategy="epoch") and both came out *more* broken than the
        # run before it introduced this mechanism at all (garbled text,
        # eventually outright multilingual token garbage and JSON that
        # wouldn't close). That's consistent with Unsloth's model wrapping
        # not round-tripping cleanly through Trainer's standard checkpoint
        # load_state_dict restore for a 4-bit+LoRA setup. Removed rather than
        # tuned further — just take the final epoch's model, like the run
        # that actually worked (28/30) did.
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
    ),
)

# CRITICAL: Calculate loss ONLY on assistant responses
trainer = train_on_responses_only(
    trainer,
    instruction_part="<|im_start|>user\n",
    response_part="<|im_start|>assistant\n",
)

print("Starting Fine-Tuning with Response Masking...")
trainer.train()

# =============================================================================
# CELL 6: Build llama.cpp (required for GGUF export)
# =============================================================================
# !pip install llama-cpp-python
# !git clone https://github.com/ggerganov/llama.cpp /kaggle/working/llama.cpp
# !cd /kaggle/working/llama.cpp && cmake -B build && cmake --build build --target llama-quantize -j$(nproc)

# =============================================================================
# CELL 7: Export to GGUF
# =============================================================================
import subprocess

# If re-running just this cell after a restart, reload the model from checkpoint
try:
    model
except NameError:
    print("Model not in memory — reloading from merged safetensors...")
    from unsloth import FastLanguageModel
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=GGUF_OUTPUT_DIR,  # Load the already-merged safetensors
        max_seq_length=768,
        dtype=None,
        load_in_4bit=False,          # Need full precision for GGUF conversion
    )

print(f"\nExporting GGUF to: {GGUF_OUTPUT_DIR}")
model.save_pretrained_gguf(GGUF_OUTPUT_DIR, tokenizer, quantization_method="q4_k_m")

# Verify the .gguf file was actually created
gguf_files = [f for f in os.listdir(GGUF_OUTPUT_DIR) if f.endswith(".gguf")]
if gguf_files:
    print(f"\n{'='*60}")
    print(f"SUCCESS! Download from the 'Output' tab:")
    for f in gguf_files:
        fpath = os.path.join(GGUF_OUTPUT_DIR, f)
        size_mb = os.path.getsize(fpath) / 1e6
        print(f"  → {f} ({size_mb:.1f} MB)")
    print(f"{'='*60}")
else:
    # Fallback: manual quantization with llama.cpp
    print("WARNING: save_pretrained_gguf did not produce a .gguf file.")
    print("Attempting manual conversion with llama.cpp...")

    llama_cpp_dir = "/kaggle/working/llama.cpp"
    convert_script = os.path.join(llama_cpp_dir, "convert_hf_to_gguf.py")
    quantize_bin = os.path.join(llama_cpp_dir, "build", "bin", "llama-quantize")

    fp16_gguf = os.path.join(GGUF_OUTPUT_DIR, "model-fp16.gguf")
    q4km_gguf = os.path.join(GGUF_OUTPUT_DIR, "jarvis-brain-v2-q4_k_m.gguf")

    # Step 1: Convert safetensors → fp16 GGUF
    subprocess.run(["python", convert_script, GGUF_OUTPUT_DIR, "--outfile", fp16_gguf], check=True)
    # Step 2: Quantize fp16 → Q4_K_M
    subprocess.run([quantize_bin, fp16_gguf, q4km_gguf, "q4_k_m"], check=True)
    # Clean up the large fp16 intermediate
    if os.path.exists(q4km_gguf):
        os.remove(fp16_gguf)
        size_mb = os.path.getsize(q4km_gguf) / 1e6
        print(f"\n{'='*60}")
        print(f"SUCCESS (manual conversion)! Download from 'Output' tab:")
        print(f"  → jarvis-brain-v2-q4_k_m.gguf ({size_mb:.1f} MB)")
        print(f"{'='*60}")

