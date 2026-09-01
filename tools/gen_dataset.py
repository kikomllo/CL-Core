"""
gen_dataset.py — Comprehensive Synthetic LoRA Dataset Generator for JARVIS SLM.

Generates ~2,500 unique training samples covering:
  - 40% Single-Intent (all action_ids, mined from intents.json)
  - 25% Compound Intents (cross-domain combinations)
  - 20% Self-Corrections, Cancellations, Contextual Inference
  - 15% Noise Rejection & Out-of-Scope Adversarial

Output format: ChatML JSONL (system/user/assistant) matching Qwen fine-tuning spec.
"""

import os
import json
import random
import re
from typing import Dict, Any, List

OUTPUT_FILE = "data/synthetic_lora_dataset.jsonl"
INTENTS_FILE = "config/intents.json"
GRAMMAR_FILE = "config/grammars/intent_schema.gbnf"

# ─── DOMAIN KNOWLEDGE ────────────────────────────────────────────────────────

LIGHT_TARGETS = ["desk", "living room", "kitchen", "bedroom", "office", "all", "ceiling", "hallway"]
COLORS = ["red", "blue", "green", "warm white", "cool white", "purple", "orange", "yellow"]
LUM_VALUES = [10, 20, 25, 30, 40, 50, 60, 75, 80, 100]
PLAYLISTS = ["synthwave", "jazz", "rock", "classical", "lofi", "chill", "ambient", "pop", "hip hop"]
TRACKS = ["Resonance", "Blinding Lights", "Starboy", "Bohemian Rhapsody", "Stairway to Heaven"]
ARTISTS = ["Home", "The Weeknd", "Queen", "Led Zeppelin", "Daft Punk"]
MODULES = ["spotify", "whisper", "mic", "tts", "lights", "ui"]
TIMES = ["7 am", "8 am", "6:30 am", "9 pm", "10 pm", "5 minutes", "10 minutes", "30 minutes", "1 hour", "2 hours"]
TASKS = ["buy groceries", "call mom", "finish the report", "clean the kitchen", "take out the trash", "water the plants"]
EVENTS = ["dentist appointment", "team meeting", "lunch with Alex", "gym session", "project deadline"]
VOLUMES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

STATES_PLAYING = [
    f"Spotify: Playing ('{t}') | ActiveContext: None | LastTargetLight: {l}"
    for t in TRACKS for l in random.sample(LIGHT_TARGETS, 3)
]
STATES_PAUSED = [
    f"Spotify: Paused/Idle | ActiveContext: None | LastTargetLight: {l}"
    for l in LIGHT_TARGETS
]
ALL_STATES = STATES_PLAYING + STATES_PAUSED

# Natural phrasing variations for replies
ACKNOWLEDGE_PREFIXES = [
    "On it.", "Done.", "Right away, sir.", "Consider it done.", "Absolutely.",
    "Sure thing.", "Of course.", "You got it.", "Acknowledged.", "Understood.",
    "Roger that.", "Will do.", "Already on it.", "As you wish, sir.", "",
]

CORRECTION_PHRASES = [
    "wait, scratch that,", "actually,", "no wait,", "hold on,",
    "uh, never mind that,", "correction:", "instead,", "wait no,",
    "scratch that,", "on second thought,", "hmm actually,",
]

HESITATION_PREFIXES = [
    "can you", "could you", "would you", "please", "hey jarvis",
    "yo jarvis", "um", "uh", "so like", "hey", "yo",
]

NOISE_SAMPLES = [
    "top", "yeah", "um yeah so", "ah", "radio check", "hello?",
    "testing", "you", "a", "it", "hmm", "what", "thanks",
    "thank you", "thanks for watching", "cough", "one two three",
    "okay", "right", "sure", "huh", "yep", "nah", "oh",
]

