"""Colour skins for the dashboard: the whole UI is written in the JARVIS orange palette, and a skin recolours it.

`settings.theme` in core.json picks the skin ("jarvis" = the colours as written, nothing changes; "ultron" = red and
black). Rather than a second copy of every colour, the skin is a deterministic mapping of the colours the code already
uses, applied in three places: `Theme`'s constants, every widget stylesheet (clUIScalerInjector), and painted `QColor`s
(clSkinQt). Pure Python, no Qt, so every process (the tray icon too) can use it.

The mapping only touches the warm family the UI is built from; greens, blues, whites, greys and brand colours (Spotify
green, Discord blue ...) pass through untouched. It is idempotent -- a colour that was already skinned maps to itself --
because a stylesheet built from `Theme` constants is recoloured again when a widget sets it.

    accent  (saturated orange/amber, hue 10-60)   -> crimson; the more yellow it was, the lighter and hotter it gets
    cream / warm grey text and fills               -> cool brushed steel
    dark brown backgrounds                         -> near-black with a cold tint
"""
import colorsys
import json
import os
import re
from typing import Callable, Tuple

SKINS = ("jarvis", "ultron")

_CORE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "core.json")

_HEX = re.compile(r"(?<![\w])#([0-9a-fA-F]{6})\b")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+%?)\s*)?\)")


def resolve_skin(settings=None) -> str:
    """The skin for a settings dict: JARVIS_THEME overrides settings.theme, anything unknown is jarvis. Pure, so a service that
    already loaded core.json (clTTS) gets the same answer as the UI without reading the file again."""
    name = os.environ.get("JARVIS_THEME") or (settings or {}).get("theme", "jarvis")
    return name if name in SKINS else "jarvis"


def _load_skin() -> str:
    try:
        with open(_CORE_JSON, "r", encoding="utf-8") as f:
            settings = json.load(f).get("settings", {})
    except Exception:
        settings = {}
    return resolve_skin(settings)


ACTIVE = _load_skin()


# Red holds about half the luminance of amber, so a translucent orange tint (borders, panel fills, glows) turned
# into the same-alpha red reads as dark maroon on black. A recoloured accent therefore gets a more opaque alpha.
# Only accents are boosted, and their output is red (never an accent again), so recolouring twice does not boost twice.
ACCENT_ALPHA_BOOST = 1.8


def is_accent(r: int, g: int, b: int) -> bool:
    """True for the colours `ultron_rgb` turns into crimson (saturated orange/amber, not dark brown)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return s >= 0.06 and v >= 0.28 and 10.0 <= h * 360.0 <= 60.0 and s >= 0.59


def boost_alpha(alpha: float, maximum: float) -> float:
    return min(maximum, alpha * ACCENT_ALPHA_BOOST)


def ultron_rgb(r: int, g: int, b: int) -> Tuple[int, int, int]:
    """The Ultron mapping for one colour (see the module docstring)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue = h * 360.0
    if s < 0.06:                                            # white, grey, black
        return r, g, b
    if v < 0.28 and (hue <= 60.0 or hue >= 345.0):          # dark brown backgrounds -> cold near-black
        out = colorsys.hsv_to_rgb(235.0 / 360.0, 0.18, 0.03 + 0.6 * v)
    elif 10.0 <= hue <= 60.0 and s >= 0.59:                 # orange / amber accent -> crimson, hotter as it was yellower
        t = (hue - 10.0) / 50.0
        out = colorsys.hsv_to_rgb(((355.0 + 13.0 * t) % 360.0) / 360.0, 1.0 - 0.28 * t, v)
    elif 10.0 <= hue <= 60.0:                               # cream / warm grey -> steel
        out = colorsys.hsv_to_rgb(215.0 / 360.0, min(0.10, s * 0.5), v * 0.92)
    else:                                                   # reds, greens, blues ...: already not part of the warm family
        return r, g, b
    return tuple(int(round(c * 255)) for c in out)


def _mapper(skin: str) -> Callable[[int, int, int], Tuple[int, int, int]]:
    return ultron_rgb if skin == "ultron" else (lambda r, g, b: (r, g, b))


def recolor_css(css: str, skin: str = None) -> str:
    """Recolours every #rrggbb / rgb() / rgba() in a stylesheet or colour string."""
    skin = skin or ACTIVE
    if skin == "jarvis" or not css:
        return css
    fn = _mapper(skin)

    def hexsub(m: "re.Match") -> str:
        r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        return "#%02x%02x%02x" % fn(r, g, b)

    def rgbsub(m: "re.Match") -> str:
        r0, g0, b0 = min(255, int(m.group(1))), min(255, int(m.group(2))), min(255, int(m.group(3)))
        r, g, b = fn(r0, g0, b0)
        alpha = m.group(4)
        if alpha is None:
            return f"rgb({r}, {g}, {b})"
        if skin == "ultron" and is_accent(r0, g0, b0):
            if alpha.endswith("%"):
                alpha = f"{boost_alpha(float(alpha[:-1]), 100.0):.0f}%"
            elif "." in alpha:                                     # 0-1 float alpha
                alpha = f"{boost_alpha(float(alpha), 1.0):.2f}"
            else:                                                  # 0-255 integer alpha
                alpha = str(int(round(boost_alpha(float(alpha), 255.0))))
        return f"rgba({r}, {g}, {b}, {alpha})"

    return _RGB.sub(rgbsub, _HEX.sub(hexsub, css))


def recolor_rgb(r: int, g: int, b: int, skin: str = None) -> Tuple[int, int, int]:
    return _mapper(skin or ACTIVE)(r, g, b)
