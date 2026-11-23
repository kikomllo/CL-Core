"""The web dashboard's skin: the stylesheet is recoloured on the server with clSkin, and colours built in JS use a port of
the same mapping (web/js/skin.js). These tests keep the two in step and check the route."""
import json
import os
import shutil
import subprocess
import sys

import pytest
import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import clSkin  # noqa: E402

SKIN_JS = os.path.join(ROOT, "web", "js", "skin.js").replace("\\", "/")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# the colours the dashboard is written in, plus greys, greens, blues, dark browns and in-betweens
SAMPLES = [(255, 170, 0), (255, 119, 0), (255, 204, 0), (255, 230, 204), (255, 244, 230), (168, 154, 140), (27, 13, 3), (12, 4, 0),
           (35, 17, 4), (136, 255, 136), (29, 185, 84), (88, 101, 242), (255, 255, 255), (0, 0, 0), (128, 128, 128), (230, 92, 0),
           (255, 140, 0), (200, 90, 20), (90, 40, 10), (255, 105, 180), (255, 0, 0), (0, 255, 255), (201, 188, 171)]


def run_js(body):
    script = ('globalThis.document = { documentElement: { dataset: {} } }; globalThis.window = { dispatchEvent() {} };\n'
              f'const S = await import("file:///{SKIN_JS}");\n{body}')
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
class TestJsMatchesPython:
    def test_the_colour_mapping_agrees(self):
        got = run_js(f"console.log(JSON.stringify({json.dumps(SAMPLES)}.map(([r,g,b]) => S.ultronRgb(r,g,b))));")
        for src, out in zip(SAMPLES, got):
            want = clSkin.ultron_rgb(*src)
            assert all(abs(a - b) <= 1 for a, b in zip(out, want)), (src, out, want)      # +-1: Python rounds half to even

    def test_the_accent_test_agrees(self):
        got = run_js(f"console.log(JSON.stringify({json.dumps(SAMPLES)}.map(([r,g,b]) => S.isAccent(r,g,b))));")
        assert got == [clSkin.is_accent(*c) for c in SAMPLES]

    @pytest.mark.parametrize("css", [
        "0 0 10px rgba(255,140,0,0.55), 0 0 3px rgba(255,170,0,0.4)",
        "color: #ffaa00; background: #1b0d03; border: 1px solid rgba(255, 150, 0, .32)",
        "rgba(136,255,136,.5) #88ff88 rgb(255, 230, 204)",
    ])
    def test_css_strings_are_recoloured_alike(self, css):
        got = run_js(f'S.setSkin("ultron"); console.log(JSON.stringify(S.skinCss({json.dumps(css)})));')
        want = clSkin.recolor_css(css, "ultron")
        norm = lambda t: __import__("re").sub(r"\s+", "", t.lower())            # spacing and 0.55 vs .55 differ only in text
        nums = lambda t: [round(float(x), 2) for x in __import__("re").findall(r"\d*\.?\d+", t)]
        assert nums(norm(got)) == nums(norm(want)) or all(abs(a - b) <= 1 for a, b in zip(nums(got), nums(want)))

    def test_the_default_skin_changes_nothing(self):
        assert run_js('S.setSkin("jarvis"); console.log(JSON.stringify(S.skinCss("rgba(255,170,0,.9)")));') == "rgba(255,170,0,.9)"
        assert run_js('S.setSkin("nonsense"); console.log(JSON.stringify(S.activeSkin()));') == "jarvis"

    def test_the_ultron_orb_palette_has_no_orange_left(self):
        waves = run_js('S.setSkin("ultron"); console.log(JSON.stringify(S.palette().waves));')
        for r, g, b, a in waves:
            assert r > g and r > b and g < 60, (r, g, b)                         # crimson, not amber
            assert 0 < a <= 255


@pytest.mark.asyncio
class TestStylesheetRoute:
    @pytest_asyncio.fixture
    async def client(self):
        from aiohttp.test_utils import TestClient, TestServer
        import clWebUI
        async with TestClient(TestServer(clWebUI.build_app(None))) as c:
            yield c

    async def test_the_jarvis_skin_serves_the_file_as_it_is(self, client, monkeypatch):
        import clWebUI
        monkeypatch.setattr(clWebUI, "active_skin", lambda: "jarvis")
        r = await client.get("/style.css")
        assert r.status == 200 and r.content_type == "text/css"
        with open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8") as f:
            assert await r.text() == f.read()

    async def test_the_ultron_skin_recolours_the_orange(self, client, monkeypatch):
        import clWebUI
        monkeypatch.setattr(clWebUI, "active_skin", lambda: "ultron")
        css = await (await client.get("/style.css")).text()
        assert "--accent: #ffaa00" not in css and "--accent:" in css
        accent = [l for l in css.splitlines() if l.strip().startswith("--accent:")][0]
        r, g, b = (int(accent.split("#")[1][i:i + 2], 16) for i in (0, 2, 4))
        assert r > 150 and g < 90                                                # crimson
        assert "JetBrains Mono" in css                                           # nothing but colours changed