OUT_OF_SCOPE = [
    "what time is it", "tell me a joke", "what's the weather like",
    "how old are you", "who created you", "what's the meaning of life",
    "translate this to spanish", "calculate 15 times 23",
    "what's the capital of france", "search for pizza near me",
    "send an email to john", "take a screenshot", "open google chrome",
    "book a flight to london", "order food from uber eats",
    "what's 2 plus 2", "how do I cook pasta", "who won the game last night",
]

# Natural reply phrases, one per intent (not per raw action value). Several
# intents share the same action_id/action combo (e.g. alarm_create,
# todo_add, jarvis_reminder_set and calendar_add are all "*.create" with no
# "action" field), so keying off the action value collapsed them into one
# generic, ungrammatical fallback ("Executing create") that broke the
# otherwise-universal "-ing" gerund convention used everywhere else in the
# dataset. That inconsistency is what taught the SLM to invent its own
# broken gerunds at inference time (e.g. "Createing", "Attention_oning").
# Every intent gets its own correct, distinct, -ing-form phrase here instead.
INTENT_REPLY_PHRASES = {
    "light_on": "Turning on",
    "light_off": "Turning off",
    "light_toggle": "Toggling",
    "light_color": "Changing the color",
    "light_dim": "Adjusting brightness",
    "spotify_play_generic": "Resuming playback",
    "spotify_play_playlist": "Starting your playlist",
    "spotify_play_specific": "Playing that track",
    "spotify_pause": "Pausing",
    "spotify_next": "Skipping to the next track",
    "spotify_prev": "Going back a track",
    "spotify_volume": "Setting the volume",
    "spotify_status_track": "Checking the track",
    "spotify_status_playlist": "Checking the playlist",
    "spotify_status_queue": "Checking the queue",
    "spotify_status_full": "Getting the status",
    "system_discovery": "Starting discovery",
    "system_restart_all": "Restarting everything",
    "system_restart_module": "Restarting that module",
    "system_attention_on": "Entering attention mode",
    "system_attention_off": "Exiting attention mode",
    "system_check_updates": "Checking for updates",
    "system_update_all": "Installing all updates",
    "system_mode_debug": "Switching to debug mode",
    "system_mode_normal": "Switching to normal mode",
    "system_mode_background": "Switching to background mode",
    "jarvis_reminder_set": "Setting the reminder",
    "system_followup_on": "Enabling follow-ups",
    "system_followup_off": "Disabling follow-ups",
    "system_silent_mode_on": "Enabling silent mode",
    "system_silent_mode_off": "Disabling silent mode",
    "system_silent_mode_toggle": "Toggling silent mode",
    "system_light_list": "Listing saved lights",
    "system_light_rename": "Renaming the light",
    "system_light_delete": "Removing the light",
    "system_light_default": "Setting the default light",
    "system_reminder_list": "Listing your reminders",
    "system_reminder_delete": "Deleting the reminder",
    "ui_fullscreen": "Switching to fullscreen",
    "ui_overlay": "Switching to overlay mode",
    "alarm_create": "Setting the alarm",
    "alarm_cancel": "Deleting the alarm",
    "alarm_list": "Listing your alarms",
    "alarm_delete_all": "Clearing all alarms",
    "alarm_deactivate": "Stopping the alarm",
    "todo_add": "Adding that to your list",
    "todo_list": "Listing your to-dos",
    "calendar_add": "Adding that to your calendar",
    "calendar_read": "Checking your calendar",
    "system_show_logs": "Opening the logs",
}

# Intents that need boosted representation to fix dataset imbalances.
# light_off/light_dim/spotify_play_playlist were raised after benchmark_slm.py
# runs kept misclassifying them (see data/benchmark_suite.json categories
# "Light Off (Simple)", "Light Dim", "Spotify Play").
BOOSTED_INTENTS = {
    # light_on/light_color/light_dim all share action_args["action"]=="on", so
    # their combined weight was outweighing light_off's boost.
    # IMPORTANT: balance this pair by trading weight between "on" and "off"
    # sides (as below), not by piling more weight onto light_off alone — an
    # earlier attempt did that and grew the total weighted pool from ~51 to
    # ~71 units, which diluted every one of the ~50 other trainable intents'
    # share of the fixed 1200-sample draw and caused new, unrelated
    # cross-domain misclassifications (light->alarm, todo->spotify, etc.)
    # that weren't present before. Same principle applies to gen_compound_samples'
    # combos and gen_correction_samples' correction_pairs: balance on/off by
    # swapping entries, not by only adding "off" ones.
    "light_off": 5,
    "light_color": 2,
    "light_dim": 2,
    "spotify_play_playlist": 4,
    "alarm_create": 2,
}


