// The Ultron head ("gap" head, the Qt default): a red dot-matrix head that replaces the orb under the Ultron skin. A port of
// src/ui/clUltronHeadGap.py (+ the shared parts of clUltronHead.py). The geometry (which cell is metal, grille plate, eye, cheek,
// gap ...) is computed by the Qt code and exported to assets/themes/ultron/head/gap_head.json (src/utils/clWebHeadExport.py);
// this file only draws and animates it. Idle shows the full dim mouth; as he talks the lit mouth grows both ways (the bar under the
// dark notch climbs into it, the lower lip drops, the notch widens), the grille brightens a little, the lower face swells
// sideways and a seam inside the jawline lights up. Every cell is strictly lit or dark (pixel art); the outline never moves.
// Colours are raw on purpose (already the Ultron palette: the skin's recolouring would turn the dim reds near-black).
// Pure maths is exported separately (tests/test_webHead.py checks it against the Python).

export const C = {
  GAP_REST: 0.0, GAP_RISE: 2.6, GAP_DROP: 2.6, GAP_WIDEN: 3.0, PLATE_GAIN: 0.3, CHEEK_BULGE: 0.6,
  DIM_METAL: [255, 20, 26], PLATE_HOT: [255, 52, 52], EYE_CORE: [255, 175, 160], HALO_PEAK_ALPHA: 80,
  PULSE_QUIET: 0.94, PULSE_LOUD: 1.14, BREATH: 0.012,
};
export const GLITCH_DEFAULTS = {
  enabled: true, idle_every_s: [3.0, 9.0], speaking_every_s: [6.0, 15.0], processing_every_s: [0.7, 2.0],
  soft_ms: [110, 320], soft_shift: 3, hard_every_s: [15.0, 45.0], hard_ms: [500, 1500], hard_shift: 9,
};

// ---------------- the brightness and movement model (pure) ----------------
/** The old JARVIS glow: a light resting at `rest` (slowly breathing) that gets brighter with the voice (`gain` < 1: only a little). */
export const light = (level, t, rest, gain = 1) => {
  const idle = rest + 0.06 * Math.sin(t * 1.3);
  return Math.min(1, idle + (1 - idle) * gain * Math.max(0, level) ** 0.85);
};
export const plateBrightness = (level, t) => light(level, t, 0.55, C.PLATE_GAIN);   // the grille barely changes: it is the gap that opens
export const browBrightness = (level, t) => light(level, t, 0.5);
export const seamBrightness = (level, t) => light(level, t, 0.26);                  // the cheek lights inside the jawline
/** Where (rows) the bar under the centre gap begins: higher as the voice rises, most in the middle (taper 1). */
export const gapEdge = (top, taper, level) => top + (C.GAP_REST - C.GAP_RISE * Math.max(0, level)) * taper;
export const gapLit = (j, edge) => ((j + 0.5) >= edge ? 1 : 0);                      // a hard step: pixel art
/** Where the lit mouth ends under a grille column: lower as the voice rises, the reverse of gapEdge. */
export const lowerEdge = (bottom, taper, level) => bottom + 1 + C.GAP_DROP * Math.max(0, level) * taper;
export const lowerLit = (j, edge) => ((j + 0.5) <= edge ? 1 : 0);
/** A side plate `depth` cells from the notch edge: lit or dark, the gap widening by GAP_WIDEN cells at full voice. */
export const gapSideLit = (depth, level) => ((depth + 0.5) >= C.GAP_WIDEN * Math.max(0, level) ? 1 : 0);
export const cheekShift = (u, weight, level) => u * weight * C.CHEEK_BULGE * Math.max(0, level);
/** How big the whole head is: at rest it only breathes; while he speaks it grows and shrinks with the voice. */
export const pulseScale = (level, speaking, t) =>
  speaking ? C.PULSE_QUIET + (C.PULSE_LOUD - C.PULSE_QUIET) * Math.min(1, Math.max(0, level)) ** 0.85 : 1 + C.BREATH * Math.sin(t * 1.3);

