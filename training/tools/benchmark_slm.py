"""
benchmark_slm.py — Comprehensive SLM Benchmark Runner.

Runs the full benchmark_suite.json against the GGUF model with grammar constraints.
Evaluates: JSON validity, action_id accuracy, field accuracy, latency, and empty-action rejection.
Outputs a detailed per-test report and a final summary with category-level breakdowns.
"""

import argparse
import os
import sys
import json
import time
from typing import Dict, Any, List


def _register_nvidia_dll_dirs() -> None:
    """Mirrors src/nlp/clSLM.py's fix -- llama_cpp's own DLL loader never
    searches pip-installed nvidia-*-cu12 packages' bundled DLL folders, only
    $CUDA_PATH, so a GPU wheel can fail to import if the system CUDA Toolkit
    is a different major version. Must run before `import llama_cpp`."""
    if sys.platform != "win32":
        return
    site_packages = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "Lib", "site-packages")
    nvidia_dir = os.path.join(site_packages, "nvidia")
    if not os.path.isdir(nvidia_dir):
        return
    for pkg_name in os.listdir(nvidia_dir):
        bin_dir = os.path.join(nvidia_dir, pkg_name, "bin")
        if os.path.isdir(bin_dir):
            try:
                os.add_dll_directory(bin_dir)
            except OSError:
                pass


_register_nvidia_dll_dirs()

if sys.platform == "win32":
    # Piped/redirected stdout on Windows defaults to cp1252, which can't
    # encode this script's box-drawing/checkmark characters.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from llama_cpp import Llama, LlamaGrammar

BENCHMARK_PATH = "training/data/benchmark_suite.json"
MODEL_PATH = "models/jarvis-brain-v2-q4_k_m.gguf"
GRAMMAR_PATH = "config/grammars/intent_schema.gbnf"

# ─── PROMPT FORMATTER ────────────────────────────────────────────────────────

