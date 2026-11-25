"""How a colour is applied to a bulb, shared by the Qt colour picker (web/js/swatch-model.js has the same rule for the web picker).

One rule: a colour carries its own brightness. A Tapo bulb is told a hue, a saturation and, separately, a brightness, so how dark a mix such as
(0, 100, 0) is cannot be part of its hue: it is sent as the brightness, as the light a screen would give (the brightest channel as a fraction of
255, to the power `gamma`, the same correction that is applied to the colour). The colour itself is sent at full value, which is what every
bulb type understands (a WiZ bulb takes the rgb and its brightness separately too). The same colour is therefore always the same light; the brightness
slider is the bulb's real brightness and is never multiplied by anything."""
from typing import Dict, Sequence


def value_of(rgb: Sequence[int]) -> float:
    """0-1: the brightest channel as a fraction of 255 (how bright the colour is on a screen, before the gamma)."""
    return max(rgb) / 255.0


def value_brightness(rgb: Sequence[int], gamma: float = 1.0) -> int:
    """The brightness (1-100) a colour is as a light."""
    return max(1, min(100, int(round(100 * value_of(rgb) ** gamma))))


def colour_command(rgb: Sequence[int], gamma: float = 1.0) -> Dict[str, object]:
    """The light.set fields that make a bulb show `rgb`: the colour at full value and its brightness. Black has no colour: it is the dimmest
    light the bulb can give, in the colour it already has."""
    v = value_of(rgb)
    if v == 0:
        return {"lum": 1}
    return {"rgb": [min(255, int(round(c / v))) for c in rgb], "lum": value_brightness(rgb, gamma)}


def keep_value(full_rgb: Sequence[int], current: Sequence[int]) -> tuple:
    """A colour from the wheel (always full value) with the value of the colour it replaces: the wheel picks the hue and the saturation only, as in
    any HSV picker, so a dark swatch stays dark when its hue is changed (black, which has no value to keep, becomes full value)."""
    v = value_of(current) or 1.0
    return tuple(int(round(c * v)) for c in full_rgb)
