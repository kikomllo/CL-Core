"""Exports the Ultron "gap" head's geometry for the web dashboard.

The head is built from a 32 x 32 pixel-art grid by numpy mask maths (ui/clUltronHead.py, ui/clUltronHeadGap.py). The web page does
not redo that: this script runs the Qt model once and writes the result (which cell is metal, plate, eye, cheek, gap ...) to
assets/themes/ultron/head/gap_head.json, and web/js/ultronhead.js only draws and animates it. So the Qt code stays the single
source of the shape; after changing the grid or the geometry rerun

    python src/utils/clWebHeadExport.py

(tests/test_webHead.py fails when the committed file is out of date).
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = os.path.join(ROOT, "assets", "themes", "ultron", "head", "gap_head.json")


def build_model() -> dict:
    from ui import clUltronHead as base
    from ui import clUltronHeadGap as gap
    head = gap.GapHead()
    cheek = head.cheek_mask
    metal = [[int(j), int(i), int(bool(head._rim[j, i]))] for j in range(head.h) for i in range(head.w) if head.metal[j, i] and not cheek[j, i]]
    return {
        "version": 1,
        "w": int(head.w), "h": int(head.h),
        "consts": {
            "GAP_REST": gap.GAP_REST, "GAP_RISE": gap.GAP_RISE, "GAP_DROP": gap.GAP_DROP, "GAP_WIDEN": gap.GAP_WIDEN,
            "GAP_EDGE_DEPTH": gap.GAP_EDGE_DEPTH, "PLATE_GAIN": gap.PLATE_GAIN, "CHEEK_BULGE": base.CHEEK_BULGE,
            "DIM_METAL": list(base.DIM_METAL), "PLATE_HOT": list(base.PLATE_HOT), "EYE_CORE": list(base.EYE_CORE),
            "HALO_PEAK_ALPHA": base.HALO_PEAK_ALPHA,
            "PULSE_QUIET": base.PULSE_QUIET, "PULSE_LOUD": base.PULSE_LOUD, "BREATH": base.BREATH,
            "GLITCH_DEFAULTS": base.GLITCH_DEFAULTS,
        },
        "shimmer": [[round(float(v), 3) for v in row] for row in head._shimmer],
        "metal": metal,                                             # [row, col, is_rim]: the static dim metal (the cheeks are live)
        "cheeks": [[j, i, round(u, 4), round(weight, 4), int(rim), int(seam)] for j, i, u, weight, rim, _v, seam in head.cheek_cells],
        "brows": [[j, i] for j, i in head.brows],
        "mouth": [[j, i] for j, i, _ in head.mouth],
        "gapCols": [{"i": i, "top": top, "taper": round(taper, 4), "cells": [[j, int(p)] for j, p in cells]} for i, top, taper, cells in head.gap_cols],
        "gapEdges": [[r, c, d] for r, c, d in head.gap_edges],
        "lowerCols": [{"i": i, "bottom": bottom, "taper": round(taper, 4), "rows": rows} for i, bottom, taper, rows in head.lower_cols],
        "eyes": [[j, i] for j, i in head.eye_cells],
        "eyeCentres": [[round(j, 3), round(i, 3)] for j, i in head.eye_centres],
    }


def write(path: str = OUT) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_model(), f, separators=(",", ":"))
    return path


if __name__ == "__main__":
    out = write()
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")
