"""The web Ultron head: geometry exported from the Qt model, and the animation maths ported to JS (web/js/ultronhead.js)."""
import json
import math
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

HEAD_JS = os.path.join(ROOT, "web", "js", "ultronhead.js").replace("\\", "/")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_js(body):
    script = f'const H = await import("file:///{HEAD_JS}");\n{body}'
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestExportedGeometry:
    def test_the_committed_file_matches_the_qt_model(self):
        """Fails when the grid or the gap geometry changed and clWebHeadExport.py was not rerun."""
        from utils import clWebHeadExport
        with open(clWebHeadExport.OUT, "r", encoding="utf-8") as f:
            committed = json.load(f)
        assert committed == json.loads(json.dumps(clWebHeadExport.build_model()))

    def test_the_geometry_is_what_the_painter_expects(self):
        from utils import clWebHeadExport
        m = clWebHeadExport.build_model()
        assert (m["w"], m["h"]) == (len(m["shimmer"][0]), len(m["shimmer"]))
        assert m["metal"] and m["cheeks"] and m["mouth"] and m["gapCols"] and m["eyes"] and len(m["eyeCentres"]) == 2
        for j, i, *_ in m["metal"] + m["cheeks"]:
            assert 0 <= j < m["h"] and 0 <= i < m["w"]
        # the metal and the live cheeks never paint the same cell twice
        assert not ({(j, i) for j, i, _ in m["metal"]} & {(j, i) for j, i, *_ in m["cheeks"]})


@needs_node
class TestJsMatchesPython:
    def test_the_constants_agree(self):
        from utils import clWebHeadExport
        py = clWebHeadExport.build_model()["consts"]
        js = run_js("console.log(JSON.stringify(H.C));")
        for k, v in js.items():
            assert py[k] == v, k
        assert run_js("console.log(JSON.stringify(H.GLITCH_DEFAULTS));") == py["GLITCH_DEFAULTS"]

    def test_the_brightness_and_movement_maths_agree(self):
        from ui import clUltronHead as base
        from ui.clUltronHeadGap import GapHead
        levels, times = [0, 0.1, 0.35, 0.7, 1.0, 1.4, -0.2], [0, 0.7, 3.3, 10.0]
        want = {
            "light": [base.UltronHead.light(l, t, 0.55, 0.3) for l in levels for t in times],
            "edge": [GapHead.gap_edge(20, 0.8, l) for l in levels],
            "lowerEdge": [GapHead.lower_edge(50, 0.6, l) for l in levels],
            "side": [GapHead.gap_side_lit(d, l) for d in range(5) for l in levels],
            "lit": [GapHead.gap_lit(j, e) + GapHead.lower_lit(j, e) for j in range(18, 24) for e in (19.2, 21.5, 22.9)],
            "shift": [base.UltronHead.cheek_shift(None, u, w, l) for u in (-1, -0.3, 0.8) for w in (0, 0.5, 1) for l in levels],
            "pulse": [base.pulse_scale(l, sp, t) for l in levels for sp in (True, False) for t in times],
        }
        got = run_js(f"""const L = {json.dumps(levels)}, T = {json.dumps(times)};
console.log(JSON.stringify({{
  light: L.flatMap(l => T.map(t => H.plateBrightness(l, t))),
  edge: L.map(l => H.gapEdge(20, 0.8, l)), lowerEdge: L.map(l => H.lowerEdge(50, 0.6, l)),
  side: [0,1,2,3,4].flatMap(d => L.map(l => H.gapSideLit(d, l))),
  lit: [18,19,20,21,22,23].flatMap(j => [19.2, 21.5, 22.9].map(e => H.gapLit(j, e) + H.lowerLit(j, e))),
  shift: [-1,-0.3,0.8].flatMap(u => [0,0.5,1].flatMap(w => L.map(l => H.cheekShift(u, w, l)))),
  pulse: L.flatMap(l => [true,false].flatMap(sp => T.map(t => H.pulseScale(l, sp, t)))),
}}));""")
        for key in want:
            assert len(got[key]) == len(want[key]), key
            for a, b in zip(got[key], want[key]):
                assert math.isclose(a, b, abs_tol=1e-9), (key, a, b)

    def test_only_the_loudest_voice_opens_the_gap_all_the_way(self):
        got = run_js("""console.log(JSON.stringify({
  rest: H.gapEdge(20, 1, 0), full: H.gapEdge(20, 1, 1), sideRest: H.gapSideLit(0, 0), sideFull: H.gapSideLit(0, 1), sideFar: H.gapSideLit(3, 1) }));""")
        assert got["rest"] == 20 and got["full"] == pytest.approx(17.4)          # the bar climbs 2.6 cells into the notch
        assert (got["sideRest"], got["sideFull"], got["sideFar"]) == (1, 0, 1)    # the nearest side plate goes dark, a far one stays lit


@needs_node
class TestGlitch:
    CLOCK = """let now = 100; const clock = () => now; const seq = [0.5, 0.1, 0.9, 0.3, 0.7, 0.2, 0.8, 0.6, 0.4];
let k = 0; const rng = () => seq[k++ % seq.length];"""

    def test_a_disabled_glitch_never_happens(self):
        assert run_js(f"""{self.CLOCK}
const g = new H.Glitch({{ enabled: false }}, rng, clock); now += 1000; console.log(JSON.stringify(g.update("IDLE", 58)));""") == [None, 1]

    def test_quiet_until_the_first_gap_then_a_soft_glitch_within_limits(self):
        got = run_js(f"""{self.CLOCK}
const g = new H.Glitch({{ idle_every_s: [3, 9], hard_every_s: [1000, 2000] }}, rng, clock);
const first = g.update("IDLE", 58); now += 50; const early = g.update("IDLE", 58);
now += 20; const seen = []; for (let i = 0; i < 30; i++) {{ now += 0.1; const [rows, fl] = g.update("IDLE", 58); if (rows) seen.push([Math.max(...rows.map(Math.abs)), rows.length, fl]); }}
console.log(JSON.stringify({{ first, early, seen }}));""")
        assert got["first"] == [None, 1] and got["early"] == [None, 1]
        assert got["seen"], "no soft glitch happened"
        for biggest, length, flicker in got["seen"]:
            assert biggest <= 3 and length == 58 and 0.85 <= flicker <= 1.0

    def test_a_hard_flicker_dims_the_head_and_shifts_further(self):
        got = run_js(f"""{self.CLOCK}
const g = new H.Glitch({{ hard_every_s: [1, 1], idle_every_s: [1000, 2000] }}, rng, clock);
now += 5; g.update("IDLE", 58); const seen = []; for (let i = 0; i < 60; i++) {{ now += 0.05; const [rows, fl] = g.update("IDLE", 58); if (rows) seen.push([Math.max(...rows.map(Math.abs)), fl]); }}
console.log(JSON.stringify(seen));""")
        assert got and all(b <= 9 for b, _ in got) and all(f <= 1.0 for _, f in got) and min(f for _, f in got) < 0.9
