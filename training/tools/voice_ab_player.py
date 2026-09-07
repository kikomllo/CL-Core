"""
voice_ab_player.py -- Free-form A/B playback for TTS voice-tuning candidates.

Standalone review tool: plays any .mp3 in a directory on command, so voice/
rate/pitch candidates (e.g. assets/voice_tuning/*.mp3) can be A/B'd by ear in
whatever order and repetition the listener wants, rather than a fixed script.

Usage:
    python training/tools/voice_ab_player.py
    python training/tools/voice_ab_player.py --dir assets/voice_tuning
"""

import argparse
import glob
import os
import sys
import time

from pygame import mixer

DEFAULT_DIR = "assets/voice_tuning"


def load_samples(directory: str):
    return sorted(glob.glob(os.path.join(directory, "*.mp3")))


def play(path: str) -> None:
    mixer.music.load(path)
    mixer.music.play()
    while mixer.music.get_busy():
        time.sleep(0.05)
    mixer.music.unload()


def list_samples(files):
    print()
    for i, f in enumerate(files):
        print(f"  [{i}] {os.path.splitext(os.path.basename(f))[0]}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Free-form A/B player for TTS voice-tuning candidates.")
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"Directory of .mp3 samples (default {DEFAULT_DIR})")
    args = parser.parse_args()

    files = load_samples(args.dir)
    if not files:
        print(f"No .mp3 samples found in {args.dir}")
        sys.exit(1)

    mixer.init()
    print(f"{len(files)} samples found in {args.dir}:")
    list_samples(files)
    print("Commands: <number>=play  r=repeat last  l=relist  q=quit\n")

    idx = None
    try:
        while True:
            cmd = input("> ").strip().lower()
            if cmd == "q":
                break
            elif cmd == "l":
                list_samples(files)
            elif cmd == "r":
                if idx is None:
                    print("Nothing played yet.")
                else:
                    play(files[idx])
            elif cmd.isdigit() and 0 <= int(cmd) < len(files):
                idx = int(cmd)
                print(f"Playing [{idx}] {os.path.splitext(os.path.basename(files[idx]))[0]}")
                play(files[idx])
            else:
                print("Unknown command.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        mixer.quit()


if __name__ == "__main__":
    main()
