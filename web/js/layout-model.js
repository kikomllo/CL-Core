// A window's saved layout (tests/test_webLayout.py runs this under node). The position is stored as fractions of the viewport, so a screen of
// another size or orientation puts it in the same relative spot, and the size in design px (the fluid unit), so it scales with the screen.
// The saved layout is the source of truth: where a window is drawn is always computed from it, never from where the window happens to be
// right now (a hidden or squeezed window has no meaningful rectangle: in the overlay the page is a 200x200 box with the windows not drawn).

const clamp = (v, hi) => Math.max(0, Math.min(v, hi));

/** The layout to save for a window drawn at `rect` ({left, top, width, height} in px) in a W x H viewport with unit `u` px per design px. */
export const toLayout = (rect, u, W, H) => ({ fx: rect.left / W, fy: rect.top / H, w: rect.width / u, h: rect.height / u });

/** Where a saved layout puts a window in a W x H viewport: {x, y, w, h} in px, kept inside the viewport. */
export function fromLayout(layout, u, W, H) {
  const w = layout.w * u, h = layout.h * u;
  return { x: clamp(layout.fx * W, W - w), y: clamp(layout.fy * H, H - h), w, h };
}

/** Whether the windows are drawn at all: not in the desktop shell's overlay mode, where only the orb is. */
export const windowsDrawn = (mode) => mode !== "overlay";