// ---------------- glitch (time based: the same at 8 or 60 fps) ----------------
export function newGlitch(heightCells, rng, hard = false, maxShift = null) {
  const limit = maxShift ?? (hard ? 9 : 3), rows = new Array(heightCells).fill(0);
  const int = (a, b) => a + Math.floor(rng() * (b - a + 1));
  const bands = hard ? int(6, 12) : int(2, 4);
  for (let n = 0; n < bands; n++) {
    const start = int(0, Math.max(0, heightCells - 4)), size = int(2, hard ? 10 : 6), shift = int(-limit, limit);
    for (let j = start; j < Math.min(heightCells, start + size); j++) rows[j] = shift;
  }
  return rows;
}

export class Glitch {
  constructor(cfg = {}, rng = Math.random, clock = () => performance.now() / 1000) {
    this.cfg = { ...GLITCH_DEFAULTS, ...(cfg || {}) }; this.rng = rng; this.clock = clock;
    this.until = 0; this.hard = false; this.nextRegen = 0; this.rows = null; this.flicker = 1;
    const now = clock();
    this.nextSoft = now + this.span("idle_every_s"); this.nextHard = now + this.span("hard_every_s");
  }
  span(key) { const [lo, hi] = this.cfg[key]; return lo + this.rng() * (hi - lo); }
  gapKey(state) { return { PROCESSING: "processing_every_s", SPEAKING: "speaking_every_s" }[state] || "idle_every_s"; }
  /** [per-row shifts or null, brightness multiplier] */
  update(state, height) {
    if (!this.cfg.enabled) return [null, 1];
    const now = this.clock();
    if (now < this.until) {                                          // a glitch is running: reshuffle it every few frames
      if (now >= this.nextRegen) {
        this.rows = newGlitch(height, this.rng, this.hard, Math.trunc(this.cfg[this.hard ? "hard_shift" : "soft_shift"]));
        this.flicker = this.hard ? (this.rng() < 0.2 ? 0.12 : 0.35 + this.rng() * 0.65) : 0.85 + this.rng() * 0.15;
        this.nextRegen = now + (this.hard ? 0.05 : 0.07);
      }
      return [this.rows, this.flicker];
    }
    this.rows = null; this.flicker = 1;
    const [lo, hi] = this.cfg[this.gapKey(state)];
    if (this.nextSoft - now > hi) this.nextSoft = now + lo + this.rng() * (hi - lo);   // the state got busier: do not wait out the old gap
    if (now >= this.nextHard) {
      this.hard = true; this.until = now + this.span("hard_ms") / 1000; this.nextHard = this.until + this.span("hard_every_s");
      this.nextSoft = Math.max(this.nextSoft, this.until + lo); this.nextRegen = 0;
    } else if (now >= this.nextSoft) {
      this.hard = false; this.until = now + this.span("soft_ms") / 1000; this.nextSoft = this.until + lo + this.rng() * (hi - lo); this.nextRegen = 0;
    }
    return [null, 1];
  }
}

// ---------------- the geometry file ----------------
let modelPromise = null;
export const loadHeadModel = () => (modelPromise ||= fetch("/assets/themes/ultron/head/gap_head.json").then((r) => r.json()));

// ---------------- painting ----------------
const rgb = (c, b) => `rgb(${Math.round(c[0] * b)},${Math.round(c[1] * b)},${Math.round(c[2] * b)})`;
function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  if (g.roundRect) g.roundRect(x, y, w, h, r);
  else g.rect(x, y, w, h);
  g.fill();
}
const key = (j, i) => j * 1024 + i;

export class HeadPainter {
  constructor(model) {
    this.m = model; this.w = model.w; this.h = model.h;
    this.cell = 0; this.dpr = 0;
    this.gapCells = new Set(); for (const g of model.gapCols) for (const [j] of g.cells) this.gapCells.add(key(j, g.i));
    for (const [r, c] of model.gapEdges) this.gapCells.add(key(r, c));
    this.mouthPlain = model.mouth.filter(([j, i]) => !this.gapCells.has(key(j, i)));
    this.metalV = (j, i, rim) => (rim ? 0.78 : 0.20 + 0.18 * (1 - j / this.h)) * model.shimmer[j][i];
  }

