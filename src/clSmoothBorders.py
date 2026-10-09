"""Anti-aliased borders for rounded stylesheet widgets.

Qt draws a stylesheet `border` on a rounded widget from clipped arc segments: at the radii this UI
uses (6-15 px) corners come out chamfered and lopsided (worst pixel ~135/255 off an ideal corner,
vs ~13 for a fill with no border, which Qt does well). Nothing in QSS fixes that.

This module keeps Qt for everything else (fill, hover/checked states, layout, text) and only takes
over the border: for a widget whose cascade gives it a radius and a solid border, the stylesheet
handed to Qt gets an override that makes the border colour transparent (same width, so the box
model is unchanged), and the widget's paintEvent paints the border afterwards as one filled ring
path with antialiasing. A semi-transparent border composites over the fill exactly as in CSS.

Hooked from clUIScalerInjector (every QWidget.setStyleSheet) plus QPushButton/QLineEdit creation,
which the global stylesheet styles without any per-widget sheet.
"""
import itertools
import os
import re
import weakref
from functools import lru_cache

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPolygonF
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLineEdit, QPushButton

# Escape hatch: JARVIS_SMOOTH_BORDERS=0 leaves every border to Qt (for A/B comparisons).
ENABLED = os.environ.get("JARVIS_SMOOTH_BORDERS", "1") != "0"

SIDES = ("top", "right", "bottom", "left")
CORNERS = ("top-left", "top-right", "bottom-right", "bottom-left")
MARK = "/*smooth-borders*/"
PSEUDOS = {"hover", "pressed", "checked", "unchecked", "disabled", "enabled", "focus"}
# State sets checked when deciding whether a widget needs the overlay at all.
_PROBE_STATES = [frozenset(), frozenset({"hover"}), frozenset({"pressed"}), frozenset({"checked"}),
                 frozenset({"disabled"}), frozenset({"focus"}), frozenset({"hover", "checked"})]
_names = itertools.count(1)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_BLOCK = re.compile(r"([^{}]*)\{([^{}]*)\}")
_OVERRIDE_BLOCK = re.compile(re.escape(MARK) + r"\s*[^{}]*\{[^{}]*\}")


# --- tiny QSS parser -------------------------------------------------------------------------

def _split_top(text, sep):
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _decls(body):
    out = []
    for item in _split_top(body, ";"):
        if ":" in item:
            prop, _, val = item.partition(":")
            out.append((prop.strip().lower(), val.strip()))
    return out


class _Selector:
    __slots__ = ("type", "id", "pseudos", "spec")

    def __init__(self, type_, id_, pseudos):
        self.type, self.id, self.pseudos = type_, id_, pseudos
        self.spec = (1 if id_ else 0, len(pseudos), 1 if type_ else 0)


def _parse_selector(text):
    """Compound selectors only (Type#id:pseudo); anything fancier returns None and is ignored."""
    text = text.strip()
    if not text:
        return _Selector(None, None, ())
    m = re.fullmatch(r"(\*|[A-Za-z_][\w]*)?(?:#([\w-]+))?((?::[a-z]+)*)", text)
    if not m:
        return None
    type_, id_, pseudo_text = m.groups()
    pseudos = tuple(p for p in pseudo_text.split(":") if p)
    if any(p not in PSEUDOS for p in pseudos):
        return None
    return _Selector(None if type_ in (None, "*") else type_, id_, pseudos)


@lru_cache(maxsize=512)
def parse_css(css):
    """-> tuple of (selector, decls, order). A sheet with no braces is bare declarations (applies to
    the widget and its descendants, like `*`)."""
    css = _COMMENT.sub("", css)
    if "{" not in css:
        return ((_Selector(None, None, ()), tuple(_decls(css)), 0),)
    rules, order = [], 0
    for m in _BLOCK.finditer(css):
        body = tuple(_decls(m.group(2)))
        for sel_text in _split_top(m.group(1), ","):
            sel = _parse_selector(sel_text)
            if sel is not None:
                rules.append((sel, body, order))
            order += 1
    return tuple(rules)


def _px(token):
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(px|pt)?", token.strip())
    if not m:
        return None
    v = float(m.group(1))
    return v * 4 / 3 if m.group(2) == "pt" else v


