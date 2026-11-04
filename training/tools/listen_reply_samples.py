"""
listen_reply_samples.py — Listen to reply-dataset samples via TTS.

Standalone review tool: speaks samples from data/reply_lora_dataset.jsonl
aloud using the exact same Piper voice and effects as the live system
(src/utils/clVoice.py, settings.tts_settings in config/core.json), so the
personality can be judged by ear rather than just read as text.

Usage:
    python tools/listen_reply_samples.py
    python tools/listen_reply_samples.py --count 10 --category action
    python tools/listen_reply_samples.py --category noise
"""

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile

from pygame import mixer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
from utils.clVoice import VoiceSynth, merge_settings

DATASET_PATH = "training/data/reply_lora_dataset.jsonl"


def load_samples(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def filter_samples(samples, category: str):
    if category == "action":
        return [s for s in samples if not s["messages"][0]["content"].startswith("[ACTION] none")]
    if category == "noise":
        return [s for s in samples if s["messages"][0]["content"].startswith("[ACTION] none")]
    return samples


async def speak(text: str, temp_path: str, synth: VoiceSynth) -> None:
    await asyncio.to_thread(synth.synthesize_to_file, text, temp_path)
    mixer.music.load(temp_path)
    mixer.music.play()
    while mixer.music.get_busy():
        await asyncio.sleep(0.05)
    mixer.music.unload()


async def review(samples, count: int, synth: VoiceSynth) -> None:
    random.shuffle(samples)
    samples = samples[:count]

    mixer.init()
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_path = os.path.join(tmp_dir, "preview.wav")
        i = 0
        while i < len(samples):
            sample = samples[i]
            action_line, user_text, reply = (m["content"] for m in sample["messages"])

            print(f"\n[{i + 1}/{len(samples)}] {action_line}")
            print(f'  User:   "{user_text}"')
            print(f'  JARVIS: "{reply}"')

            try:
                await speak(reply, temp_path, synth)
            except Exception as e:
                print(f"  (TTS error, skipping: {e})")

            choice = input("  [Enter]=next  r=replay  q=quit > ").strip().lower()
            if choice == "q":
                break
            elif choice != "r":
                i += 1


def main():
    parser = argparse.ArgumentParser(description="Listen to reply-dataset samples to judge the JARVIS personality by ear.")
    parser.add_argument("--count", type=int, default=20, help="How many samples to review (default 20)")
    parser.add_argument("--category", choices=["all", "action", "noise"], default="all",
                         help="all = everything, action = only action-grounded replies, noise = only noise/OOS rejections")
    parser.add_argument("--dataset", default=DATASET_PATH, help="Path to the reply dataset JSONL")
    parser.add_argument("--no-fx", action="store_true", help="Play the dry Piper voice without the effect chain")
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"Dataset not found: {args.dataset}. Run tools/gen_reply_dataset.py first.")
        sys.exit(1)

    samples = filter_samples(load_samples(args.dataset), args.category)
    if not samples:
        print(f"No samples matched category={args.category!r}.")
        sys.exit(1)

    n = min(args.count, len(samples))
    print(f"{len(samples)} samples available (category={args.category}). Reviewing {n}.\n")

    try:
        with open(os.path.join("config", "core.json"), encoding="utf-8") as f:
            settings = merge_settings(json.load(f).get("settings", {}).get("tts_settings"))
        if args.no_fx:
            settings["fx"]["enabled"] = False
        asyncio.run(review(samples, args.count, VoiceSynth(settings)))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