  /** The dim metal with its brighter rim (painted once per size), the eye glow sprite and the halo sprite. */
  build(cell, dpr) {
    const { w, h } = this, m = this.m, margin = 2 * cell, W = w * cell + 2 * margin, H = h * cell + 2 * margin, gap = Math.max(1, cell * 0.18);
    const canvas = (cw, ch) => { const c = document.createElement("canvas"); c.width = Math.ceil(cw * dpr); c.height = Math.ceil(ch * dpr); const g = c.getContext("2d"); g.scale(dpr, dpr); return [c, g]; };
    const [sc, sg] = canvas(W, H);
    for (const [j, i, rim] of m.metal) {
      const v = this.metalV(j, i, rim), x = margin + i * cell + gap / 2, y = margin + j * cell + gap / 2, s = cell - gap;
      if (rim) { sg.fillStyle = "rgba(255,20,25,0.27)"; roundRect(sg, x - 1.2, y - 1.2, s + 2.4, s + 2.4, cell * 0.4); }
      sg.fillStyle = rgb(C.DIM_METAL, v); roundRect(sg, x, y, s, s, cell * 0.3);
    }
    this.static = sc; this.staticW = W; this.staticH = H; this.margin = margin;
    const gs = cell * 14, [gc, gg] = canvas(gs, gs), grad = gg.createRadialGradient(gs / 2, gs / 2, 0, gs / 2, gs / 2, gs / 2);
    grad.addColorStop(0, "rgba(255,30,30,0.784)"); grad.addColorStop(0.45, "rgba(255,30,30,0.275)"); grad.addColorStop(1, "rgba(255,30,30,0)");
    gg.fillStyle = grad; gg.fillRect(0, 0, gs, gs);
    this.glow = gc; this.glowSize = gs;
    const hs = Math.floor(Math.max(w, h) * cell * 1.9), [hc, hg] = canvas(hs, hs), halo = hg.createRadialGradient(hs / 2, hs / 2, 0, hs / 2, hs / 2, hs / 2), rim = Math.exp(-3.5);
    for (let k = 0; k <= 16; k++) {                                  // a gaussian that reaches zero at its rim
      const t = k / 16, fall = (Math.exp(-3.5 * t * t) - rim) / (1 - rim);
      halo.addColorStop(t, `rgba(255,${Math.floor(60 - 40 * t)},20,${Math.floor(C.HALO_PEAK_ALPHA * fall) / 255})`);
    }
    hg.fillStyle = halo; hg.fillRect(0, 0, hs, hs);
    this.halo = hc; this.haloSize = hs;
    this.cell = cell; this.dpr = dpr;
  }