def parse_color(text):
    text = text.strip()
    if not text or "gradient" in text or "(" in text and not text.lower().startswith(("rgb", "hsl")):
        return None
    m = re.fullmatch(r"rgba?\(([^)]*)\)", text, re.I)
    if m:
        p = [x.strip() for x in m.group(1).split(",")]
        try:
            r, g, b = (int(float(x)) for x in p[:3])
            a = 255
            if len(p) > 3:
                a = float(p[3].rstrip("%")) * 2.55 if p[3].endswith("%") else (float(p[3]) * 255 if "." in p[3] else float(p[3]))
            return QColor(r, g, b, max(0, min(255, round(a))))
        except ValueError:
            return None
    c = QColor(text)
    return c if c.isValid() else None


class _Style:
    """Cascaded border/radius for one widget in one state."""
    __slots__ = ("radii", "borders", "margin", "ok")

    def __init__(self):
        self.radii = [0.0] * 4                       # tl, tr, br, bl
        self.borders = {s: [0.0, "none", None] for s in SIDES}   # width, style, QColor
        self.margin = False
        self.ok = True

    def visible(self):
        return {s: (w, c) for s, (w, st, c) in self.borders.items() if w > 0 and st == "solid" and c is not None and c.alpha() > 0}


def _border_value(val):
    """'1px solid <color>' -> (width, style, color|None); 'none' -> (0, 'none', None)."""
    val = val.strip()
    if val.lower() in ("none", "0", "0px"):
        return 0.0, "none", None
    width, style, color = None, "solid", None
    for tok in re.findall(r"[a-zA-Z]+\([^)]*\)|\S+", val):
        if _px(tok) is not None and width is None:
            width = _px(tok)
        elif tok.lower() in ("solid", "dashed", "dotted", "double", "none", "outset", "inset", "ridge", "groove"):
            style = tok.lower()
        else:
            color = parse_color(tok)
            if color is None:
                return None
    return (width if width is not None else 0.0), style, color


def _apply(style, prop, val):
    if prop == "border":
        b = _border_value(val)
        if b is None:
            style.ok = False
            return
        for s in SIDES:
            style.borders[s] = list(b)
    elif prop in ("border-top", "border-right", "border-bottom", "border-left"):
        b = _border_value(val)
        if b is None:
            style.ok = False
            return
        style.borders[prop[7:]] = list(b)
    elif prop == "border-width":
        w = [_px(t) for t in val.split()]
        if None in w or len(w) not in (1, 2, 4):
            style.ok = False
            return
        w = {1: w * 4, 2: w * 2, 4: w}[len(w)]
        for s, x in zip(SIDES, w):
            style.borders[s][0] = x
    elif prop == "border-color":
        cols = [parse_color(t) for t in re.findall(r"[a-zA-Z]+\([^)]*\)|\S+", val)]
        if None in cols or len(cols) not in (1, 2, 4):
            style.ok = False
            return
        cols = {1: cols * 4, 2: cols * 2, 4: cols}[len(cols)]
        for s, c in zip(SIDES, cols):
            style.borders[s][2] = c
    elif prop == "border-style":
        for s in SIDES:
            style.borders[s][1] = val.split()[0].lower()
    elif prop == "border-radius":
        r = [_px(t) for t in val.split("/")[0].split()]
        if None in r or len(r) not in (1, 2, 3, 4):
            style.ok = False
            return
        if len(r) == 1:
            r = r * 4
        elif len(r) == 2:
            r = [r[0], r[1], r[0], r[1]]
        elif len(r) == 3:
            r = [r[0], r[1], r[2], r[1]]
        style.radii = list(map(float, r))
    elif prop.startswith("border-") and prop.endswith("-radius"):
        corner = prop[7:-7]
        if corner in CORNERS and _px(val) is not None:
            style.radii[CORNERS.index(corner)] = _px(val)
        else:
            style.ok = False
    elif prop == "margin" and any((_px(t) or 0) for t in val.split()):
        style.margin = True


def _states_match(pseudos, active):
    for p in pseudos:
        if p == "enabled":
            if "disabled" in active:
                return False
        elif p == "unchecked":
            if "checked" in active:
                return False
        elif p not in active:
            return False
    return True


def _class_names(cls):
    return {c.__name__ for c in cls.__mro__}


