// The lyrics settings and their layout maths (tests/test_webLyrics.py runs this under node). All sizes are design px (times the unit --u at draw time).
// The lines are a column: `after` upcoming lines (small), the current one (big), `before` finished ones (small). The font size is one factor on
// both sizes, so the big and the small line grow together. The column keeps the padding below it the default layout had (1 + 1 lines, 100 %) and
// grows upward: the current line is raised by the room the extra "before" rows (or the bigger font) take below it.

export const DIM_PX = 14, CUR_PX = 26, LINE_HEIGHT = 1.15;
export const GAP_RATIO = 0.6;                       // the gap above a row is this much of its own font size, so small rows sit closer than big ones
export const FADE = 0.8, MIN_OPACITY = 0.35;      // each row further out is 20 % fainter, down to 35 %
export const FALLOFF = 0.88, MIN_DIM_PX = 8;      // each row further from the current line is 12 % smaller than the one before, down to 8 px
export const COUNT_RANGE = [0, 5], SIZE_RANGE = [0.8, 1.6];
export const DEFAULTS = { before: 1, after: 1, size: 1 };

/** A usable line count: a whole number in the range, or the fallback. */
export function clampCount(n, fallback = 1) {
  const v = Number(n);
  if (n === null || n === "" || !Number.isFinite(v)) return fallback;
  return Math.min(COUNT_RANGE[1], Math.max(COUNT_RANGE[0], Math.round(v)));
}

/** A usable font factor (rounded to 1 %), 1 for anything else. */
export function clampSize(z) {
  const v = Number(z);
  if (z === null || z === "" || !Number.isFinite(v) || v <= 0) return 1;
  return Math.min(SIZE_RANGE[1], Math.max(SIZE_RANGE[0], Math.round(v * 100) / 100));
}

/** What is stored (anything, even null) as usable settings. */
export function parseSettings(raw) {
  const o = raw && typeof raw === "object" ? raw : {};
  return { before: clampCount(o.before, DEFAULTS.before), after: clampCount(o.after, DEFAULTS.after), size: clampSize(o.size) };
}

export const percent = (z) => Math.round(z * 100);

/** Font size (design px, before the user's factor) of the row `d` places from the current line: the big size at 0, the small size next to it
 *  (what every small row used to be), then smaller and smaller. */
export const rowPx = (d) => (d <= 0 ? CUR_PX : Math.max(MIN_DIM_PX, DIM_PX * Math.pow(FALLOFF, d - 1)));

/** Design px between the row `d` places from the current line and the row next to it on the current-line side. */
export const gapAt = (d, size = 1) => GAP_RATIO * rowPx(d) * size;

/** How much of the colour the row `d` places out keeps (1 next to the current line, fainter and fainter). */
export const opacityAt = (d) => (d <= 1 ? 1 : Math.max(MIN_OPACITY, Math.pow(FADE, d - 1)));

/** How far a single-line column reaches below the centre line: half the current line, then each "before" row with its gap. */
export function reach(before, size) {
  let r = (CUR_PX * size * LINE_HEIGHT) / 2;
  for (let d = 1; d <= before; d++) r += gapAt(d, size) + rowPx(d) * size * LINE_HEIGHT;
  return r;
}

/** Where the column's bottom edge sits by default (below the centre line): the first layout's, 1 + 1 lines at 100 % with a 4 px gap. */
export const REF_BOTTOM = (CUR_PX * LINE_HEIGHT) / 2 + 4 + DIM_PX * LINE_HEIGHT;

/** Design px to add to every row's top so the column's bottom stays at `bottom` (default: where the default layout had it; negative = up). */
export const bottomShift = ({ before, size }, bottom = REF_BOTTOM) => Math.round((bottom - reach(before, size)) * 1000) / 1000;

/** The height of a typical column (a two-line current line, one-line rows around it): what has to fit between the visualizer and the pills.
 *  The real lines can wrap more; that is pushed up at draw time. */
export const nominalHeight = ({ before, after, size }) => {
  let h = 2 * CUR_PX * size * LINE_HEIGHT;
  for (let d = 1; d <= before; d++) h += gapAt(d, size) + rowPx(d) * size * LINE_HEIGHT;
  for (let d = 1; d <= after; d++) h += gapAt(d, size) + rowPx(d) * size * LINE_HEIGHT;
  return h;
};

/** Where the column goes in the gap between the visualizer and the pills. `space` is {visTop, maxBottom} in design px from the centre line
 *  (visTop: the lowest the visualizer reaches, plus a margin; maxBottom: the highest pill, minus the padding). The column stays at its default
 *  place while it fits above it; when it does not it is lowered, never past `maxBottom` (the pills keep their padding). When even the whole
 *  gap is too small it gives way in this order: the font shrinks (`fit`, a factor on the user's size) down to 75 %; the outermost rows are
 *  dropped until one is left on each side (so the current line always has some context); the font shrinks further, down to `floor`; and only
 *  then are the last rows dropped. Returns {fit, bottom, before, after} (`before` / `after` are the counts really shown).
 *  If nothing fits it still sits on the pills' padding and reaches up into the visualizer, never down into the pills. */
export function plan(s, space, floor = 0.45) {
  const room = space.maxBottom - space.visTop, home = Math.min(REF_BOTTOM, space.maxBottom);
  const need = (f, before, after) => nominalHeight({ before, after, size: s.size * f });
  let { before, after } = s, fit = 1;
  const done = () => ({ fit, bottom: Math.min(space.maxBottom, space.visTop + need(fit, before, after)), before, after });
  if (need(1, before, after) <= home - space.visTop) return { fit, bottom: home, before, after };
  if (need(1, before, after) <= room) return done();
  const shrink = (to) => { while (fit > to && need(fit, before, after) > room) fit = Math.max(to, Math.round((fit - 0.02) * 100) / 100); };
  const drop = (min) => {
    while (need(fit, before, after) > room) {
      const minB = Math.min(min, s.before), minA = Math.min(min, s.after);
      if (after > minA && after >= before) after--; else if (before > minB) before--; else if (after > minA) after--; else break;
    }
  };
  shrink(0.75); drop(1); shrink(floor); drop(0);
  return done();
}