def _format_prompt(user_prompt: str, system_snapshot: str) -> str:
    """Naked ChatML prompt matching the fine-tuning layout."""
    return (
        f"<|im_start|>system\n[STATE]: {system_snapshot}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

# ─── EVALUATION LOGIC ────────────────────────────────────────────────────────

def evaluate_actions(actual: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Flexible evaluator:
    - Checks action count match
    - Checks action_id match (order-sensitive)
    - Checks field values only for fields explicitly specified in expected
    Returns a dict with pass/fail details.
    """
    result = {"passed": True, "errors": []}

    # Count check
    if len(actual) != len(expected):
        result["passed"] = False
        result["errors"].append(f"Count mismatch: expected {len(expected)} actions, got {len(actual)}")
        return result

    # Empty actions (noise rejection) — both empty means pass
    if len(expected) == 0 and len(actual) == 0:
        return result

    for i, (act, exp) in enumerate(zip(actual, expected)):
        # action_id is always required
        if act.get("action_id") != exp.get("action_id"):
            result["passed"] = False
            result["errors"].append(
                f"Action[{i}] action_id: expected '{exp.get('action_id')}', got '{act.get('action_id')}'"
            )
            continue

        # Only check fields that are explicitly in the expected dict (besides action_id)
        for key, expected_val in exp.items():
            if key == "action_id":
                continue
            actual_val = act.get(key)
            if actual_val != expected_val:
                result["passed"] = False
                result["errors"].append(
                    f"Action[{i}].{key}: expected '{expected_val}', got '{actual_val}'"
                )

    return result

# ─── MAIN BENCHMARK RUNNER ───────────────────────────────────────────────────

def run_benchmark(model_path: str = MODEL_PATH):
    if not os.path.exists(BENCHMARK_PATH):
        print(f"Error: {BENCHMARK_PATH} not found.")
        sys.exit(1)

    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        tests = json.load(f)

    if not os.path.exists(model_path):
        print(f"Error: Model file '{model_path}' not found.")
        sys.exit(1)

    print(f"Loading model '{model_path}'...")
    llm = Llama(model_path=model_path, n_ctx=1024, n_threads=4, verbose=False)
    
    grammar = None
    if os.path.exists(GRAMMAR_PATH):
        grammar = LlamaGrammar.from_file(GRAMMAR_PATH)
        print(f"GBNF grammar loaded from '{GRAMMAR_PATH}'")
    else:
        print(f"WARNING: No grammar file found. Running unconstrained.")

    total = len(tests)
    passed = 0
    json_errors = 0
    total_time = 0.0
    category_stats = {}

    divider = "─" * 100
    print(f"\n{'#':<3} │ {'Category':<28} │ {'Result':<6} │ {'Time':<7} │ Details")
    print(divider)

    for i, test in enumerate(tests, 1):
        cat = test["category"]
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "passed": 0, "time": 0.0}
        category_stats[cat]["total"] += 1

        prompt = _format_prompt(test["prompt"], test["state"])
        
        t0 = time.perf_counter()
        try:
            output = llm(
                prompt,
                max_tokens=256,
                stop=["<|im_end|>"],
                grammar=grammar,
                temperature=0.0
            )
            elapsed = time.perf_counter() - t0
        except Exception as e:
            elapsed = time.perf_counter() - t0
            total_time += elapsed
            category_stats[cat]["time"] += elapsed
            print(f"{i:<3} │ {cat:<28} │ {'CRASH':<6} │ {elapsed:<7.2f} │ Engine error: {str(e)[:50]}")
            continue

        total_time += elapsed
        category_stats[cat]["time"] += elapsed

        raw_text = output["choices"][0]["text"].strip()

        # 1. JSON Parse Check
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            json_errors += 1
            print(f"{i:<3} │ {cat:<28} │ {'JSON!':<6} │ {elapsed:<7.2f} │ Parse failed: {str(e)[:40]}")
            print(f"    │ Raw output: {raw_text[:80]}")
            continue

        # 2. Action Evaluation
        actual_actions = parsed.get("actions", [])
        eval_result = evaluate_actions(actual_actions, test["expected_actions"])

        if eval_result["passed"]:
            passed += 1
            category_stats[cat]["passed"] += 1
            reply_preview = parsed.get("reply", "")[:40]
            print(f"{i:<3} │ {cat:<28} │ {'PASS':<6} │ {elapsed:<7.2f} │ ✓ Actions: {len(actual_actions)} │ Reply: \"{reply_preview}\"")
        else:
            errors_str = "; ".join(eval_result["errors"][:2])
            print(f"{i:<3} │ {cat:<28} │ {'FAIL':<6} │ {elapsed:<7.2f} │ ✗ {errors_str}")

    # ─── SUMMARY ──────────────────────────────────────────────────────────────
    accuracy = (passed / total) * 100 if total else 0
    avg_latency = total_time / total if total else 0

    print(f"\n{'═' * 100}")
    print(f"  BENCHMARK SUMMARY")
    print(f"{'═' * 100}")
    print(f"  Model:          {os.path.basename(model_path)}")
    print(f"  Grammar:        {'Enabled' if grammar else 'Disabled'}")
    print(f"  Total Tests:    {total}")
    print(f"  Passed:         {passed}/{total} ({accuracy:.1f}%)")
    print(f"  JSON Errors:    {json_errors}")
    print(f"  Avg Latency:    {avg_latency:.2f}s per eval")
    print(f"  Total Time:     {total_time:.1f}s")
    print()

    # Category breakdown
    print(f"  {'Category':<28} │ {'Pass Rate':<12} │ {'Avg Time':<10}")
    print(f"  {'─' * 28}─┼─{'─' * 12}─┼─{'─' * 10}")
    for cat, stats in sorted(category_stats.items()):
        rate = f"{stats['passed']}/{stats['total']}"
        avg_t = stats['time'] / stats['total'] if stats['total'] else 0
        print(f"  {cat:<28} │ {rate:<12} │ {avg_t:<10.2f}s")

    print(f"{'═' * 100}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark the action-classification GGUF against benchmark_suite.json.")
    parser.add_argument("--model", default=MODEL_PATH, help=f"Path to the GGUF model (default: {MODEL_PATH})")
    args = parser.parse_args()
    run_benchmark(args.model)