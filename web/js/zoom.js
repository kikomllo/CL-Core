// The zoom state and its input (keys, Ctrl+wheel, a badge that says the level). The maths is in zoom-model.js. The state is kept in localStorage,
// which the dashboard and every floating window share, so a change in one reaches the others through the "storage" event.
import { clampZoom, stepZoom, wheelSteps, zoomKey, parseZoomState, percent, WIDGET_RANGE } from "./zoom-model.js";

const KEY = "jarvis.zoom.v1";
const load = () => { try { return parseZoomState(JSON.parse(localStorage.getItem(KEY))); } catch { return parseZoomState(null); } };
let state = load();
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private mode */ } };
const announce = () => window.dispatchEvent(new CustomEvent("jarvis-zoom", { detail: { global: state.global } }));

export const getGlobal = () => state.global;

/** Sets the zoom of the whole UI (the unit follows, and the windows with it). */
export function setGlobal(z) {
  const n = clampZoom(z);
  if (n === state.global) return;
  state.global = n; save(); announce();
}

const announceWidget = (id) => window.dispatchEvent(new CustomEvent("jarvis-widget-zoom", { detail: { id } }));

/** The zoom of one window (1 = none): only its content scales, its frame stays what it is. It multiplies the global zoom. */
export const getWidget = (id) => state.widgets[id] || 1;

export function setWidget(id, z) {
  const n = clampZoom(z, WIDGET_RANGE);
  if (n === getWidget(id)) return;
  if (n === 1) delete state.widgets[id]; else state.widgets[id] = n;
  save(); announceWidget(id);
}

/** Back to no zoom for every window. */
export function resetWidgets() {
  const ids = Object.keys(state.widgets);
  if (!ids.length) return;
  state.widgets = {}; save();
  ids.forEach(announceWidget);
}

addEventListener("storage", (e) => {                                  // another window of the UI changed something: tell this one what differs
  if (e.key !== KEY) return;
  const before = state; state = load();
  if (before.global !== state.global) announce();
  for (const id of new Set([...Object.keys(before.widgets), ...Object.keys(state.widgets)])) if ((before.widgets[id] || 1) !== getWidget(id)) announceWidget(id);
});

// ---------------------------------------------------------------- the badge
let badge = null, badgeTimer = null;
function showBadge(text, onReset) {
  if (!badge) {
    badge = document.createElement("div");
    badge.className = "zoom-badge"; badge.title = "Double-click to reset";
    document.body.append(badge);
  }
  badge.textContent = text; badge.hidden = false;
  badge.ondblclick = onReset;
  clearTimeout(badgeTimer);
  badgeTimer = setTimeout(() => { badge.hidden = true; }, 1400);
}
const showWidgetBadge = (id, title) => showBadge(`${title} ${percent(getWidget(id))} %`, () => { setWidget(id, 1); showWidgetBadge(id, title); });
const showGlobalBadge = () => showBadge(`Zoom ${percent(state.global)} %`, () => { setGlobal(1); showGlobalBadge(); });

// ---------------------------------------------------------------- input
/** Ctrl + / - / 0 and Ctrl+Alt+wheel zoom the whole UI; Ctrl+wheel over empty space does too, and over a window it zooms that window alone.
 *  `enabled()` says whether input counts (not in the overlay, which takes no input and is not zoomed). */
export function startZoomInput({ enabled = () => document.documentElement.dataset.mode !== "overlay" } = {}) {
  let acc = 0;
  addEventListener("wheel", (e) => {
    if (!e.ctrlKey || !enabled()) return;
    e.preventDefault();
    // over a window the wheel is that window's zoom (a floating window is one window: the whole page); with Alt, or over empty space, the whole UI's
    const win = !e.altKey && ((e.target.closest && e.target.closest(".win")) || (document.documentElement.dataset.mode === "widget" && document.querySelector(".win")));
    const r = wheelSteps(acc, e.deltaY); acc = r.acc;
    if (!r.steps) return;
    if (win) {
      let z = getWidget(win.dataset.id);
      for (let i = 0; i < Math.abs(r.steps); i++) z = stepZoom(z, Math.sign(r.steps), WIDGET_RANGE);
      setWidget(win.dataset.id, z); showWidgetBadge(win.dataset.id, (win.querySelector(".win-title") || {}).textContent || "Window");
      return;
    }
    let z = state.global;
    for (let i = 0; i < Math.abs(r.steps); i++) z = stepZoom(z, Math.sign(r.steps));
    setGlobal(z); showGlobalBadge();
  }, { passive: false, capture: true });
  addEventListener("keydown", (e) => {
    const k = zoomKey(e);
    if (!k || !enabled()) return;
    e.preventDefault(); e.stopPropagation();                    // before a terminal window can take Ctrl+- for itself
    setGlobal(k === "reset" ? 1 : stepZoom(state.global, k === "in" ? 1 : -1));
    showGlobalBadge();
  }, true);
}
