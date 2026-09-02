"""
Kaggle Training Script for the JARVIS Reply/Personality SLM
=============================================================

Second, separate model from kaggle_train.py's action-classification model.
This one only ever phrases a spoken reply from an already-decided action —
it never classifies intent and has no grammar constraint — so it uses a
much smaller base model (SmolLM2-360M-Instruct) for low latency/memory,
trained on data/reply_lora_dataset.jsonl (see tools/gen_reply_dataset.py).

SETUP INSTRUCTIONS (before running this notebook on Kaggle):
------------------------------------------------------------
1. Go to kaggle.com → "New Notebook"
2. On the right sidebar → Settings:
   - Accelerator: GPU T4 x2 (or GPU P100)
   - Internet: Enable (required for pip installs + model download)
   - Persistence: Files (keeps output between runs)
3. Add your dataset:
   - Upload reply_lora_dataset.jsonl as a new Kaggle dataset
     (e.g. "cl-core-reply-training-data")
   - In the notebook, click "+ Add data" and add your dataset
   - It will appear at: /kaggle/input/cl-core-reply-training-data/reply_lora_dataset.jsonl
4. Copy-paste this entire file into a Kaggle notebook code cell
   (or split at the marked section breaks into separate cells)
"""

# =============================================================================
# CELL 1: Install Dependencies
# =============================================================================
# !pip install unsloth trl peft accelerate bitsandbytes

# =============================================================================
# CELL 2: Configuration
# =============================================================================
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"  # unsloth does not support multi-GPU
import glob
import torch

IS_KAGGLE = os.path.exists("/kaggle")
IS_COLAB = os.path.exists("/content")

DATASET_FILENAME = "reply_lora_dataset.jsonl"

if IS_KAGGLE:
    matches = glob.glob(f"/kaggle/input/**/{DATASET_FILENAME}", recursive=True)
    DATASET_PATH = matches[0] if matches else f"/kaggle/input/{DATASET_FILENAME}"
    OUTPUT_DIR = "/kaggle/working/outputs"
    GGUF_OUTPUT_DIR = "/kaggle/working/jarvis-reply-v1"
elif IS_COLAB:
    DATASET_PATH = DATASET_FILENAME
    OUTPUT_DIR = "outputs"
    GGUF_OUTPUT_DIR = "jarvis-reply-v1"
else:
    DATASET_PATH = f"data/{DATASET_FILENAME}"
    OUTPUT_DIR = "outputs"
    GGUF_OUTPUT_DIR = "jarvis-reply-v1"

# Wipe stale output dirs from a previous run before training starts fresh.
import shutil
for _stale_dir in (OUTPUT_DIR, GGUF_OUTPUT_DIR):
    if os.path.exists(_stale_dir):
        print(f"[CLEANUP] Removing stale output directory from a previous run: {_stale_dir}")
        shutil.rmtree(_stale_dir, ignore_errors=True)

assert os.path.exists(DATASET_PATH), (
    f"Dataset not found at: {DATASET_PATH}\n"
    f"If on Kaggle, make sure you added your dataset via '+ Add data' in the notebook sidebar."
)

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

# Replies are short, single-sentence phrasings -- no need for the 768-token
# window the compound/correction classification prompts required.
max_seq_length = 256

import json


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


_clear_corrupted_model_cache("smollm2-360m-instruct")

# 1. Load base model — small on purpose, this model only phrases, never classifies.
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/SmolLM2-360M-Instruct",
    max_seq_length=max_seq_length,
    dtype=None,
    load_in_4bit=True,
)

# 2. Configure LoRA adapters
# Lower capacity than the action model -- this task is phrasing/voice, not
# multi-class discrimination across 50 intents.
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    lora_alpha=32,
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

split_dataset = dataset.train_test_split(test_size=0.1, seed=3407)
train_dataset = split_dataset["train"]
eval_dataset = split_dataset["test"]

print(f"Dataset loaded: {len(dataset)} samples ({len(train_dataset)} train / {len(eval_dataset)} eval)")
print(f"Sample preview:\n{train_dataset[0]['text'][:300]}...")

# =============================================================================
# CELL 5: Train
# =============================================================================
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
        per_device_train_batch_size=8,
        per_device_eval_batch_size=8,
        gradient_accumulation_steps=1,
        warmup_ratio=0.1,
        num_train_epochs=6,
        learning_rate=2e-4,
        fp16=use_fp16,
        bf16=use_bf16,
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir=OUTPUT_DIR,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
    ),
)

# CRITICAL: Calculate loss ONLY on assistant responses, not the action summary/user turn
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

try:
    model
except NameError:
    print("Model not in memory — reloading from merged safetensors...")
    from unsloth import FastLanguageModel
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=GGUF_OUTPUT_DIR,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=False,
    )

print(f"\nExporting GGUF to: {GGUF_OUTPUT_DIR}")
# q8_0 rather than q4_k_m -- the model is already tiny, so the extra bits are
# cheap and buy back quality on a task that's all about voice/phrasing.
model.save_pretrained_gguf(GGUF_OUTPUT_DIR, tokenizer, quantization_method="q8_0")

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
    print("WARNING: save_pretrained_gguf did not produce a .gguf file.")
    print("Attempting manual conversion with llama.cpp...")

    llama_cpp_dir = "/kaggle/working/llama.cpp"
    convert_script = os.path.join(llama_cpp_dir, "convert_hf_to_gguf.py")
    quantize_bin = os.path.join(llama_cpp_dir, "build", "bin", "llama-quantize")

    fp16_gguf = os.path.join(GGUF_OUTPUT_DIR, "model-fp16.gguf")
    q8_gguf = os.path.join(GGUF_OUTPUT_DIR, "jarvis-reply-v1-q8_0.gguf")

    subprocess.run(["python", convert_script, GGUF_OUTPUT_DIR, "--outfile", fp16_gguf], check=True)
    subprocess.run([quantize_bin, fp16_gguf, q8_gguf, "q8_0"], check=True)
    if os.path.exists(q8_gguf):
        os.remove(fp16_gguf)
        size_mb = os.path.getsize(q8_gguf) / 1e6
        print(f"\n{'='*60}")
        print(f"SUCCESS (manual conversion)! Download from 'Output' tab:")
        print(f"  → jarvis-reply-v1-q8_0.gguf ({size_mb:.1f} MB)")
        print(f"{'='*60}")
