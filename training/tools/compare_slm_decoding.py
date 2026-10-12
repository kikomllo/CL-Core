"""compare_slm_decoding.py - free decoding + schema validator vs grammar-constrained decoding.

Why: the GBNF grammar costs ~35 ms per generated token (measured: 650-900 ms per command vs ~200 ms free), so
the SLM could decode freely and only fall back to the grammar when src/nlp/clIntentSchema.py rejects the output.
This measures how often free output is valid AND identical to the constrained output, on the benchmark suite,
a sample of the training dataset and some real/noisy phrases.

Run from the repo root (loads the GGUF, so stop other GPU-heavy work first):
    python training/tools/compare_slm_decoding.py [N dataset samples, default 250]
"""
"""Free decoding + validator vs grammar-constrained decoding: speed and agreement."""
import sys, time, json, random, logging
sys.path.insert(0, "src")
logging.disable(logging.CRITICAL)
from nlp.clSLM import SLMInferenceEngine
from nlp.clIntentSchema import IntentSchema

core = json.load(open("config/core.json", encoding="utf-8"))
eng = SLMInferenceEngine(core)
schema = IntentSchema.from_gbnf(eng.grammar_path)

cases = []
for b in json.load(open("training/data/benchmark_suite.json", encoding="utf-8")):
    cases.append(("bench:" + b["category"], b["prompt"], b["state"]))
rows = [json.loads(l) for l in open("training/data/synthetic_lora_dataset.jsonl", encoding="utf-8")]
random.Random(5).shuffle(rows)
for r in rows[:int(sys.argv[1]) if len(sys.argv) > 1 else 250]:
    m = r["messages"]
    state = m[0]["content"].replace("[STATE]: ", "", 1)
    cases.append(("data", m[1]["content"], state))
# things the daemon really sent it, plus noise
for text in ["hey tervis pause music", "no jarvis change the light color to white", "turn the light on hey jarvis turn the light on",
             "new jarvis post music", "uh the thing with the", "asdf qwerty", "what is the capital of france", "play", "set it to"]:
    cases.append(("real/noise", text, "Spotify: Playing 'AUTO:BOOM' | ActiveContext: None | LastTargetLight: all"))

def decode(prompt, grammar):
    t = time.perf_counter()
    out = eng.llm(prompt, max_tokens=100, stop=["<|im_end|>", "\n\n\n"], grammar=grammar, temperature=eng.temp)
    return time.perf_counter() - t, out["choices"][0]["text"].strip()

for _ in range(2): decode(eng._format_prompt("pause music", "Spotify: Paused/Idle", None), eng.grammar)
stats = {"n": 0, "valid": 0, "same": 0, "t_con": 0.0, "t_free": 0.0, "t_fast": 0.0}
mismatch, invalid = [], []
for cat, text, state in cases:
    prompt = eng._format_prompt(text, state, None)
    t_c, raw_c = decode(prompt, eng.grammar)
    t_f, raw_f = decode(prompt, None)
    con = json.loads(raw_c) if raw_c else None
    free = schema.parse(raw_f)
    stats["n"] += 1; stats["t_con"] += t_c; stats["t_free"] += t_f
    if free is None:
        invalid.append((cat, text, raw_f[:110])); stats["t_fast"] += t_f + t_c        # fast attempt, then the grammar re-run
        continue
    stats["valid"] += 1; stats["t_fast"] += t_f
    if free == con: stats["same"] += 1
    else: mismatch.append((cat, text, json.dumps(con)[:120], json.dumps(free)[:120]))
n = stats["n"]
print(f"cases {n} | constrained avg {stats['t_con']/n*1000:.0f} ms | free avg {stats['t_free']/n*1000:.0f} ms | "
      f"fast-with-fallback avg {stats['t_fast']/n*1000:.0f} ms")
print(f"free output valid: {stats['valid']}/{n} ({stats['valid']/n:.1%}) | identical to constrained: {stats['same']}/{stats['valid']}")
print(f"\nINVALID free outputs ({len(invalid)}) -> would fall back to the grammar:")
for c in invalid[:12]: print("  ", c)
print(f"\nVALID but DIFFERENT from constrained ({len(mismatch)}):")
for m in mismatch[:15]: print("  ", m[0], "|", m[1], "\n      constrained:", m[2], "\n      free:       ", m[3])
