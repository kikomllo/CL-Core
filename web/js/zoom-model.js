// Zoom for the whole UI and for single windows (tests/test_webZoom.py runs this under node). Zoom is a factor on the fluid unit --u, which every
// size is written in (calc(N * var(--u))), so a zoomed UI is laid out again at the new size, not scaled as a picture: text stays sharp and the
// saved window layouts (in design px) follow by themselves.

export const GLOBAL_RANGE = [0.6, 2];
export const WIDGET_RANGE = [0.5, 2.5];
export const STEP = 0.1;

/** A usable zoom: a number in the range (rounded to 1 %), 1 for anything else. */
export function clampZoom(z, range = GLOBAL_RANGE) {
  const n = Number(z);
  if (!Number.isFinite(n) || n <= 0) return 1;
  return Math.min(range[1], Math.max(range[0], Math.round(n * 100) / 100));
}

/** One step in or out (dir +1 / -1), kept inside the range. */
export const stepZoom = (z, dir, range = GLOBAL_RANGE) => clampZoom(Math.round((z + (dir > 0 ? STEP : -STEP)) * 100) / 100, range);

export const percent = (z) => Math.round(z * 100);

/** The wheel turns that make whole steps: a notch is ~100 and a trackpad sends many small ones, so they are added up. Wheel up zooms in.
 *  Returns {steps (signed), acc (what is left over)}. */
export function wheelSteps(acc, deltaY, threshold = 80) {
  const total = acc + (Number.isFinite(deltaY) ? deltaY : 0);
  const n = Math.trunc(total / threshold);
  return { steps: -n, acc: total - n * threshold };
}

/** What a key press asks of the zoom: "in" (Ctrl + / =), "out" (Ctrl -), "reset" (Ctrl 0), or null. */
export function zoomKey(e) {
  if (!e || e.type !== "keydown" || !(e.ctrlKey || e.metaKey) || e.altKey) return null;
  if (e.key === "=" || e.key === "+") return "in";
  if (e.key === "-" || e.key === "_") return "out";
  if (e.key === "0") return "reset";
  return null;
}

/** The saved zoom state, whatever was stored: {global, widgets: {window id: factor}}, every value valid. */
export function parseZoomState(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const widgets = {};
  if (r.widgets && typeof r.widgets === "object") {
    for (const [id, z] of Object.entries(r.widgets)) {
      const n = clampZoom(z, WIDGET_RANGE);
      if (/^[\w-]{1,64}$/.test(id) && n !== 1) widgets[id] = n;
    }
  }
  return { global: clampZoom(r.global, GLOBAL_RANGE), widgets };
}
