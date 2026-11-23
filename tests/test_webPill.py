"""The dock pills' hover label (web/js/pillmarquee.js under node) and the rules that keep it like the Qt pill."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQ = os.path.join(ROOT, "web", "js", "pillmarquee.js").replace("\\", "/")
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(body):
    script = f'import * as M from "file:///{MQ}";\n' + body
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@node
class TestLabelWidth:
    def test_it_opens_to_the_text_plus_two_pixels_so_rounding_never_clips_a_letter(self):
        assert run("console.log(M.labelWidth(80, 170))") == 82
        assert run("console.log(M.labelWidth(80.2, 170))") == 83

    def test_it_never_opens_wider_than_the_cap(self):
        assert run("console.log(M.labelWidth(400, 170))") == 170


@node
class TestMarqueeDecision:
    def test_only_a_text_longer_than_the_cap_scrolls(self):
        assert run("console.log(JSON.stringify([M.needsMarquee(80, 170), M.needsMarquee(171, 170), M.needsMarquee(172, 170), M.needsMarquee(400, 170)]))") == [False, False, True, True]


@node
class TestMarqueeFrames:
    def test_it_scrolls_one_pixel_per_forty_milliseconds_like_the_qt_label(self):
        f = run("console.log(JSON.stringify(M.marqueeFrames(300, 170)))")
        assert f["first"] == {"from": 0, "to": -300, "duration": 300 * 40}

    def test_then_it_comes_back_in_from_the_right_edge_of_the_box(self):
        f = run("console.log(JSON.stringify(M.marqueeFrames(300, 170)))")
        assert f["loop"] == {"from": 170, "to": -300, "duration": (170 + 300) * 40}

    def test_it_holds_still_for_eight_hundred_milliseconds_first(self):
        assert run("console.log(M.MARQUEE_DELAY_MS)") == 800


class TestLikeTheQtPill:
    css = read("web", "style.css")

    def test_the_opening_uses_the_qt_timing(self):
        # IconPill: a 260 ms ease-out animation after an 80 ms hover delay (so a pointer sweeping past opens nothing)
        assert "--open-ms: 260ms" in self.css and "--open-delay: 80ms" in self.css

    def test_the_label_opens_to_the_measured_width(self):
        assert re.search(r"\.pill:hover \.label[^{]*\{[^}]*max-width:\s*var\(--lw", self.css)

    def test_a_pill_has_no_native_hint(self):
        # the label opening is the hint; a title attribute would pop a second one up
        for name in ("main.js", "audiopills.js"):
            src = re.sub(r"//[^\n]*", "", read("web", "js", name))                # comments may say "title"
            assert not re.search(r'class:\s*"pill"[^)]*\btitle\s*:', src), name
            assert "pill.title" not in src, name


class TestAudioPairLayout:
    css = read("web", "style.css")

    def test_the_audio_pills_are_a_centred_pair_above_the_text_bar(self):
        rule = re.search(r"\.right-zone \{[^}]*\}", self.css).group(0)
        assert "grid-template-columns: 1fr 1fr" in rule
        assert re.search(r"top:\s*calc\(-\d+ \* var\(--u\)\)", rule)          # above the bar, not beside it

    def test_the_mic_opens_to_the_left_and_the_speaker_to_the_right(self):
        assert re.search(r"\.right-zone > :first-child \{ justify-self: end; \}", self.css)
        assert re.search(r"\.right-zone > :last-child \{ justify-self: start; \}", self.css)

    def test_the_lyrics_sit_clear_above_the_pair(self):
        # the pair's top is 54 + 44 = 98 design px above the bar's top, 84 px above the screen bottom: the lyrics' centre line is higher than before (205)
        bottom = int(re.search(r"#lyrics \{[^}]*bottom: calc\((\d+) \* var\(--u\)\)", self.css).group(1))
        assert bottom > 205
