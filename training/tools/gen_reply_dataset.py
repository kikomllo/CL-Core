"""
gen_reply_dataset.py — Training Dataset Generator for the JARVIS Reply/Personality SLM.

This is a second, separate model from tools/gen_dataset.py's classification model.
Where that dataset optimizes for classification correctness and class balance, this
one optimizes for phrasing VARIETY in a consistent voice (JARVIS, from Iron Man) --
the opposite priority. Collapsing onto one fixed phrase per action was exactly the
failure mode that made the old combined model's replies feel robotic, so every
action here has multiple hand-written variants, and follow-up phrasing ("Anything
else sir?") is drawn from its own varied pool rather than hardcoded.

Output format: ChatML JSONL (system/user/assistant), assistant content is plain
text (not JSON) -- this model has no grammar constraint, it only ever phrases.
"""

import json
import os
import random
import re
from typing import Any, Dict, List

from gen_dataset import (
    ARTISTS,
    COLORS,
    LIGHT_TARGETS,
    LUM_VALUES,
    MODULES,
    OUT_OF_SCOPE,
    NOISE_SAMPLES,
    PLAYLISTS,
    TASKS,
    TIMES,
    TRACKS,
    VOLUMES,
    fill_template,
    load_intents,
    variate_phrasing,
)

OUTPUT_FILE = "training/data/reply_lora_dataset.jsonl"

# ─── FOLLOW-UP PHRASING ──────────────────────────────────────────────────────
# Deliberately varied and separate from the base confirmation -- the model
# should learn to pick a phrasing, not always append the same fixed string.
FOLLOWUP_PHRASES = [
    "Will that be all sir?",
    "Is there anything more I can do for you sir?",
    "Shall I do anything further?",
    "Is there anything else you require sir?",
    "How else might I be of service?",
    "Anything further sir?",
    "Will there be anything else?",
]

