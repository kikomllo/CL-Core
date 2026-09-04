"""
Kaggle Training Script — Action Model + Reply Model, in Sequence
==================================================================

Combines kaggle_train.py (action-classification model) and
kaggle_reply_train.py (reply/personality model) into one notebook run.
Whenever config/intents.json changes, both tools/gen_dataset.py and
tools/gen_reply_dataset.py need regenerating, and both models need
retraining to stay in sync with the new grammar/phrasing -- this runs
that as one pass instead of two separate notebook sessions.

SETUP INSTRUCTIONS (before running this notebook on Kaggle):
------------------------------------------------------------
1. Go to kaggle.com → "New Notebook"
2. On the right sidebar → Settings:
   - Accelerator: GPU T4 x2 (or GPU P100)
   - Internet: Enable (required for pip installs + model download)
   - Persistence: Files (keeps output between runs)
3. Add BOTH datasets:
   - Upload synthetic_lora_dataset.jsonl as one Kaggle dataset
   - Upload reply_lora_dataset.jsonl as another Kaggle dataset
     (or both files in the same dataset -- either way, both are
     auto-discovered by filename below)
   - In the notebook, click "+ Add data" and add both
4. Copy-paste this entire file into a Kaggle notebook code cell
   (or split at the marked section breaks into separate cells)

Output: two GGUF files under /kaggle/working/ --
  jarvis-brain-v2/jarvis-brain-v2-q4_k_m.gguf   (action model)
  jarvis-reply-v1/jarvis-reply-v1-q8_0.gguf     (reply model)
"""

# =============================================================================
# CELL 1: Install Dependencies
# =============================================================================
# !pip install unsloth trl peft accelerate bitsandbytes

# =============================================================================
# CELL 2: Shared Configuration
# =============================================================================
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"  # unsloth does not support multi-GPU
import gc
import glob
import json
import shutil
import subprocess
import torch

IS_KAGGLE = os.path.exists("/kaggle")
IS_COLAB = os.path.exists("/content")


def find_dataset(filename: str, local_subdir: str = "training/data") -> str:
    if IS_KAGGLE:
        matches = glob.glob(f"/kaggle/input/**/{filename}", recursive=True)
        return matches[0] if matches else f"/kaggle/input/{filename}"
    if IS_COLAB:
        return filename
    return f"{local_subdir}/{filename}"


ACTION_DATASET_PATH = find_dataset("synthetic_lora_dataset.jsonl")
REPLY_DATASET_PATH = find_dataset("reply_lora_dataset.jsonl")

WORKING_DIR = "/kaggle/working" if IS_KAGGLE else "." if IS_COLAB else "training"
ACTION_OUTPUT_DIR = f"{WORKING_DIR}/outputs_action"
ACTION_GGUF_DIR = f"{WORKING_DIR}/jarvis-brain-v2"
REPLY_OUTPUT_DIR = f"{WORKING_DIR}/outputs_reply"
REPLY_GGUF_DIR = f"{WORKING_DIR}/jarvis-reply-v1"
LLAMA_CPP_DIR = f"{WORKING_DIR}/llama.cpp"

# Wipe stale output dirs from a previous run before training starts fresh.
for _stale_dir in (ACTION_OUTPUT_DIR, ACTION_GGUF_DIR, REPLY_OUTPUT_DIR, REPLY_GGUF_DIR):
    if os.path.exists(_stale_dir):
        print(f"[CLEANUP] Removing stale output directory from a previous run: {_stale_dir}")
        shutil.rmtree(_stale_dir, ignore_errors=True)

assert os.path.exists(ACTION_DATASET_PATH), (
    f"Action dataset not found at: {ACTION_DATASET_PATH}\n"
    f"Add synthetic_lora_dataset.jsonl via '+ Add data' in the notebook sidebar."
)
assert os.path.exists(REPLY_DATASET_PATH), (
    f"Reply dataset not found at: {REPLY_DATASET_PATH}\n"
    f"Add reply_lora_dataset.jsonl via '+ Add data' in the notebook sidebar."
)