  /** Draw the head centred on (cx, cy). level 0..1 is the voice; shift is a per-row sideways glitch shift in cells (or null). */
  paint(g, cx, cy, cell, level, t, shift = null, flicker = 1, dpr = 1) {
    cell = Math.max(2, Math.floor(cell));
    if (cell !== this.cell || dpr !== this.dpr) this.build(cell, dpr);
    const { w, h } = this, m = this.m, base = g.globalAlpha, margin = this.margin;
    g.globalAlpha = base * flicker;                                  // a hard flicker stutters the whole head's brightness
    const x0 = cx - (w * cell) / 2, y0 = cy - (h * cell) / 2, off = (j) => ((shift && shift[j]) || 0) * cell, gap = Math.max(1, cell * 0.18);

    g.globalAlpha = base * flicker * (0.55 + 0.45 * level);          // halo: faint at rest, stronger as the voice rises
    g.drawImage(this.halo, cx - this.haloSize / 2, cy - this.haloSize / 2, this.haloSize, this.haloSize);
    g.globalAlpha = base * flicker;

    const sx = x0 - margin, sy = y0 - margin;                        // dim metal: the whole layer, or in horizontal bands when glitching
    if (!shift) g.drawImage(this.static, sx, sy, this.staticW, this.staticH);
    else {
      const groups = [];
      for (let j = 0; j < h; j++) { const o = off(j), last = groups[groups.length - 1]; if (last && last[2] === o) last[1] = j + 1; else groups.push([j, j + 1, o]); }
      for (const [j0, j1, o] of groups) {
        const top = j0 === 0 ? 0 : margin + j0 * cell, bot = j1 >= h ? this.staticH : margin + j1 * cell;
        g.drawImage(this.static, 0, top * this.dpr, this.static.width, (bot - top) * this.dpr, sx + o, sy + top, this.staticW, bot - top);
      }
    }

    const gscale = 1 + 0.45 * level;                                 // eye glow, flaring with the voice
    for (const [ej, ei] of m.eyeCentres) {
      const gs = this.glowSize * gscale;
      g.drawImage(this.glow, x0 + (ei + 0.5) * cell + off(Math.floor(ej)) - gs / 2, y0 + (ej + 0.5) * cell - gs / 2, gs, gs);
    }

    const dot = (j, i, b, core, dx = 0) => {
      const x = x0 + (i + dx) * cell + off(j) + gap / 2, y = y0 + j * cell + gap / 2, s = cell - gap;
      if (b > 0.6) { g.fillStyle = `rgba(255,20,25,${(110 * b) / 255})`; roundRect(g, x - 1.3, y - 1.3, s + 2.6, s + 2.6, cell * 0.4); }
      g.fillStyle = rgb(core, b); roundRect(g, x, y, s, s, cell * 0.28);
    };
    const sh = m.shimmer, pb = plateBrightness(level, t), HOT = C.PLATE_HOT;

    const lowerOn = new Set();                                       // the lower lip: metal cells below the grille that are lit now
    for (const col of m.lowerCols) { const edge = lowerEdge(col.bottom, col.taper, level); for (const j of col.rows) if (lowerLit(j, edge)) lowerOn.add(key(j, col.i)); }

    const sb = seamBrightness(level, t);                             // lower face: the cells inside the outline follow the mouth; the seam dots are lights
    for (const [j, i, u, weight, rim, seam] of m.cheeks) {
      if (lowerOn.has(key(j, i))) continue;
      const dx = cheekShift(u, weight, level);
      if (seam) dot(j, i, sb * sh[j][i], HOT, dx);
      else {
        const x = x0 + (i + dx) * cell + off(j) + gap / 2, y = y0 + j * cell + gap / 2, s = cell - gap;
        if (rim) { g.fillStyle = "rgba(255,20,25,0.27)"; roundRect(g, x - 1.2, y - 1.2, s + 2.4, s + 2.4, cell * 0.4); }
        g.fillStyle = rgb(C.DIM_METAL, this.metalV(j, i, rim) ); roundRect(g, x, y, s, s, cell * 0.3);
      }
    }

    const bb = browBrightness(level, t);
    for (const [j, i] of m.brows) dot(j, i, bb * sh[j][i], HOT);
    for (const [j, i] of this.mouthPlain) dot(j, i, pb * sh[j][i], HOT);
    for (const k of lowerOn) { const j = Math.floor(k / 1024), i = k % 1024; dot(j, i, pb * sh[j][i], HOT); }

    const blended = (j, i, lit, isPlate) => {                        // a gap cell between a dark metal dot (0) and a lit plate dot (1)
      if (lit < 0.02 && !isPlate) return;                            // a notch cell that stays dark is just the metal underneath
      const s0 = sh[j][i], dark = (0.20 + 0.18 * (1 - j / h)) * s0, litB = pb * s0;
      const col = [0, 1, 2].map((n) => C.DIM_METAL[n] * dark * (1 - lit) + HOT[n] * litB * lit);
      const x = x0 + i * cell + off(j) + gap / 2, y = y0 + j * cell + gap / 2, s = cell - gap;
      if (litB * lit > 0.6) { g.fillStyle = `rgba(255,20,25,${(110 * litB * lit) / 255})`; roundRect(g, x - 1.3, y - 1.3, s + 2.6, s + 2.6, cell * 0.4); }
      g.fillStyle = `rgb(${Math.round(col[0])},${Math.round(col[1])},${Math.round(col[2])})`; roundRect(g, x, y, s, s, cell * 0.28);
    };
    for (const col of m.gapCols) { const edge = gapEdge(col.top, col.taper, level); for (const [j, plate] of col.cells) blended(j, col.i, gapLit(j, edge), !!plate); }
    for (const [j, i, depth] of m.gapEdges) blended(j, i, gapSideLit(depth, level), true);

    for (const [j, i] of m.eyes) dot(j, i, 1, C.EYE_CORE);
    g.globalAlpha = base;
  }
}