def resolve(layers, names, object_name, active):
    """layers: sheets from lowest precedence (application) to highest (the widget itself)."""
    matched = []
    for layer, css in enumerate(layers):
        for sel, body, order in parse_css(css):
            if sel.type and sel.type not in names:
                continue
            if sel.id and sel.id != object_name:
                continue
            if not _states_match(sel.pseudos, active):
                continue
            matched.append(((layer, sel.spec, order), body))
    matched.sort(key=lambda m: m[0])
    style = _Style()
    for _, body in matched:
        for prop, val in body:
            _apply(style, prop, val)
    return style


# --- painting --------------------------------------------------------------------------------

def _rr_path(rect, corners):
    """Rounded rect with per-corner (rx, ry), tl/tr/br/bl."""
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = corners
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    p = QPainterPath()
    p.moveTo(x0 + tlx, y0)
    p.lineTo(x1 - trx, y0)
    if trx and try_:
        p.arcTo(QRectF(x1 - 2 * trx, y0, 2 * trx, 2 * try_), 90, -90)
    p.lineTo(x1, y1 - bry)
    if brx and bry:
        p.arcTo(QRectF(x1 - 2 * brx, y1 - 2 * bry, 2 * brx, 2 * bry), 0, -90)
    p.lineTo(x0 + blx, y1)
    if blx and bly:
        p.arcTo(QRectF(x0, y1 - 2 * bly, 2 * blx, 2 * bly), 270, -90)
    p.lineTo(x0, y0 + tly)
    if tlx and tly:
        p.arcTo(QRectF(x0, y0, 2 * tlx, 2 * tly), 180, -90)
    p.closeSubpath()
    return p


def _clamp_radii(radii, w, h):
    tl, tr, br, bl = radii
    f = 1.0
    for total, size in ((tl + tr, w), (bl + br, w), (tl + bl, h), (tr + br, h)):
        if total > size and total > 0:
            f = min(f, size / total)
    return [r * f for r in radii]


def ring_paths(rect, radii, widths):
    """(outer path, inner path, outer corner points, inner corner points) for a border box."""
    tl, tr, br, bl = _clamp_radii(radii, rect.width(), rect.height())
    wt, wr, wb, wl = (widths[s] for s in SIDES)
    outer = _rr_path(rect, [(tl, tl), (tr, tr), (br, br), (bl, bl)])
    inner_rect = rect.adjusted(wl, wt, -wr, -wb)
    inner = QPainterPath()
    if inner_rect.width() > 0 and inner_rect.height() > 0:
        inner = _rr_path(inner_rect, [(max(0, tl - wl), max(0, tl - wt)), (max(0, tr - wr), max(0, tr - wt)),
                                      (max(0, br - wr), max(0, br - wb)), (max(0, bl - wl), max(0, bl - wb))])
    oc = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
    ic = [inner_rect.topLeft(), inner_rect.topRight(), inner_rect.bottomRight(), inner_rect.bottomLeft()]
    return outer, inner, oc, ic


def paint_border(painter, rect, radii, borders):
    """borders: {side: (width, QColor)} for visible sides only. Draws the ring with antialiasing;
    sides of different colours are cut along the usual CSS corner diagonals."""
    if not borders:
        return
    widths = {s: (borders[s][0] if s in borders else 0.0) for s in SIDES}
    outer, inner, oc, ic = ring_paths(rect, radii, widths)
    ring = QPainterPath()
    ring.setFillRule(Qt.FillRule.OddEvenFill)
    ring.addPath(outer)
    ring.addPath(inner)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    colours = {}
    for s, (_, c) in borders.items():
        colours.setdefault(c.rgba(), (c, []))[1].append(s)
    if len(colours) == 1:
        painter.fillPath(ring, next(iter(colours.values()))[0])
    else:
        wedge_pts = {"top": (oc[0], oc[1], ic[1], ic[0]), "right": (oc[1], oc[2], ic[2], ic[1]),
                     "bottom": (oc[2], oc[3], ic[3], ic[2]), "left": (oc[3], oc[0], ic[0], ic[3])}
        for colour, sides in colours.values():
            wedge = QPainterPath()
            wedge.setFillRule(Qt.FillRule.WindingFill)
            for s in sides:
                wedge.addPolygon(QPolygonF([QPointF(p) for p in wedge_pts[s]]))
                wedge.closeSubpath()
            painter.fillPath(ring.intersected(wedge), colour)
    painter.restore()