print(f"Environment: {'Kaggle' if IS_KAGGLE else 'Colab' if IS_COLAB else 'Local'}")
print(f"Action dataset: {ACTION_DATASET_PATH}")
print(f"Reply dataset:  {REPLY_DATASET_PATH}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    gpu_name = torch.cuda.get_device_name(0)
    gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {gpu_name} ({gpu_mem:.1f} GB)")
    print(f"bf16 support: {torch.cuda.is_bf16_supported()}")
else:
    print("WARNING: No GPU detected! Training will be extremely slow.")


def clear_corrupted_model_cache(repo_substring: str) -> None:
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


def free_gpu_memory(*objects) -> None:
    """Drops references to a finished model/tokenizer/trainer and clears CUDA
    cache before the next model loads -- both models share one GPU session."""
    for obj in objects:
        del obj
    gc.collect()
    torch.cuda.empty_cache()


def export_to_gguf(model, tokenizer, output_dir: str, final_gguf_name: str, quant_method: str) -> None:
    print(f"\nExporting GGUF to: {output_dir}")
    model.save_pretrained_gguf(output_dir, tokenizer, quantization_method=quant_method)

    # Unsloth's own conversion writes the .gguf into a sibling "<output_dir>_gguf"
    # directory, not output_dir itself -- check both, preferring the sibling.
    for search_dir in (f"{output_dir}_gguf", output_dir):
        if not os.path.isdir(search_dir):
            continue
        gguf_files = [f for f in os.listdir(search_dir) if f.endswith(".gguf")]
        if not gguf_files:
            continue
        os.makedirs(output_dir, exist_ok=True)
        final_gguf = os.path.join(output_dir, final_gguf_name)
        shutil.move(os.path.join(search_dir, gguf_files[0]), final_gguf)
        if search_dir != output_dir:
            shutil.rmtree(search_dir, ignore_errors=True)
        size_mb = os.path.getsize(final_gguf) / 1e6
        print(f"\n{'='*60}\nSUCCESS! GGUF ready: {final_gguf} ({size_mb:.1f} MB)\n{'='*60}")
        return

    print("WARNING: save_pretrained_gguf did not produce a .gguf file. Attempting manual conversion...")
    convert_script = os.path.join(LLAMA_CPP_DIR, "convert_hf_to_gguf.py")
    quantize_bin = os.path.join(LLAMA_CPP_DIR, "build", "bin", "llama-quantize")
    fp16_gguf = os.path.join(output_dir, "model-fp16.gguf")
    final_gguf = os.path.join(output_dir, final_gguf_name)

    subprocess.run(["python", convert_script, output_dir, "--outfile", fp16_gguf], check=True)
    subprocess.run([quantize_bin, fp16_gguf, final_gguf, quant_method], check=True)
    if os.path.exists(final_gguf):
        os.remove(fp16_gguf)
        size_mb = os.path.getsize(final_gguf) / 1e6
        print(f"\n{'='*60}\nSUCCESS (manual conversion)! Download from 'Output' tab:\n  -> {final_gguf_name} ({size_mb:.1f} MB)\n{'='*60}")


# =============================================================================
# CELL 3: Build llama.cpp (required for GGUF export, shared by both models)
# =============================================================================
# !pip install llama-cpp-python
# !git clone https://github.com/ggerganov/llama.cpp {LLAMA_CPP_DIR}
# !cd {LLAMA_CPP_DIR} && cmake -B build && cmake --build build --target llama-quantize -j$(nproc)


# =============================================================================
# PHASE 1: ACTION MODEL (Qwen2.5-0.5B-Instruct, grammar-constrained)
# =============================================================================

# =============================================================================
# CELL 4: Load Action Model + LoRA
# =============================================================================
from unsloth import FastLanguageModel, is_bfloat16_supported
from unsloth.chat_templates import get_chat_template, train_on_responses_only
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

ACTION_MAX_SEQ_LENGTH = 768  # Sized for compound/correction prompts

clear_corrupted_model_cache("qwen2.5-0.5b-instruct")

action_model, action_tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen2.5-0.5B-Instruct",
    max_seq_length=ACTION_MAX_SEQ_LENGTH,
    dtype=None,
    load_in_4bit=True,
)

# LoRA adapter capacity for intent classification.
action_model = FastLanguageModel.get_peft_model(
    action_model,
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
# CELL 5: Prepare Action Dataset
# =============================================================================
action_tokenizer = get_chat_template(action_tokenizer, chat_template="chatml")


def format_action_prompts(examples):
    texts = [
        action_tokenizer.apply_chat_template(msg, tokenize=False, add_generation_prompt=False)
        for msg in examples["messages"]
    ]
    return {"text": texts}


action_dataset = load_dataset("json", data_files=ACTION_DATASET_PATH, split="train")
action_dataset = action_dataset.map(format_action_prompts, batched=True)

action_split = action_dataset.train_test_split(test_size=0.08, seed=3407)
action_train, action_eval = action_split["train"], action_split["test"]

print(f"Action dataset loaded: {len(action_dataset)} samples ({len(action_train)} train / {len(action_eval)} eval)")
print(f"Sample preview:\n{action_train[0]['text'][:300]}...")

# =============================================================================
# CELL 6: Train Action Model
# =============================================================================
use_bf16 = is_bfloat16_supported()
print(f"Training precision: {'bf16' if use_bf16 else 'fp16'}")

action_trainer = SFTTrainer(
    model=action_model,
    tokenizer=action_tokenizer,
    train_dataset=action_train,
    eval_dataset=action_eval,
    dataset_text_field="text",
    max_seq_length=ACTION_MAX_SEQ_LENGTH,
    dataset_num_proc=2,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=2,
        warmup_ratio=0.1,
        num_train_epochs=7,
        learning_rate=2e-4,
        fp16=not use_bf16,
        bf16=use_bf16,
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir=ACTION_OUTPUT_DIR,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
    ),
)