# ─── JARVIS VOICE: PHRASE BANK ───────────────────────────────────────────────
# Keyed by intent name (from intents.json) purely for generation convenience --
# the model itself never sees the intent name, only the rendered action dict,
# so this key set can freely be extended per intent without touching the
# runtime contract.
PHRASE_BANK: Dict[str, List[str]] = {
    "light_on": [
        "Lights on sir.", "Illuminating the room now.", "There we are.",
        "Switching that on for you.", "Let there be light.", "On it.",
    ],
    "light_off": [
        "Lights off sir.", "Powering down the lights.", "Darkness, as requested.",
        "Switched off.", "Killing the lights now.", "Dark it is.",
    ],
    "light_toggle": [
        "Toggled sir.", "Switching that now.", "There it is.",
        "Flipped.", "There you go.",
    ],
    "light_color": [
        "Adjusting the colour now sir.", "There, much better.", "Colour changed.",
        "A tasteful choice.", "Quite the ambience.", "As you wish.",
    ],
    "light_dim": [
        "Adjusting the brightness sir.", "There we go.", "Dimmed to your liking.",
        "Brightness set.", "That should suit you better.",
    ],
    "light_brightness_up": [
        "Brightening it up sir.", "A bit brighter now.", "There we go.",
        "Raised the brightness.", "Better sir?",
    ],
    "light_brightness_down": [
        "Dimming it down sir.", "A bit dimmer now.", "There we go.",
        "Lowered the brightness.", "Better sir?",
    ],
    "spotify_play_generic": [
        "Resuming playback.", "Music's back on sir.", "There we go.", "Playing now.",
    ],
    "spotify_play_playlist": [
        "Playing your playlist now sir.", "There we are — enjoy.",
        "Cueing that up.", "On it.",
    ],
    "spotify_play_specific": [
        "Playing that track now.", "There you go sir.", "Cueing it up.", "Right away.",
    ],
    "spotify_pause": [
        "Pausing sir.", "Music paused.", "Quiet now.", "Silence, as requested.",
    ],
    "spotify_next": [
        "Skipping ahead sir.", "Next track.", "Onward.", "There we go.",
    ],
    "spotify_prev": [
        "Going back a track.", "Previous track sir.", "Back we go.",
    ],
    "spotify_volume": [
        "Volume adjusted sir.", "There we go.", "Set.",
    ],
    "spotify_status_track": [
        "That would be the current track.", "Checking now.", "Let's see sir.",
    ],
    "spotify_status_playlist": [
        "Checking the playlist sir.", "Let me have a look.", "Right away.",
    ],
    "spotify_status_queue": [
        "Checking what's next.", "Let's see what's queued.", "One moment sir.",
    ],
    "spotify_status_full": [
        "Pulling up the status now sir.", "Compiling that for you.", "Right away.",
    ],
    "system_discovery": [
        "Scanning for devices now sir.", "Searching the network.", "Give me a moment.",
    ],
    "system_restart_all": [
        "Restarting everything now sir.", "Right away — back shortly.", "As you wish.",
    ],
    "system_restart_module": [
        "Restarting that module sir.", "Right away.", "Back shortly.",
    ],
    "system_attention_on": [
        "Attention mode engaged sir.", "Focused and ready.", "At your service.",
    ],
    "system_attention_off": [
        "Stepping back sir.", "Attention mode disengaged.", "As you wish.",
    ],
    "system_check_updates": [
        "Checking for updates now sir.", "Looking now.", "Give me a moment.",
    ],
    "system_update_all": [
        "Installing updates now sir.", "This may take a moment.",
    ],
    "system_mode_debug": [
        "Debug mode engaged sir.", "Switching over now.",
    ],
    "system_mode_normal": [
        "Back to normal mode sir.", "Standard mode restored.", "Right you are.",
    ],
    "system_mode_background": [
        "Slipping into the background sir.", "Going quiet now.",
    ],
    "jarvis_reminder_set": [
        "Reminder set sir.", "Noted — I'll remind you.", "Consider it noted.",
    ],
    "system_followup_on": [
        "Follow-ups enabled sir.", "I'll check in from now on.",
    ],
    "system_followup_off": [
        "Follow-ups disabled sir.", "Understood — I'll hold my tongue.",
    ],
    "system_silent_mode_on": [
        "Going silent now sir.", "Understood.",
    ],
    "system_silent_mode_off": [
        "Silent mode disabled sir.", "Good to be heard again.",
    ],
    "system_silent_mode_toggle": [
        "Toggled sir.", "Switched.",
    ],
    "system_light_list": [
        "Here are the saved lights.", "Pulling that list now.", "Right away sir.",
    ],
    # system_light_rename intentionally excluded -- its single-word templates in
    # intents.json don't match what clControl.py's actuator requires ("old to
    # new"), so a confirmation reply here would often be false.
    "system_light_delete": [
        "Removed sir.", "Forgotten.", "Gone sir.",
    ],
    "system_light_default": [
        "Default set sir.", "Noted as your default.",
    ],
    "system_reminder_list": [
        "Here are your reminders.", "Fetching those now.", "Right away sir.",
    ],
    "system_reminder_delete": [
        "Reminder removed sir.", "Cleared.", "Consider it forgotten.",
    ],
    "ui_fullscreen": [
        "Going fullscreen sir.", "There we are.", "Expanding the view now.",
    ],
    "ui_overlay": [
        "Switching to overlay sir.", "Overlay active.",
    ],
    "alarm_create": [
        "Alarm set sir.", "Noted — I'll wake you.", "All set.",
    ],
    "alarm_cancel": [
        "Alarm cancelled sir.", "Consider it undone.",
    ],
    "alarm_list": [
        "Here are your alarms.", "Fetching those now.", "Right away sir.",
    ],
    "alarm_delete_all": [
        "All alarms cleared sir.", "Slate wiped clean.",
    ],
    "alarm_deactivate": [
        "Alarm silenced sir.", "Good morning.", "There we are.",
    ],
    "todo_add": [
        "Added to your list sir.", "Noted.", "Consider it added.",
    ],
    "todo_list": [
        "Here's your list sir.", "Pulling that up.", "Right away.",
    ],
    "calendar_add": [
        "Added to your calendar sir.", "Noted.", "Marked on the calendar.",
    ],
    "calendar_read": [
        "Checking your calendar now sir.", "Let's see sir.", "One moment.",
    ],
    "system_show_logs": [
        "Pulling up the logs sir.", "Right away.", "Bringing those up now.",
    ],
}

