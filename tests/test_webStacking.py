import os
from webcss import web_css
import re

CSS = web_css()


def z(selector):
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert rule, f"no rule for {selector}"
    m = re.search(r"z-index:\s*(\d+)", rule.group(1))
    return int(m.group(1)) if m else None


def test_windows_are_above_the_dock_and_the_lyrics():
    """#widgets is position: fixed, so it is its own stacking context: the z-index of each window inside it
    (ui.js zTop, from 11) never competes with the lyrics. Without a z-index of its own, #widgets sat at level 0 and the
    lyrics (z-index 5) were painted over every window, the To-Do list included. The dock (the pills and the command bar)
    is under the windows too: it was above them, so a window dragged low had its switches and buttons covered by the
    pill row and the command bar, which took the clicks."""
    assert z("#lyrics") < z("#dock") < z("#widgets")
