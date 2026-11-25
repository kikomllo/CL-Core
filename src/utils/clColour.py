"""Colour rules shared by the light service and the Qt colour picker (web/js/swatch-model.js has the same ones for the web picker).

A Tapo bulb is told a hue, a saturation and, separately, a brightness. The darkness of a mix such as (0, 100, 0) is not part of its hue, so
it goes into the brightness: the brightness slider is the overall brightness, and the colour's own value (its brightest channel as a fraction of
255, to the power `gamma`, the light a screen would give) scales it. A bright colour is therefore a brighter light than a dark one, and the
slider itself is never overwritten by a colour."""
from typing import Optional, Sequence

FULL = 0.995                                        # a value this close to 1 is full value: nothing to scale


def value_factor(rgb: Sequence[int], gamma: float = 1.0) -> float:
    """0-1: how much of full brightness a colour is (1 for every wheel colour and default swatch)."""
    return (max(rgb) / 255.0) ** gamma


def compose_brightness(master: int, factor: float) -> int:
    """The brightness (1-100) to send for an overall brightness `master` and a colour's `factor`."""
    return master if factor >= FULL else max(1, min(100, int(round(master * factor))))


def brightness_for(rgb: Sequence[int], lum: Optional[int], gamma: float = 1.0) -> Optional[int]:
    """The brightness to send with an explicit colour: `lum` (100 when none was given) scaled by the colour's value, or `lum` untouched for a colour
    at full value (None stays None: nothing to say about the brightness)."""
    factor = value_factor(rgb, gamma)
    if factor >= FULL:
        return lum
    return compose_brightness(100 if lum is None else lum, factor)