def load_intents() -> Dict[str, Any]:
    with open(INTENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def random_state() -> str:
    return random.choice(ALL_STATES)


def random_reply_prefix() -> str:
    return random.choice(ACKNOWLEDGE_PREFIXES)


def variate_phrasing(base: str) -> str:
    """Add natural human speech variations to a base command."""
    mutations = []

    # Maybe add a hesitation prefix
    if random.random() < 0.3:
        prefix = random.choice(HESITATION_PREFIXES)
        mutations.append(f"{prefix} {base}")
    else:
        mutations.append(base)

    # Maybe add "please" at end
    if random.random() < 0.2:
        mutations[-1] += " please"

    # Random capitalization (simulating Whisper output)
    result = mutations[-1]
    if random.random() < 0.5:
        result = result.lower()

    return result


def fill_template(template: str, intent_config: Dict[str, Any]) -> tuple:
    """Fill a template with random slot values, returns (filled_text, filled_args)."""
    action_args = intent_config.get("action_args", {}).copy()
    filled = template

    if "{light_target}" in filled:
        t = random.choice(LIGHT_TARGETS)
        filled = filled.replace("{light_target}", t)
        action_args["light_target"] = t

    if "{color}" in filled:
        c = random.choice(COLORS)
        filled = filled.replace("{color}", c)
        action_args["color"] = c

    if "{lum}" in filled:
        l = random.choice(LUM_VALUES)
        filled = filled.replace("{lum}", str(l))
        action_args["lum"] = l

    if "{volume}" in filled:
        v = random.choice(VOLUMES)
        filled = filled.replace("{volume}", str(v))
        action_args["volume"] = v

    if "{playlist_name}" in filled:
        p = random.choice(PLAYLISTS)
        filled = filled.replace("{playlist_name}", p)
        action_args["playlist_name"] = p

    if "{track_name}" in filled:
        t = random.choice(TRACKS)
        filled = filled.replace("{track_name}", t)
        action_args["track_name"] = t

    if "{artist_name}" in filled:
        a = random.choice(ARTISTS)
        filled = filled.replace("{artist_name}", a)
        action_args["artist_name"] = a

    if "{search_query}" in filled:
        # TRACKS only, deliberately excluding PLAYLISTS: spotify_play_playlist's
        # "play some {playlist_name}" template used the same PLAYLISTS pool,
        # so identical surface text (e.g. "play some jazz") was being trained
        # with two different, mutually exclusive field labels depending on
        # which intent got sampled — label noise that showed up as the model
        # losing confidence in the whole action, not just the field name.
        q = random.choice(TRACKS)
        filled = filled.replace("{search_query}", q)
        action_args["search_query"] = q

    if "{time_str}" in filled:
        t = random.choice(TIMES)
        filled = filled.replace("{time_str}", t)
        action_args["time_str"] = t

    if "{time}" in filled:
        t = random.choice(TIMES)
        filled = filled.replace("{time}", t)
        action_args["time"] = t

    if "{task}" in filled:
        t = random.choice(TASKS)
        filled = filled.replace("{task}", t)
        action_args["task"] = t

    if "{event}" in filled:
        e = random.choice(EVENTS)
        filled = filled.replace("{event}", e)
        action_args["event"] = e

    if "{target}" in filled:
        m = random.choice(MODULES)
        filled = filled.replace("{target}", m)
        action_args["target"] = m

    if "{target_str}" in filled:
        t = random.choice(LIGHT_TARGETS)
        filled = filled.replace("{target_str}", t)
        action_args["target_str"] = t

    return filled, action_args


# ─── SAMPLE GENERATORS ───────────────────────────────────────────────────────

def gen_single_intent_samples(intents: Dict[str, Any], count: int) -> List[Dict]:
    """Generate single-intent samples with weighted sampling to fix imbalances."""
    records = []
    intent_keys = [k for k in intents.keys() if intents[k].get("action_id", "unknown") != "unknown" and intents[k].get("templates")]

    # Build weighted key list — boosted intents appear multiple times
    weighted_keys = []
    for k in intent_keys:
        weight = BOOSTED_INTENTS.get(k, 1)
        weighted_keys.extend([k] * weight)

    for _ in range(count):
        intent_name = random.choice(weighted_keys)
        config = intents[intent_name]
        action_id = config.get("action_id", "unknown")
        templates = config.get("templates", [])

        template = random.choice(templates)
        filled_text, filled_args = fill_template(template, config)
        user_text = variate_phrasing(filled_text)

        action_payload = {"action_id": action_id}
        action_payload.update(filled_args)

        prefix = random_reply_prefix()
        verb_phrase = INTENT_REPLY_PHRASES[intent_name]
        reply = f"{prefix} {verb_phrase}." if prefix else f"{verb_phrase}."
        reply = reply.strip()

        records.append({
            "state": random_state(),
            "user": user_text,
            "actions": [action_payload],
            "reply": reply
        })

    return records


def gen_compound_samples(intents: Dict[str, Any], count: int) -> List[Dict]:
    """Generate compound intent samples combining 2 different action domains."""
    records = []

    # Define sensible compound combos.
    # light_on/light_dim/light_color all resolve to action_args["action"]=="on",
    # so counting combos naively (light_on vs light_off) undercounts the real
    # skew: of the 10 light-involving combos here, 5 resolve to "on" and 5 to
    # "off". Balanced by swapping which intent each pairing uses (light_off
    # <-> spotify_play_specific, light_off <-> todo_add below) rather than
    # appending new combos — appending grows the list and dilutes every other
    # (non-light) combo's share of the fixed draw count, which is what
    # caused unrelated cross-domain misclassifications in an earlier attempt
    # at this same fix. See also gen_correction_samples for the same
    # swap-not-append fix applied to correction_pairs.
    combos = [
        # (intent_key_1, intent_key_2, connector)
        ("light_dim", "spotify_play_generic", "and"),
        ("light_off", "spotify_play_specific", "and also"),
        ("light_off", "spotify_pause", "and"),
        ("light_on", "jarvis_reminder_set", "and also"),
        ("light_color", "spotify_play_generic", "and then"),
        ("alarm_create", "light_off", "and"),
        ("todo_add", "light_off", "plus"),
        ("light_on", "alarm_create", "and also"),
        ("spotify_pause", "light_off", "and then"),
        ("light_dim", "spotify_volume", "and"),
        ("jarvis_reminder_set", "spotify_pause", "and"),
        ("todo_add", "spotify_play_generic", "and then"),
    ]

    for _ in range(count):
        combo = random.choice(combos)
        k1, k2, connector = combo

        if k1 not in intents or k2 not in intents:
            continue

        c1, c2 = intents[k1], intents[k2]
        t1 = random.choice(c1.get("templates", ["action one"]))
        t2 = random.choice(c2.get("templates", ["action two"]))

        filled1, args1 = fill_template(t1, c1)
        filled2, args2 = fill_template(t2, c2)

        user_text = variate_phrasing(f"{filled1} {connector} {filled2}")

        a1 = {"action_id": c1["action_id"]}
        a1.update(args1)
        a2 = {"action_id": c2["action_id"]}
        a2.update(args2)

        prefix = random_reply_prefix()
        reply = f"{prefix} Handling both requests." if prefix else "Both commands processed."

        records.append({
            "state": random_state(),
            "user": user_text,
            "actions": [a1, a2],
            "reply": reply.strip()
        })

    return records


def gen_correction_samples(intents: Dict[str, Any], count: int) -> List[Dict]:
    """Generate self-correction samples where the user changes their mind mid-sentence."""
    records = []
    # Only the second element of each pair becomes the training label. This
    # list stays at 6 pairs (not 8) — an earlier fix appended 2 more instead
    # of rebalancing within the original list, which diluted every other
    # correction pair's share of the fixed draw count. Balanced here by
    # flipping the (spotify_play_generic, light_on) pair to end in light_off
    # instead: 2 pairs now terminate "on" (light_on, light_color), 2 in "off"
    # (light_off, light_off), 2 are non-light (spotify_play_generic, jarvis_reminder_set).
    correction_pairs = [
        ("light_on", "light_on"),       # Change target
        ("light_on", "light_off"),      # Change action
        ("light_on", "spotify_play_generic"),  # Change domain entirely
        ("spotify_play_generic", "light_off"),  # Change domain, ends off
        ("alarm_create", "jarvis_reminder_set"),
        ("light_dim", "light_color"),
    ]

    for _ in range(count):
        pair = random.choice(correction_pairs)
        k1, k2 = pair
        if k1 not in intents or k2 not in intents:
            continue

        c1, c2 = intents[k1], intents[k2]
        t1 = random.choice(c1.get("templates", ["do thing one"]))
        t2 = random.choice(c2.get("templates", ["do thing two"]))

        filled1, _ = fill_template(t1, c1)
        filled2, args2 = fill_template(t2, c2)

        correction = random.choice(CORRECTION_PHRASES)
        user_text = f"{filled1}... {correction} {filled2}"
        if random.random() < 0.3:
            user_text = variate_phrasing(user_text)

        a2 = {"action_id": c2["action_id"]}
        a2.update(args2)

        replies = [
            f"Changed my mind noted. {random_reply_prefix()}",
            f"Understood, switching to the new request.",
            f"Scratch that, got it. Executing the updated command.",
            f"{random_reply_prefix()} Corrected.",
        ]

        records.append({
            "state": random_state(),
            "user": user_text,
            "actions": [a2],
            "reply": random.choice(replies).strip()
        })

    return records


def gen_contextual_inference_samples(count: int) -> List[Dict]:
    """Samples where the model must read [STATE] to infer the target."""
    records = []
    
    contextual_templates = [
        # Pronoun resolution from LastTargetLight.
        # weight=3 on this one: it's the only template producing action="off"
        # among the 4 light-pronoun templates below, while the other 3
        # (brighter/dim/color) all produce action="on" — left at uniform
        # random.choice() this alone skewed the aggregate on/off split.
        {
            "user_variants": ["turn it off", "switch it off", "kill it", "turn that off"],
            "state_key": "LastTargetLight",
            "build_action": lambda target: {"action_id": "light.set", "action": "off", "light_target": target},
            "reply_fn": lambda target: f"Turning off the {target} light.",
            "weight": 3,
        },
        {
            "user_variants": ["make it brighter", "brighter please", "more light", "turn it up"],
            "state_key": "LastTargetLight",
            "build_action": lambda target: {"action_id": "light.set", "action": "on", "lum": 100, "light_target": target},
            "reply_fn": lambda target: f"Maxing out the {target} light."
        },
        {
            "user_variants": ["dim it down", "lower the brightness", "make it darker", "less light"],
            "state_key": "LastTargetLight",
            "build_action": lambda target: {"action_id": "light.set", "action": "on", "lum": 20, "light_target": target},
            "reply_fn": lambda target: f"Dimming the {target} light."
        },
        {
            "user_variants": ["make them red", "change it to blue", "set it to warm white", "purple please"],
            "state_key": "LastTargetLight",
            "build_action": lambda target: {"action_id": "light.set", "action": "on", "light_target": target, "color": "red"},
            "reply_fn": lambda target: f"Changing the {target} light color."
        },
        # Contextual media commands based on Spotify state
        {
            "user_variants": ["pause", "stop", "pause that", "hold on"],
            "state_key": "Spotify_Playing",
            "build_action": lambda _: {"action_id": "spotify.control", "action": "pause"},
            "reply_fn": lambda _: "Pausing playback."
        },
        {
            "user_variants": ["resume", "continue", "keep playing", "unpause"],
            "state_key": "Spotify_Paused",
            "build_action": lambda _: {"action_id": "spotify.control", "action": "play"},
            "reply_fn": lambda _: "Resuming playback."
        },
    ]

    weighted_templates = []
    for t in contextual_templates:
        weighted_templates.extend([t] * t.get("weight", 1))

    for _ in range(count):
        template = random.choice(weighted_templates)
        user_text = variate_phrasing(random.choice(template["user_variants"]))
        target = random.choice(LIGHT_TARGETS)

        if template["state_key"] == "Spotify_Playing":
            state = random.choice(STATES_PLAYING)
        elif template["state_key"] == "Spotify_Paused":
            state = random.choice(STATES_PAUSED)
        else:
            state = f"Spotify: Paused/Idle | ActiveContext: None | LastTargetLight: {target}"

        action = template["build_action"](target)
        reply = template["reply_fn"](target)

        records.append({
            "state": state,
            "user": user_text,
            "actions": [action],
            "reply": f"{random_reply_prefix()} {reply}".strip()
        })

    return records


def gen_noise_rejection_samples(count: int) -> List[Dict]:
    """Noise, gibberish, and out-of-scope queries that should return empty actions."""
    records = []

    for _ in range(count // 2):
        noise = random.choice(NOISE_SAMPLES)
        records.append({
            "state": random_state(),
            "user": noise,
            "actions": [],
            "reply": random.choice(["", "I didn't catch that, sir.", "Sorry, I didn't understand."])
        })

    for _ in range(count // 2):
        oos = random.choice(OUT_OF_SCOPE)
        user_text = variate_phrasing(oos)
        records.append({
            "state": random_state(),
            "user": user_text,
            "actions": [],
            "reply": random.choice([
                "I'm not set up for that yet, sir.",
                "That's outside my capabilities for now.",
                "I can't do that yet, but noted for the future.",
                "Sorry sir, that's not something I can handle right now.",
            ])
        })

    return records


# ─── GRAMMAR GENERATION ──────────────────────────────────────────────────────
# The GBNF grammar used at inference time (config/grammars/intent_schema.gbnf)
# must stay in lockstep with what this generator actually produces. Previously
# "action_id" and "action" were left as free-form strings, which let the model
# hallucinate values outside the trained vocabulary (e.g. echoing the action_id
# back as the "action" value, or copying stray input words). Deriving both
# enums straight from intents.json closes that gap and keeps them from drifting
# out of sync as intents are added.

OPEN_STRING_FIELDS = [
    "light_target", "color", "playlist_name", "track_name", "artist_name",
    "search_query", "task", "time", "time_str", "event", "target", "target_str",
]


def collect_vocab(intents: Dict[str, Any]) -> tuple:
    action_ids = sorted({
        cfg["action_id"] for cfg in intents.values()
        if cfg.get("action_id", "unknown") != "unknown"
    })
    actions = sorted({
        cfg["action_args"]["action"] for cfg in intents.values()
        if "action" in cfg.get("action_args", {})
    })
    return action_ids, actions


def _gbnf_enum(rule_name: str, values: List[str]) -> str:
    alts = " |\n    ".join(f'"\\"{v}\\""' for v in values)
    return f"{rule_name} ::=\n    {alts}"


def build_grammar(action_ids: List[str], actions: List[str]) -> str:
    field_alts = ['"\\"action\\"" ws ":" ws action-value']
    field_alts += [f'"\\"{f}\\"" ws ":" ws string' for f in OPEN_STRING_FIELDS]
    field_alts += ['"\\"lum\\"" ws ":" ws number', '"\\"volume\\"" ws ":" ws number']

    return f"""# AUTO-GENERATED by tools/gen_dataset.py — do not hand-edit.
# Regenerate with: python tools/gen_dataset.py (edit config/intents.json instead)
# Root: exactly matches training output schema {{"actions": [...], "reply": "..."}}
root ::= "{{" ws "\\"actions\\"" ws ":" ws action-array "," ws "\\"reply\\"" ws ":" ws string ws "}}"

# Array of actions (can be empty [] or contain objects)
action-array ::= "[" ws "]" | "[" ws action-object (ws "," ws action-object)* ws "]"

# Action object: action_id is mandatory and constrained to known intents,
# followed by zero or more optional fields
action-object ::= "{{" ws "\\"action_id\\"" ws ":" ws action-id (ws "," ws action-field)* ws "}}"

# Known action_id values, derived from config/intents.json
{_gbnf_enum("action-id", action_ids)}

# Each optional field as a flat alternative (no parentheses)
action-field ::=
    {" |\n    ".join(field_alts)}

# Known "action" verb values, derived from config/intents.json action_args
{_gbnf_enum("action-value", actions)}

# String: proper GBNF character class (not regex), used for open-vocabulary fields
string ::= "\\"" string-char* "\\""
string-char ::= [^"\\\\] | "\\\\" ["\\\\/bfnrt]

# Number: integer or decimal, no grouping
number ::= [0-9]+ number-decimal?
number-decimal ::= "." [0-9]+

# Whitespace
ws ::= [ \\t\\n\\r]*
"""


def write_grammar(intents: Dict[str, Any]) -> None:
    action_ids, actions = collect_vocab(intents)
    os.makedirs(os.path.dirname(GRAMMAR_FILE), exist_ok=True)
    with open(GRAMMAR_FILE, "w", encoding="utf-8") as f:
        f.write(build_grammar(action_ids, actions))
    print(f"[SUCCESS] Wrote grammar to '{GRAMMAR_FILE}' ({len(action_ids)} action_ids, {len(actions)} actions).")


# ─── FORMATTING & MAIN ───────────────────────────────────────────────────────

def format_record(r: Dict[str, Any]) -> Dict[str, Any]:
    """Format into ChatML JSONL matching Qwen fine-tuning spec."""
    system_prompt = f"[STATE]: {r['state']}"
    assistant_output = json.dumps({
        "actions": r["actions"],
        "reply": r["reply"]
    }, ensure_ascii=False)
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": r["user"]},
            {"role": "assistant", "content": assistant_output}
        ]
    }


def main():
    intents = load_intents()
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    write_grammar(intents)

    # Target distribution: ~2500 total
    singles = gen_single_intent_samples(intents, 1200)        # 48% — boosted for imbalance fix
    compounds = gen_compound_samples(intents, 500)             # 20%
    corrections = gen_correction_samples(intents, 350)         # 14%
    contextual = gen_contextual_inference_samples(200)          # 8%
    noise = gen_noise_rejection_samples(375)                    # 15%

    all_records = singles + compounds + corrections + contextual + noise
    random.shuffle(all_records)

    # Deduplicate by user prompt text
    seen = set()
    unique_records = []
    for rec in all_records:
        key = rec["user"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for item in unique_records:
            f.write(json.dumps(format_record(item), ensure_ascii=False) + "\n")

    # Stats
    n = len(unique_records)
    n_single = sum(1 for r in unique_records if len(r["actions"]) == 1 and "..." not in r["user"])
    n_compound = sum(1 for r in unique_records if len(r["actions"]) >= 2)
    n_empty = sum(1 for r in unique_records if len(r["actions"]) == 0)
    action_ids = set()
    for r in unique_records:
        for a in r["actions"]:
            action_ids.add(a.get("action_id", ""))

    print(f"[SUCCESS] Generated {n} unique training samples (from {len(all_records)} raw).")
    print(f"  Single-Intent:  {n_single}")
    print(f"  Compound:       {n_compound}")
    print(f"  Noise/OOS:      {n_empty}")
    print(f"  Action IDs covered: {len(action_ids)} -> {sorted(action_ids)}")


if __name__ == "__main__":
    main()