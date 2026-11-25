"""Colour rules shared by the light service and the Qt colour picker (web/js/swatch-model.js has the same one for the web picker)."""
from typing import Optional, Sequence


def dark_brightness(rgb: Sequence[int], gamma: float = 1.0) -> Optional[int]:
    """The brightness (1-100) a colour that is not at full value asks for, or None when it is at full value.

    A Tapo bulb is told a hue and a saturation and, separately, a brightness: the darkness of a mix such as (0, 100, 0) is not part of the
    hue, so it goes into the brightness instead, as the light the screen would give: the brightest channel as a fraction of 255, to the
    power `gamma` (the same correction that is applied to the colour). A colour at full value (every wheel colour and every default swatch)
    asks for nothing and keeps the brightness the user set."""
    v = (max(rgb) / 255.0) ** gamma
    if v >= 0.995:
        return None
    return max(1, min(100, int(round(100 * v))))
