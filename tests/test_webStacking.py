import os
import re

CSS = open(os.path.join(os.path.dirname(__file__), "..", "web", "style.css"), encoding="utf-8").read()


def z(selector):
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert rule, f"no rule for {selector}"
    m = re.search(r"z-index:\s*(\d+)", rule.group(1))
    return int(m.group(1)) if m else None


def test_windows_are_above_the_lyrics_and_below_the_dock():
    """#widgets is position: fixed, so it is its own stacking context: the z-index of each window inside it
    (ui.js zTop, from 11) never competes with the lyrics. Without a z-index of its own, #widgets sat at level 0 and the
    lyrics (z-index 5) were painted over every window, the To-Do list included."""
    assert z("#lyrics") < z("#widgets") < z("#dock")