# ─── SLOT-AWARE PHRASE VARIANTS ──────────────────────────────────────────────
# {placeholder}-bearing phrases, mixed in alongside PHRASE_BANK's generic ones
# so JARVIS sometimes names the specific light/song/task instead of always
# speaking generically -- only ever selected when the referenced field is
# actually present for that sample (checked in select_phrase below), so
# there's no risk of a template trying to fill a value that doesn't exist.
SLOTTED_PHRASES: Dict[str, List[str]] = {
    "light_on": [
        "Turning on the {light_target} light.", "The {light_target} light is on now.",
        "{light_target} light on sir.",
    ],
    "light_off": [
        "Turning off the {light_target} light.", "The {light_target} light is off now.",
        "{light_target} light off sir.",
    ],
    "light_dim": [
        "Brightness on the {light_target} set to {lum} percent.",
        "The {light_target} is at {lum} percent now.",
    ],
    "light_color": [
        "Setting the {light_target} to {color}.", "The {light_target} light is {color} now.",
    ],
    "spotify_play_playlist": [
        "Playing your {playlist_name} playlist.", "Cueing up {playlist_name} for you.",
    ],
    "spotify_play_specific": [
        "Playing {track_name} now.", "Cueing up {track_name} for you.",
        "Playing {track_name} by {artist_name}.",
    ],
    "todo_add": [
        "Added \"{task}\" to your list.", "Noted — {task} is on your list now.",
    ],
    "calendar_add": [
        "Added {event} to your calendar.", "{event} is on the calendar now.",
    ],
    "alarm_create": [
        "Alarm set for {time_str}.", "I'll wake you at {time_str} sir.",
    ],
    "system_restart_module": [
        "Restarting {target} now.",
    ],
}

# ─── PROACTIVE SUGGESTIONS ────────────────────────────────────────────────────
# Deterministic action -> suggestion pairings (decided here, not left to the
# model to invent) -- occasionally used in place of a plain follow-up phrase.
# NOTE: purely conversational flavor right now. If the user replies "yes" to
# one of these, nothing automatically happens -- the action model has no
# visibility into what the reply model just asked, so a bare "yes" isn't
# understood as "do the suggested thing" without separate follow-through
# plumbing that doesn't exist yet.
SUGGESTION_PHRASES: Dict[str, List[str]] = {
    "light_on": [
        "Would you like some music to go with that?",
        "Shall I put some music on as well?",
        "Care for some music while you're at it?",
    ],
    "light_off": [
        "Shall I dim the rest of the house too?",
    ],
    "spotify_play_generic": [
        "Would you like the lights set to match the mood?",
    ],
    "spotify_play_playlist": [
        "Shall I set the mood with the lights as well?",
    ],
    "alarm_create": [
        "Shall I add this to your calendar as well?",
    ],
    "jarvis_reminder_set": [
        "Would you like this added to your calendar as well?",
    ],
    "todo_add": [
        "Shall I set a reminder for that as well?",
    ],
}
SUGGESTION_RATE = 0.4  # fraction of should_followup=True samples, for intents with a pool, that get a suggestion instead of a plain follow-up

NOISE_PHRASES = [
    "I didn't quite catch that sir.", "Forgive me, I didn't catch that.", "Pardon sir?",
    "I'm afraid I missed that.",
]

OOS_PHRASES = [
    "I'm afraid that's outside my capabilities sir.",
    "Not something I'm equipped for.",
    "That one's beyond me sir.",
    "I can't help with that just yet.",
]


