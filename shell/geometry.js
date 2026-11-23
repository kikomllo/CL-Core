// The shell's pure logic (tests/test_shellGeometry.py runs it under node): where the window goes in each mode, which display, which
// mode to start in. Mirrors the Qt dashboard (src/clUI.py): the overlay is a 200x400 box scaled by UIScaler, 20 px in from the bottom
// right of the primary monitor's available area; a cold start opens the overlay, a supervisor restart reopens the saved mode.

const OVERLAY_W = 200, OVERLAY_H = 400, MARGIN = 20;
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
  return { mode: MODES.includes(r.mode) ? r.mode : "overlay", displayId: Number.isFinite(r.displayId) ? r.displayId : null };
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

module.exports = { OVERLAY_W, OVERLAY_H, MARGIN, MODES, scaleFor, overlayBounds, normalizeState, startMode, pickDisplay, modeFromAction };
