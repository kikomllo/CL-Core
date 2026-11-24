// The zoom state and its input (keys, Ctrl+wheel, a badge that says the level). The maths is in zoom-model.js. The state is kept in localStorage,
// which the dashboard and every floating window share, so a change in one reaches the others through the "storage" event.
import { clampZoom, stepZoom, wheelSteps, zoomKey, parseZoomState, percent } from "./zoom-model.js";

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

addEventListener("storage", (e) => { if (e.key === KEY) { state = load(); announce(); } });      // another window of the UI changed it

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
const showGlobalBadge = () => showBadge(`Zoom ${percent(state.global)} %`, () => { setGlobal(1); showGlobalBadge(); });

// ---------------------------------------------------------------- input
/** Ctrl + / - / 0 and Ctrl+Alt+wheel zoom the whole UI; Ctrl+wheel over empty space does too (over a window it is that window's, later).
 *  `enabled()` says whether input counts (not in the overlay, which takes no input and is not zoomed). */
export function startZoomInput({ enabled = () => document.documentElement.dataset.mode !== "overlay" } = {}) {
  let acc = 0;
  addEventListener("wheel", (e) => {
    if (!e.ctrlKey || !enabled()) return;
    e.preventDefault();
    const overWindow = !!(e.target.closest && e.target.closest(".win")) && document.documentElement.dataset.mode !== "widget";
    if (overWindow && !e.altKey) return;                        // a window's own zoom (not built yet): nothing to do, and no browser zoom either
    const r = wheelSteps(acc, e.deltaY); acc = r.acc;
    if (!r.steps) return;
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
