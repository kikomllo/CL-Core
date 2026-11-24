// The shell's pure logic (tests/test_shellGeometry.py runs it under node): where the window goes in each mode, which display, which
// mode to start in. Mirrors the Qt dashboard (src/clUI.py): the overlay is a 200x200 box (the Qt one is 200x400 with the orb in its middle, which sat 200 px up the screen; the orb's bloom ends 100 px from its centre and its loudest wave is 50, so a square holds it) scaled by UIScaler, 20 px in from the bottom
// right of the primary monitor's available area; a cold start opens the overlay, a supervisor restart reopens the saved mode.

const OVERLAY_W = 200, OVERLAY_H = 200, MARGIN = 20;
const MODES = ["overlay", "fullscreen"];

/** One fluid unit, UIScaler.scale_for: min(long / 1920, short / 1080), so a screen scales the same turned either way. */
const scaleFor = (w, h) => Math.min(Math.max(w, h) / 1920, Math.min(w, h) / 1080);

/** The overlay box on a display: `bounds` sizes it, `workArea` (the screen minus the taskbar) places it. */
function overlayBounds(bounds, workArea) {
  const s = scaleFor(bounds.width, bounds.height);
  const width = Math.round(OVERLAY_W * s), height = Math.round(OVERLAY_H * s);
  return { x: workArea.x + workArea.width - width - MARGIN, y: workArea.y + workArea.height - height - MARGIN, width, height };
}

/** The saved state file, whatever it holds: {mode, displayId}. Anything unusable falls back to the overlay. */
function normalizeState(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  return { mode: MODES.includes(r.mode) ? r.mode : "overlay", displayId: Number.isFinite(r.displayId) ? r.displayId : null, floating: sanitizeFloating(r.floating) };
}

const FLOAT_MIN = { width: 160, height: 90 };
const isRect = (b) => b && ["x", "y", "width", "height"].every((k) => Number.isFinite(b[k])) && b.width > 0 && b.height > 0;

/** The saved floating windows, {id: {x, y, width, height}}: only entries with a usable id and rectangle, sizes at least FLOAT_MIN. */
function sanitizeFloating(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [id, b] of Object.entries(raw)) {
    if (!/^[\w-]{1,64}$/.test(id) || !isRect(b)) continue;
    out[id] = { x: Math.round(b.x), y: Math.round(b.y), width: Math.max(FLOAT_MIN.width, Math.round(b.width)), height: Math.max(FLOAT_MIN.height, Math.round(b.height)) };
  }
  return out;
}

const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;

/** Where a floating (unpinned) window goes. `req` is {x, y, width, height} where it was (unpinning from the dashboard keeps it in place), or
 *  just {width, height} (opened from the overlay, which has nothing to take a place from). A rectangle that is no longer on any monitor
 *  (one was unplugged) is pulled onto the primary one; a size alone goes above the overlay box at the bottom right of the primary work
 *  area, each further window a step up and to the left. `displays` are {id, bounds}; `index` counts the floating windows already open. */
function floatBounds(req, displays, primaryWorkArea, index = 0) {
  const w = Math.max(FLOAT_MIN.width, Math.round((req && req.width) || 380)), h = Math.max(FLOAT_MIN.height, Math.round((req && req.height) || 300));
  const wa = primaryWorkArea;
  const fit = (x, y) => ({ x: Math.max(wa.x, Math.min(x, wa.x + wa.width - w)), y: Math.max(wa.y, Math.min(y, wa.y + wa.height - h)), width: w, height: h });
  if (req && Number.isFinite(req.x) && Number.isFinite(req.y)) {
    const r = { x: Math.round(req.x), y: Math.round(req.y), width: w, height: h };
    return displays.some((d) => overlaps(r, d.bounds)) ? r : fit(r.x, r.y);
  }
  const step = 32 * (index % 8);
  return fit(wa.x + wa.width - w - 40 - step, wa.y + wa.height - h - OVERLAY_H - MARGIN - 40 - step);
}

/** The mode to open in: a restart (JARVIS_REBOOT=1) restores the saved one, a cold start is always the overlay. */
const startMode = (reboot, saved) => (reboot ? normalizeState(saved).mode : "overlay");

const contains = (b, p) => p.x >= b.x && p.x < b.x + b.width && p.y >= b.y && p.y < b.y + b.height;

/** Which display a mode uses: the overlay always sits on the primary one; the dashboard opens where the pointer is (you pressed the
 *  hotkey on the monitor you are using), else on the saved display, else the primary. `displays` are {id, bounds}. */
function pickDisplay(mode, displays, primaryId, savedId, cursor) {
  const byId = (id) => displays.find((d) => d.id === id);
  const primary = byId(primaryId) || displays[0];
  if (mode === "overlay") return primary;
  return (cursor && displays.find((d) => contains(d.bounds, cursor))) || byId(savedId) || primary;
}

/** What a ui_control payload asks for: "overlay" | "fullscreen" | null (not a mode switch). */
function modeFromAction(action) {
  return action === "set_overlay" ? "overlay" : action === "set_fullscreen" ? "fullscreen" : null;
}

/** The keyboard shortcut for reloading the page: "reload" (Ctrl+R, F5), "hard" (Ctrl+Shift+R, Ctrl+F5, Shift+F5: also drops the cache, so
 *  an edited script or stylesheet is fetched again), else null. The shell has no menu (Menu.setApplicationMenu(null) in main.js), and
 *  the menu is where Electron keeps these accelerators, so without this none of them work. `input` is Electron's before-input-event. */
function reloadKind(input) {
  if (!input || input.type !== "keyDown" || input.alt || input.meta) return null;
  const key = String(input.key || "").toLowerCase();
  if (key === "f5") return input.control || input.shift ? "hard" : "reload";
  if (key === "r" && input.control) return input.shift ? "hard" : "reload";
  return null;
}

module.exports = { OVERLAY_W, OVERLAY_H, MARGIN, MODES, FLOAT_MIN, scaleFor, overlayBounds, normalizeState, sanitizeFloating, floatBounds, startMode, pickDisplay, modeFromAction, reloadKind };
