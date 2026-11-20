"""Renders the same lines with several voice variants (model x effect level x Piper noise) through the real clVoice
chain and plays them on demand, so a voice is chosen by ear instead of by metric. Run with no training job and the
live JARVIS TTS idle (either starves audio output and cuts words).

    python training/tools/voice_audition.py render            # renders every variant -> outputs/voice_audition/<letter>/<n>.wav
    python training/tools/voice_audition.py play              # interactive: "B 2", "all 3", "A B C 1", "q"
    python training/tools/voice_audition.py tour [lines...]   # plays every variant for each line, A..F, with 1..6 soft beeps before each
    python training/tools/voice_audition.py show              # lists the variants

Edit VARIANTS / LINES below to try something else; each variant overrides settings.tts_settings from core.json."""
import json
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "src", "utils")]
ROUND = os.environ.get("VOICE_AUDITION_ROUND", "1")          # "2" = the speed / duration-noise round, "3" = e275 against the v2 fine-tune
OUT = os.path.join(ROOT, "outputs", "voice_audition" if ROUND == "1" else f"voice_audition_r{ROUND}")
CAND = "outputs/voice_candidates"

LINES = [
    "Good evening. All systems are online and running normally.",
    "The lights in the bedroom are now off. Anything else you need?",
    "You have two reminders today, and one calendar event at three in the afternoon.",
    "I am afraid I cannot do that. There is only one way this ends.",
]

# letter -> (what it is, overrides on top of settings.tts_settings)
_E275 = {"piper_model": f"{CAND}/ultron_e275.onnx", "fx_level": "light"}
VARIANTS_R2 = {      # round 2: the live voice (F of round 1) against slower speech and steadier phoneme durations
    "A": ("live now: e275, light, noise 0.5/0.6, length_scale 0.9", {**_E275, "noise_scale": 0.5, "noise_w_scale": 0.6}),
    "B": ("same, length_scale 1.0 (natural speed)", {**_E275, "noise_scale": 0.5, "noise_w_scale": 0.6, "length_scale": 1.0}),
    "C": ("length_scale 1.0, noise_w 0.3 (steadier durations, same liveliness)", {**_E275, "noise_scale": 0.5, "noise_w_scale": 0.3, "length_scale": 1.0}),
    "D": ("length_scale 1.1 (a little slower than natural), noise_w 0.3", {**_E275, "noise_scale": 0.5, "noise_w_scale": 0.3, "length_scale": 1.1}),
}
_V2 = {"fx_level": "light", "noise_scale": 0.5, "noise_w_scale": 0.6}
VARIANTS_R3 = {      # round 3: the live voice against the v2 fine-tune (denoised data, short clips, other speakers removed)
    "A": ("live now: e275, light, noise 0.5/0.6, length_scale 0.9", {**_E275, "noise_scale": 0.5, "noise_w_scale": 0.6}),
    "B": ("v2 epoch 125, same settings (length_scale 0.9)", {**_V2, "piper_model": "outputs/voice_v2_candidates/ultron_v2_e125.onnx"}),
    "C": ("v2 epoch 125, length_scale 1.1", {**_V2, "piper_model": "outputs/voice_v2_candidates/ultron_v2_e125.onnx", "length_scale": 1.1}),
    "D": ("v2 epoch 136 (best val loss late), length_scale 1.0", {**_V2, "piper_model": "outputs/voice_v2_candidates/ultron_v2_best136.onnx", "length_scale": 1.0}),
    "E": ("v2 epoch 75, length_scale 1.0", {**_V2, "piper_model": "outputs/voice_v2_candidates/ultron_v2_e075.onnx", "length_scale": 1.0}),
}
if ROUND == "3":
    LINES[:] = ["Good evening. All systems are online and running normally.", "Hello.", "Done.", "Right away. The lights in the bedroom are now off.",
                "I am afraid I cannot do that. There is only one way this ends."]