def render_action_summary(action_id: str, args: Dict[str, Any]) -> str:
    """Generic, intent-agnostic rendering of an action dict for the prompt.
    Must work for future action_ids without any code changes here -- this
    model never sees the intents.json category, only this string."""
    parts = [action_id]
    for k, v in args.items():
        parts.append(f"{k}={v}")
    return " ".join(parts)


def select_phrase(intent_name: str, filled_args: Dict[str, Any]) -> str:
    """Picks a phrase for this intent, mixing in slot-referencing variants only
    when every placeholder they need is actually available for this sample,
    and substituting the real values in."""
    candidates = list(PHRASE_BANK.get(intent_name, []))
    for phrase in SLOTTED_PHRASES.get(intent_name, []):
        placeholders = set(re.findall(r"\{(\w+)\}", phrase))
        if placeholders.issubset(filled_args.keys()):
            candidates.append(phrase)
    chosen = random.choice(candidates)
    return chosen.format(**filled_args) if "{" in chosen else chosen


def format_record(system_line: str, user_text: str, reply: str) -> Dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": system_line},
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ]
    }


def gen_action_reply_samples(intents: Dict[str, Any], per_intent: int = 8) -> List[Dict]:
    records = []
    for intent_name in PHRASE_BANK:
        config = intents.get(intent_name)
        if not config or not config.get("templates"):
            continue
        action_id = config.get("action_id", "unknown")

        for _ in range(per_intent):
            template = random.choice(config["templates"])
            filled_text, filled_args = fill_template(template, config)
            user_text = variate_phrasing(filled_text)

            should_followup = random.random() < 0.5
            base_reply = select_phrase(intent_name, filled_args)

            tail = None
            if should_followup:
                suggestion_pool = SUGGESTION_PHRASES.get(intent_name)
                if suggestion_pool and random.random() < SUGGESTION_RATE:
                    tail = random.choice(suggestion_pool)
                else:
                    tail = random.choice(FOLLOWUP_PHRASES)
            reply = f"{base_reply} {tail}" if tail else base_reply

            summary = render_action_summary(action_id, filled_args)
            system_line = f"[ACTION] {summary} | followup: {'yes' if should_followup else 'no'}"

            records.append(format_record(system_line, user_text, reply.strip()))

    return records


def gen_noise_oos_reply_samples(count: int) -> List[Dict]:
    records = []
    for _ in range(count // 2):
        user_text = variate_phrasing(random.choice(NOISE_SAMPLES))
        system_line = "[ACTION] none | followup: no"
        records.append(format_record(system_line, user_text, random.choice(NOISE_PHRASES)))

    for _ in range(count // 2):
        user_text = variate_phrasing(random.choice(OUT_OF_SCOPE))
        system_line = "[ACTION] none | followup: no"
        records.append(format_record(system_line, user_text, random.choice(OOS_PHRASES)))

    return records


def main():
    intents = load_intents()
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    action_samples = gen_action_reply_samples(intents, per_intent=8)
    noise_samples = gen_noise_oos_reply_samples(150)

    all_records = action_samples + noise_samples
    random.shuffle(all_records)

    # Deduplicate by (system, user, assistant) triple -- unlike gen_dataset.py,
    # duplicate user text is fine here (many actions legitimately share
    # phrasing input), what must stay unique is the full training triple.
    seen = set()
    unique_records = []
    for rec in all_records:
        msgs = rec["messages"]
        key = (msgs[0]["content"], msgs[1]["content"], msgs[2]["content"])
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for item in unique_records:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"[SUCCESS] Generated {len(unique_records)} unique reply samples (from {len(all_records)} raw).")
    print(f"  Action-grounded: {len(action_samples)}")
    print(f"  Noise/OOS:       {len(noise_samples)}")
    print(f"  Intents covered: {len(PHRASE_BANK)} / {sum(1 for c in intents.values() if c.get('action_id','unknown')!='unknown' and c.get('templates'))} classifiable intents")


if __name__ == "__main__":
    main()
