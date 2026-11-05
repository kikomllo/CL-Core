"""`QColor` that follows the skin (see clSkin.py). Import it instead of PyQt6.QtGui.QColor wherever the code paints
with colours written in the JARVIS palette:  `from clSkinQt import QColor`.

With the "jarvis" skin this *is* PyQt6's QColor, so the default look and behaviour are untouched. With another skin
the integer / "#rrggbb" constructors are recoloured; copies, Qt.GlobalColor, QRgb and named colours pass through."""
from PyQt6.QtGui import QColor as _QColor

import clSkin

if clSkin.ACTIVE == "jarvis":
    QColor = _QColor
else:
    class QColor(_QColor):
        def __init__(self, *args):
            if len(args) in (3, 4) and all(isinstance(a, int) and not isinstance(a, bool) for a in args):
                r0, g0, b0 = (min(255, max(0, a)) for a in args[:3])
                r, g, b = clSkin.recolor_rgb(r0, g0, b0)
                alpha = args[3:]
                if alpha and clSkin.is_accent(r0, g0, b0):             # red is dimmer than amber: see clSkin.ACCENT_ALPHA_BOOST
                    alpha = (int(round(clSkin.boost_alpha(alpha[0], 255))),)
                super().__init__(r, g, b, *alpha)
            elif len(args) == 1 and isinstance(args[0], str) and args[0].startswith("#") and len(args[0]) == 7:
                super().__init__(clSkin.recolor_css(args[0]))
            else:
                super().__init__(*args)

__all__ = ["QColor"]
