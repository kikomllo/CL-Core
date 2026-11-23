// The orb's rules for the desktop shell's overlay window (tests/test_webOrb.py runs this under node). The overlay is a small click-through box
// that holds only the orb, so: it shows nothing at rest (the Qt overlay's orb fades out and shrinks when idle), the bloom must end inside
// the box (a glow that reaches the window's edge is cut off there and shows the rectangle), and the shell hides the window while nothing shows.

export const BLOOM_RADIUS = 170;

/** What an idle orb looks like: a dim orb on the dashboard, nothing in the overlay (opacity 0, shrunk to half). */
export const restingLook = (overlay) => (overlay ? { op: 0, scale: 0.5 } : { op: 0.8, scale: 0.8 });

/** The bloom's radius in px, `scale` being the orb's current scale. In the overlay it is held so it still fits the window at full scale:
 *  the bloom fades to exactly zero at its rim, so a smaller one has no visible edge. */
export function bloomRadius(u, W, H, scale, overlay) {
  const r = BLOOM_RADIUS * u;
  return overlay ? Math.min(r, Math.min(W, H) / 2 / Math.max(scale, 1)) : r;
}

/** The Ultron head is 300 design px across: in the overlay it is kept inside the window's width. */
export const headUnit = (u, W, overlay) => (overlay ? Math.min(u, (W * 0.8) / 300) : u);

/** Whether the overlay window has anything to show: the orb is visible or on its way up. When false the shell hides the window. */
export const overlayVisible = (opacity, targetOpacity) => targetOpacity > 0 || opacity > 0.01;