# CRITICAL: Calculate loss ONLY on assistant responses
action_trainer = train_on_responses_only(
    action_trainer,
    instruction_part="<|im_start|>user\n",
    response_part="<|im_start|>assistant\n",
)

print("Starting action-model fine-tuning...")
action_trainer.train()

# =============================================================================
# CELL 7: Export Action Model to GGUF
# =============================================================================
export_to_gguf(action_model, action_tokenizer, ACTION_GGUF_DIR, "jarvis-brain-v2-q4_k_m.gguf", "q4_k_m")

# Free the action model from GPU memory before the reply model loads.
free_gpu_memory(action_model, action_tokenizer, action_trainer)


# =============================================================================
# PHASE 2: REPLY MODEL (SmolLM2-360M-Instruct, no grammar)
# =============================================================================

# =============================================================================
# CELL 8: Load Reply Model + LoRA
# =============================================================================
REPLY_MAX_SEQ_LENGTH = 256  # Replies are short, single-sentence phrasings

clear_corrupted_model_cache("smollm2-360m-instruct")

reply_model, reply_tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/SmolLM2-360M-Instruct",
    max_seq_length=REPLY_MAX_SEQ_LENGTH,
    dtype=None,
    load_in_4bit=True,
)

# Lower capacity than the action model -- phrasing/voice, not 50-way classification.
reply_model = FastLanguageModel.get_peft_model(
    reply_model,
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
# CELL 9: Prepare Reply Dataset
# =============================================================================
reply_tokenizer = get_chat_template(reply_tokenizer, chat_template="chatml")


def format_reply_prompts(examples):
    texts = [
        reply_tokenizer.apply_chat_template(msg, tokenize=False, add_generation_prompt=False)
        for msg in examples["messages"]
    ]
    return {"text": texts}


reply_dataset = load_dataset("json", data_files=REPLY_DATASET_PATH, split="train")
reply_dataset = reply_dataset.map(format_reply_prompts, batched=True)

reply_split = reply_dataset.train_test_split(test_size=0.1, seed=3407)
reply_train, reply_eval = reply_split["train"], reply_split["test"]

print(f"Reply dataset loaded: {len(reply_dataset)} samples ({len(reply_train)} train / {len(reply_eval)} eval)")
print(f"Sample preview:\n{reply_train[0]['text'][:300]}...")

# =============================================================================
# CELL 10: Train Reply Model
# =============================================================================
reply_trainer = SFTTrainer(
    model=reply_model,
    tokenizer=reply_tokenizer,
    train_dataset=reply_train,
    eval_dataset=reply_eval,
    dataset_text_field="text",
    max_seq_length=REPLY_MAX_SEQ_LENGTH,
    dataset_num_proc=2,
    args=TrainingArguments(
        per_device_train_batch_size=8,
        per_device_eval_batch_size=8,
        gradient_accumulation_steps=1,
        warmup_ratio=0.1,
        num_train_epochs=6,
        learning_rate=2e-4,
        fp16=not use_bf16,
        bf16=use_bf16,
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir=REPLY_OUTPUT_DIR,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
    ),
)

# CRITICAL: Calculate loss ONLY on assistant responses, not the action summary/user turn
reply_trainer = train_on_responses_only(
    reply_trainer,
    instruction_part="<|im_start|>user\n",
    response_part="<|im_start|>assistant\n",
)

print("Starting reply-model fine-tuning...")
reply_trainer.train()

# =============================================================================
# CELL 11: Export Reply Model to GGUF
# =============================================================================
# q8_0 rather than q4_k_m -- the model is already tiny, so the extra bits are
# cheap and buy back quality on a task that's all about voice/phrasing.
export_to_gguf(reply_model, reply_tokenizer, REPLY_GGUF_DIR, "jarvis-reply-v1-q8_0.gguf", "q8_0")

free_gpu_memory(reply_model, reply_tokenizer, reply_trainer)

# =============================================================================
# CELL 12: Final Summary
# =============================================================================
print(f"\n{'='*60}")
print("BOTH MODELS TRAINED")
print(f"  Action model: {ACTION_GGUF_DIR}")
print(f"  Reply model:  {REPLY_GGUF_DIR}")
print("Download both from the 'Output' tab, then update config/core.json's")
print("slm_settings.model_path and reply_slm_settings.model_path accordingly.")
print(f"{'='*60}")
