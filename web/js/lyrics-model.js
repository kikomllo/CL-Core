// The lyrics settings and their layout maths (tests/test_webLyrics.py runs this under node). All sizes are design px (times the unit --u at draw time).
// The lines are a column: `after` upcoming lines (small), the current one (big), `before` finished ones (small). The font size is one factor on
// both sizes, so the big and the small line grow together. The column keeps the padding below it the default layout had (1 + 1 lines, 100 %) and
// grows upward: the current line is raised by the room the extra "before" rows (or the bigger font) take below it.

export const DIM_PX = 14, CUR_PX = 26, LINE_HEIGHT = 1.15, GAP = 4;
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

// how far a single-line column reaches below the centre line: half the current line, then each "before" row with its gap
const reach = (before, size) => (CUR_PX * size * LINE_HEIGHT) / 2 + before * (GAP + DIM_PX * size * LINE_HEIGHT);

/** Design px to add to every row's top so the column's bottom stays where the default layout had it (negative = up). */
export const bottomShift = ({ before, size }) => Math.round((reach(DEFAULTS.before, 1) - reach(before, size)) * 1000) / 1000;