# --- widget integration ----------------------------------------------------------------------

_OVERRIDE_STATES = ("hover", "pressed", "checked", "disabled", "focus")


def _override_css(name):
    # `QWidget#name` ties on specificity with the usual `Type#name` / `#name` rules in the widget's
    # own sheet and comes last, so it wins; one selector per state (and pair of states) so the
    # `:hover` / `:checked` rules, which are more specific, are covered too. (Repeating the id, or
    # an attribute selector, does not raise Qt's specificity.) A widget's own sheet already
    # outranks its ancestors'.
    base = f"QWidget#{name}"
    sels = [base] + [f"{base}:{s}" for s in _OVERRIDE_STATES]
    sels += [f"{base}:{a}:{b}" for i, a in enumerate(_OVERRIDE_STATES) for b in _OVERRIDE_STATES[i + 1:]]
    return f"\n{MARK}\n{', '.join(sels)} {{ border-color: transparent; }}"


class _SmoothBorder:
    def __init__(self, widget):
        self.ref = weakref.ref(widget)
        self.names = _class_names(type(widget))

    @staticmethod
    def _layers(widget):
        chain = []
        p = widget.parentWidget()
        while p is not None:
            chain.append(getattr(p, "_sb_css", "") or "")
            p = p.parentWidget()
        chain.reverse()
        app = QApplication.instance()
        return (app.styleSheet() if app else "",) + tuple(chain) + (getattr(widget, "_sb_css", "") or "",)

    @staticmethod
    def _states(widget):
        s = set()
        if not widget.isEnabled():
            s.add("disabled")
        else:
            if widget.underMouse():
                s.add("hover")
            if widget.hasFocus():
                s.add("focus")
        if isinstance(widget, QAbstractButton):
            if widget.isDown():
                s.add("pressed")
            if widget.isCheckable() and widget.isChecked():
                s.add("checked")
        return frozenset(s)

    def wants_overlay(self, widget):
        layers = self._layers(widget)
        for st in _PROBE_STATES:
            style = resolve(layers, self.names, widget.objectName(), st)
            if style.ok and not style.margin and style.visible() and any(r > 0 for r in style.radii):
                return True
        return False

    def paint(self, widget):
        style = resolve(self._layers(widget), self.names, widget.objectName(), self._states(widget))
        if not style.ok or style.margin:
            return
        borders = style.visible()
        if borders:
            p = QPainter(widget)
            paint_border(p, QRectF(widget.rect()), style.radii, borders)
            p.end()


def _install_paint(widget):
    if widget.__dict__.get("_sb_paint_installed"):
        return
    orig = type(widget).paintEvent
    ref = weakref.ref(widget)

    def paintEvent(event):
        w = ref()
        if w is None:
            return
        orig(w, event)
        info = w.__dict__.get("_sb")
        if info is not None:
            info.paint(w)

    widget.paintEvent = paintEvent
    widget._sb_paint_installed = True


def process(widget, css):
    """Called with the (already scaled) sheet being set on `widget`; returns the sheet for Qt."""
    css = _OVERRIDE_BLOCK.sub("", css)   # drop a block we added earlier (callers re-read styleSheet())
    if not ENABLED:
        return css
    widget._sb_css = css
    if not isinstance(widget, (QPushButton, QLineEdit)) and not css.strip():
        widget.__dict__.pop("_sb", None)
        return css
    info = _SmoothBorder(widget)
    if not info.wants_overlay(widget):
        widget.__dict__.pop("_sb", None)
        return css
    if not widget.objectName():
        widget.setObjectName(f"sb{next(_names)}")
    widget._sb = info
    _install_paint(widget)
    return css + _override_css(widget.objectName())


def attach_default(widget, orig_set_style_sheet):
    """Widgets the global stylesheet styles (no per-widget sheet): give them the same treatment."""
    css = process(widget, "")
    if css:
        orig_set_style_sheet(widget, css)


def install(orig_set_style_sheet):
    if not ENABLED:
        return
    for cls in (QPushButton, QLineEdit):
        orig_init = cls.__init__

        def __init__(self, *args, _orig=orig_init, **kwargs):
            _orig(self, *args, **kwargs)
            attach_default(self, orig_set_style_sheet)

        cls.__init__ = __init__