VARIANTS = VARIANTS_R3 if ROUND == "3" else VARIANTS_R2 if ROUND == "2" else {
    "A": ("live: actor_voice, full effects, default noise", {}),
    "B": ("ultron_e275, full effects", {"piper_model": f"{CAND}/ultron_e275.onnx"}),
    "C": ("ultron_e275, light effects (no vocoder/denoise)", {"piper_model": f"{CAND}/ultron_e275.onnx", "fx_level": "light"}),
    "D": ("ultron_e275, dry (no effects at all)", {"piper_model": f"{CAND}/ultron_e275.onnx", "fx_level": "off"}),
    "E": ("ultron_e275, light, steadier (noise 0.35/0.35: clearer, flatter)",
          {"piper_model": f"{CAND}/ultron_e275.onnx", "fx_level": "light", "noise_scale": 0.35, "noise_w_scale": 0.35}),
    "F": ("ultron_e275, light, in between (noise 0.5/0.6)",
          {"piper_model": f"{CAND}/ultron_e275.onnx", "fx_level": "light", "noise_scale": 0.5, "noise_w_scale": 0.6}),
}


def base_settings():
    with open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8") as f:
        return json.load(f).get("settings", {}).get("tts_settings", {})


def render():
    from utils.clVoice import VoiceSynth
    base = base_settings()
    for letter, (desc, over) in VARIANTS.items():
        folder = os.path.join(OUT, letter)
        os.makedirs(folder, exist_ok=True)
        cfg = {**base, **over}
        if "fx" in over:
            cfg["fx"] = {**base.get("fx", {}), **over["fx"]}
        synth = VoiceSynth(cfg)
        t0 = time.time()
        for n, text in enumerate(LINES, 1):
            audio, sr = synth.render(text)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            with wave.open(os.path.join(folder, f"{n}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm.tobytes())
        print(f"{letter}: {desc}  ({time.time() - t0:.1f}s)")
    print(f"\nRendered to {OUT}. Next: python training/tools/voice_audition.py play")


def show():
    for letter, (desc, _) in VARIANTS.items():
        print(f"  {letter}  {desc}")
    for n, text in enumerate(LINES, 1):
        print(f"  line {n}: {text}")


def tour(line_numbers=None):
    """Non-interactive: per line, every variant in order; N soft beeps (A=1 ... F=6) announce which voice comes next."""
    from pygame import mixer
    dev = json.load(open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8"))["settings"].get("audio_settings", {}).get("output_device")
    kw = dict(frequency=22050, size=-16, channels=1, buffer=1024)
    if dev and dev != "System Default":
        kw["devicename"] = dev
    mixer.init(**kw)
    t = np.arange(int(22050 * 0.09)) / 22050
    beep = mixer.Sound(buffer=(np.sin(2 * np.pi * 660 * t) * 0.06 * 32767).astype(np.int16).tobytes())   # low volume on purpose
    time.sleep(1.0)
    for line in line_numbers or range(1, len(LINES) + 1):
        print(f"line {line}: {LINES[line - 1]}", flush=True)
        for i, letter in enumerate(VARIANTS, 1):
            for _ in range(i):
                beep.play()
                time.sleep(0.28)
            time.sleep(0.5)
            print(f"  {letter}", flush=True)
            sound = mixer.Sound(os.path.join(OUT, letter, f"{line}.wav"))
            sound.play()
            time.sleep(sound.get_length() + 0.8)
        time.sleep(1.5)


def play():
    from pygame import mixer
    dev = json.load(open(os.path.join(ROOT, "config", "core.json"), encoding="utf-8"))["settings"].get("audio_settings", {}).get("output_device")
    kw = dict(frequency=22050, size=-16, channels=1, buffer=1024)
    if dev and dev != "System Default":
        kw["devicename"] = dev
    mixer.init(**kw)
    show()
    print('\nType e.g. "B 2" (voice B, line 2), "A B C 1" (compare on line 1), "all 3", "q" to quit.')
    while True:
        try:
            cmd = input("> ").strip().upper().split()
        except (EOFError, KeyboardInterrupt):
            return
        if not cmd or cmd[0] == "Q":
            return
        line = next((int(c) for c in cmd if c.isdigit()), 1)
        letters = [c for c in cmd if c in VARIANTS] or (list(VARIANTS) if "ALL" in cmd else [])
        for letter in letters:
            path = os.path.join(OUT, letter, f"{line}.wav")
            if not os.path.exists(path):
                print(f"  {letter}/{line}.wav is missing, run render first")
                continue
            print(f"  {letter} line {line}")
            sound = mixer.Sound(path)
            sound.play()
            time.sleep(sound.get_length() + 0.7)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "tour":
        tour([int(a) for a in sys.argv[2:]] or None)
    else:
        {"render": render, "play": play, "show": show}.get(cmd, show)()
