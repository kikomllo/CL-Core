// Shared UI helpers: inline-SVG icons (recoloured with currentColor) and the floating-window manager.
import { toLayout, fromLayout, windowsDrawn } from "./layout-model.js";
import { wheelScroll, edges, revealOffset } from "./tabstrip-model.js";
import { getWidget } from "./zoom.js";

const svgCache = new Map();
/** Resolves when every icon asked for so far has arrived (a window shown before that has gaps where its icons go). */
export const iconsReady = () => Promise.all([...svgCache.values()]);

/** A <span class="ic"> holding assets/icons/<name>, filled with currentColor unless keep=true (brand colours). */
export function icon(name, { size = 18, keep = false } = {}) {
  const el = document.createElement("span");
  el.className = "ic";
  el.style.cssText = `display:inline-flex;width:calc(${size} * var(--u));height:calc(${size} * var(--u))`;
  const set = (n, k) => {
    let p = svgCache.get(n);
    if (!p) { p = fetch(`/assets/icons/${n}`).then(r => r.text()); svgCache.set(n, p); }
    p.then(t => {
      if (!k) t = t.replace(/fill="#[0-9a-fA-F]{3,8}"/g, 'fill="currentColor"');
      el.innerHTML = t.replace("<svg", '<svg width="100%" height="100%"');
    });
  };
  el.setIcon = (n, k = keep) => set(n, k);
  set(name, keep);
  return el;
}

export const h = (tag, props = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style") e.style.cssText = v;
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid.nodeType ? kid : document.createTextNode(kid));
  return e;
};

/** Makes a `.tabs-pill` usable when it is wider than its window: the mouse wheel scrolls it sideways, a fade at each edge with more tabs
 *  behind it says so, and the selected tab (aria-pressed) is brought into view whenever the strip is rebuilt or resized. */
export function enableTabScroll(strip) {
  const m = () => ({ scrollLeft: strip.scrollLeft, scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth });
  const fades = () => { const e = edges(m()); strip.classList.toggle("fade-l", e.left); strip.classList.toggle("fade-r", e.right); };
  const reveal = () => {
    const on = strip.querySelector('[aria-pressed="true"]');
    if (on) {
      const s = strip.getBoundingClientRect(), r = on.getBoundingClientRect();
      strip.scrollLeft = revealOffset(m(), r.left - s.left, r.width, 12 * unit());
    }
    fades();
  };
  strip.addEventListener("wheel", (e) => {
    const next = wheelScroll(e, m());
    if (next != null) { e.preventDefault(); strip.scrollLeft = next; fades(); }
  }, { passive: false });
  strip.addEventListener("scroll", fades, { passive: true });
  new ResizeObserver(fades).observe(strip);
  new MutationObserver(reveal).observe(strip, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-pressed"] });
  requestAnimationFrame(reveal);
}

/** A window can have a zoom of its own: its content scales (its --u is the global unit times that zoom), its frame does not. */
function applyZoom(el) {
  const z = getWidget(el.dataset.id);
  if (z === 1) el.style.removeProperty("--u"); else el.style.setProperty("--u", `calc(var(--ug) * ${z})`);
}
/** The unit inside a window: the global one times its own zoom (what its minimum size is measured in). */
const unitOf = (el) => unit() * getWidget(el.dataset.id);

/** The unit without the zoom (--ub): window frames (their size, their position, their saved layout) are made of this, so zooming the UI scales what is
 *  inside the frames and leaves the frames where and as big as they are. Falls back to --u where --ub is not set (the unzoomed case). */
const baseUnit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ub")) || unit();

export const unit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;

// ---------- windows ----------
const LAYOUT_KEY = "jarvis.layout.v1";
const loadLayout = () => { try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) || {}; } catch { return {}; } };
const saveLayout = (l) => { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(l)); } catch { /* private mode */ } };
// Pin / unpin, like the Qt widgets: a window is pinned (part of the dashboard, hidden with it in the overlay) or unpinned (a window of its
// own, always on top of other apps, draggable to any monitor, there also while the dashboard is only the overlay). The shell makes the
// window (shell/main.js); this page is then either the dashboard, or the page of one floating window (widget.html, widgetMode).
const SHELL = window.jarvisShell || null;
export const widgetMode = document.documentElement.dataset.mode === "widget";
// the page can be newer than the shell that shows it (a reload picks up new web files, the shell only changes when it is restarted):
// a feature the shell lacks must be absent, never an error that stops the page, which once left the overlay without its orb
export const canFloat = !!(SHELL && typeof SHELL.float === "function");

/** Unpins a dashboard window: it floats where it is, now as a window of its own. */
function unpin(id, el) {
  const r = el.getBoundingClientRect();
  // the window stays in the dashboard until the floating one is on screen right over it (onFloatShown below): no gap, no flicker
  SHELL.float(id, { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height), relative: true });
}
if (canFloat && SHELL.onFloatShown) SHELL.onFloatShown((id) => { if (open.has(id)) closeWindow(id); });

/** The grip of a floating window resizes the window itself (the shell sets the size; the page just follows). */
function floatResize(grip) {
  grip.addEventListener("pointerdown", (e) => {
    const sx = e.screenX, sy = e.screenY, w0 = innerWidth, h0 = innerHeight;
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => SHELL.resizeSelf(w0 + ev.screenX - sx, h0 + ev.screenY - sy);
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); };
    grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up);
  });
}
const open = new Map();
let zTop = 10;
export const openIds = () => [...open.keys()];

// small per-window choices (a selected tab, an open drawer) that survive a reload: one JSON object in localStorage
const STATE_KEY = "jarvis.state.v1";
export const loadUiState = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; } };
export function saveUiState(key, value) {
  try { const s = loadUiState(); if (value == null) delete s[key]; else s[key] = value; localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch { /* private mode */ }
}

// ---------- minimum size: fit to content ----------
// A window can be resized to any size at which its content still fits: the smallest size is MEASURED from the content, not a number each widget
// guessed. Scrolling regions are the exception (a list, a terminal, a strip of tabs can be any size), so while measuring they are replaced by a
// small floor ({w, h} in design px; a missing w or h leaves that axis to the content): what is left is the header, the buttons, the rows that
// must all be visible. Text that is cut with an ellipsis never forces the window wide (a long song title must not set the minimum).
const ELASTIC = [
  { sel: ".mod-list, .rem-grid, .set-pages", w: 200, h: 72 },      // vertical lists and pages: a few rows, wide enough that their text does not break up
  { sel: ".tabs-pill", w: 0 },                              // a row of tabs that scrolls sideways
  { sel: ".t-screen, .cl-screen", w: 350, h: 140 },        // terminals: 40 columns (~310 px) plus the screen's padding, and a few rows
];
// Controls that appear over the content on hover (the volume bar) are drawn absolutely, so they take no room by themselves; while measuring, each one
// reserves the strip it covers on the right of its container (its own width, its distance from the edge, and OVERLAY_GAP design px between it and
// the content), so at the smallest size the content stays clear of it. The strip's size comes from the overlay itself, not from a number.
const OVERLAYS = [".fader"];
const OVERLAY_GAP = 8;
const FIT_FLOOR = { w: 120, h: 80 };                       // design px: below this a window is only a title bar

/** The smallest {w, h} in px at which `el` (an open window) shows its fixed content. Measures with the live DOM and restores it within the
 *  same task, so nothing is painted in between. */
export function fitMin(el, u = unitOf(el)) {
  const saved = [];
  const touch = (n, props) => { saved.push([n, n.style.cssText]); Object.assign(n.style, props); };
  const elastic = new Set();
  for (const { sel, w, h } of ELASTIC) el.querySelectorAll(sel).forEach((n) => {
    elastic.add(n);
    const p = { flex: "none", minWidth: "0", minHeight: "0", overflow: "hidden" };
    if (h != null) p.height = h * u + "px";
    if (w != null) p.width = w * u + "px";
    touch(n, p);
  });
  for (const sel of OVERLAYS) el.querySelectorAll(sel).forEach((o) => {
    const host = o.offsetParent;
    if (!host || host === el) return;
    const padRight = parseFloat(getComputedStyle(host).paddingRight) || 0, edge = host.clientWidth - (o.offsetLeft + o.offsetWidth);   // edge: from the overlay to the host's right edge
    const reserve = o.offsetWidth + edge + OVERLAY_GAP * u - padRight;
    if (reserve > 0) touch(host, { paddingRight: padRight + reserve + "px" });
  });
  const walk = (n) => {                                      // the rest of the content: ellipsised text does not count towards the width
    for (const c of n.children) {
      if (elastic.has(c)) continue;
      if (getComputedStyle(c).textOverflow === "ellipsis") touch(c, { containIntrinsicSize: "0", contain: "inline-size" });
      walk(c);
    }
  };
  walk(el);
  touch(el, { width: "min-content", height: "auto", minWidth: "0", minHeight: "0" });
  const out = { w: Math.max(Math.ceil(el.offsetWidth), FIT_FLOOR.w * u), h: Math.max(Math.ceil(el.offsetHeight), FIT_FLOOR.h * u) };
  for (let i = saved.length - 1; i >= 0; i--) saved[i][0].style.cssText = saved[i][1];
  return out;
}

/** Measures a window and makes the result its minimum size (the grip and media.js read el.style.minWidth / minHeight). */
function applyMin(el) {
  if (!el.isConnected || !windowsDrawn(document.documentElement.dataset.mode)) return null;
  const m = fitMin(el);
  el.style.minWidth = m.w + "px"; el.style.minHeight = m.h + "px";
  if (widgetMode) SHELL.minSelf(m.w, m.h);            // the floating window cannot be made smaller than its content needs
  return m;
}

/** def = {id, title, w, h (design px), build(body, win) -> {onClose?}}; win = {close, el, head, title, pin} */
/** `at` = {x, y, width, height} in page px puts the window exactly there (a window pinned back from where it floated), instead of at its saved place. */
export function openWindow(def, at) {
  if (open.has(def.id)) { focus(open.get(def.id).el); return open.get(def.id); }
  const u = baseUnit(), layout = widgetMode ? undefined : loadLayout()[def.id];
  const el = h("div", { class: "win panel", "data-id": def.id });
  applyZoom(el);
  // pinned (in the dashboard): the dimmed pin_off icon; unpinned (a window of its own): the bright pin icon, like the Qt widgets. Needs the shell.
  const pin = h("button", { class: "chrome-btn pin" + (widgetMode ? " on" : ""), hidden: !canFloat,
    title: widgetMode ? "Pin back into the dashboard" : "Unpin: a window of its own, on top of other apps", "aria-label": widgetMode ? "Pin" : "Unpin",
    onclick: () => (widgetMode ? SHELL.dockSelf() : unpin(def.id, el)) }, icon(widgetMode ? "pin.svg" : "pin_off.svg", { size: 12 }));
  const titleEl = h("span", { class: "win-title" }, def.title);
  const head = h("div", { class: "win-head" }, h("div", { class: "win-dot" }),
    titleEl, pin,
    h("button", { class: "chrome-btn", title: "Close", "aria-label": "Close", onclick: () => closeWindow(def.id) }, icon("close.svg", { size: 12 })));
  const body = h("div", { class: "win-body" });
  const grip = h("div", { class: "win-grip" });
  el.append(head, body, grip);
  document.getElementById("widgets").append(el);

  if (widgetMode) {
    // a floating window: the window fills the page, the shell moves (the title bar is a drag region, style.css) and sizes the page, and
    // nothing of the dashboard's saved layouts is read or written
    Object.assign(el.style, { left: "0", top: "0", width: "100%", height: "100%" });
    floatResize(grip);
  } else {
    const W = innerWidth, H = innerHeight;
    const w = at ? at.width : layout ? layout.w * u : def.w * u, hh = at ? at.height : layout ? layout.h * u : def.h * u;
    // a window never moved yet goes in the first free spot of a tidy row along the top (the Qt dashboard's arrangement)
    const spot = layout || at ? null : freeSpot(w, hh, u);
    const x = at ? at.x : layout ? layout.fx * W : spot.x, y = at ? at.y : layout ? layout.fy * H : spot.y;
    Object.assign(el.style, { width: Math.round(w) + "px", height: Math.round(hh) + "px", minWidth: FIT_FLOOR.w * u + "px", minHeight: FIT_FLOOR.h * u + "px" });
    place(el, x, y);
    if (!layout) persist(def.id, el);               // every open window has a saved layout, the source of truth when the viewport changes
    if (at) persist(def.id, el);                    // a window pinned back saves where it now is
    drag(head, el, () => persist(def.id, el));
    resizeGrip(grip, el, () => persist(def.id, el));
  }
  focus(el);
  el.addEventListener("pointerdown", () => focus(el));
  const inst = { el, body, def, api: {} };
  open.set(def.id, inst);
  inst.api = def.build(body, { close: () => closeWindow(def.id), el, head, title: titleEl, pin }) || {};      // head/title/pin: for a window that puts controls in its title bar
  window.dispatchEvent(new CustomEvent("windows-changed"));
  requestAnimationFrame(() => applyMin(el));
  setTimeout(() => applyMin(el), 1500);                  // data that arrives after the first paint (a list, a title) may change what must fit
  return inst;
}

/** First position, scanning rows from the top-left, where a w x h window overlaps no open window. */
function freeSpot(w, hh, u) {
  const gap = 14 * u, left = 16 * u, W = innerWidth, rects = [...open.values()].map(({ el }) => el.getBoundingClientRect());
  let x = left, y = 48 * u;
  for (let guard = 0; guard < 200; guard++) {
    const hit = rects.find(r => x < r.right + gap && x + w + gap > r.left && y < r.bottom + gap && y + hh + gap > r.top);
    if (!hit) return { x, y };
    x = hit.right + gap;
    if (x + w > W - left) { x = left; y = Math.max(y, ...rects.filter(r => r.bottom + gap > y && r.top < y + hh).map(r => r.bottom + gap)); if (y + hh > innerHeight) return { x: left, y: 48 * u }; }
  }
  return { x: left, y: 48 * u };
}

export function closeWindow(id) {
  const w = open.get(id); if (!w) return;
  if (!widgetMode) persist(id, w.el);
  w.api.onClose?.(); w.el.remove(); open.delete(id);
  window.dispatchEvent(new CustomEvent("windows-changed"));
  if (widgetMode) SHELL.closeSelf();                 // closing a floating window closes its window
}
export const isOpen = (id) => open.has(id);
export const toggleWindow = (def) => (open.has(def.id) ? closeWindow(def.id) : openWindow(def));

function focus(el) { el.style.zIndex = ++zTop; }
function place(el, x, y) {
  const r = el.getBoundingClientRect(), W = innerWidth, H = innerHeight;
  // whole pixels: a window at a fractional position is blurred, and one that floats (a window of its own, at whole pixels) would be a hair off
  el.style.left = Math.round(Math.max(0, Math.min(x, W - r.width))) + "px";
  el.style.top = Math.round(Math.max(0, Math.min(y, H - r.height))) + "px";
}
function persist(id, el) {
  const r = el.getBoundingClientRect(), l = loadLayout();
  if (!r.width || !r.height) return;                    // not drawn (display: none): there is nothing to save
  l[id] = toLayout(r, baseUnit(), innerWidth, innerHeight);
  saveLayout(l);
}
function drag(handle, el, done) {
  handle.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    handle.setPointerCapture(e.pointerId);
    const move = (ev) => place(el, ev.clientX - dx, ev.clientY - dy);
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); done(); };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
  });
}
function resizeGrip(grip, el, done) {
  grip.addEventListener("pointerdown", (e) => {
    applyMin(el);                                          // the content may have changed since it opened
    const r = el.getBoundingClientRect(), sx = e.clientX, sy = e.clientY;
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => {
      el.style.width = Math.max(parseFloat(el.style.minWidth), r.width + ev.clientX - sx) + "px";
      el.style.height = Math.max(parseFloat(el.style.minHeight), r.height + ev.clientY - sy) + "px";
    };
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); done(); };
    grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up);
  });
}
// The viewport changed (a monitor swap, a rotated screen, the desktop shell's overlay <-> dashboard): every window is placed again from its SAVED
// layout. It used to clamp each window where it was, which lost the layout whenever the page was squeezed: in the overlay the windows are not
// drawn, so each measured as 0x0 at 0,0 and went there for good.
function refit() {
  if (widgetMode) return;                              // a floating window fills its own page, it has no saved layout to go back to
  if (!windowsDrawn(document.documentElement.dataset.mode)) return;
  const u = baseUnit(), layouts = loadLayout();
  open.forEach(({ el }, id) => {
    const l = layouts[id];
    if (!l) return;
    const r = fromLayout(l, u, innerWidth, innerHeight);
    Object.assign(el.style, { width: r.w + "px", height: r.h + "px", left: r.x + "px", top: r.y + "px" });
  });
}
addEventListener("resize", refit);
// the unit changed (a zoom): what must fit in each window is measured again at the new size. The frames stay (they are made of the unit
// without the zoom); a window only grows when its content, zoomed in, no longer fits, and goes back to its size when zoomed out again
addEventListener("jarvis-widget-zoom", (e) => {                     // one window's zoom changed (here or in another window of the UI): its content only
  for (const { el } of open.values()) if (!e.detail || !e.detail.id || el.dataset.id === e.detail.id) { applyZoom(el); requestAnimationFrame(() => applyMin(el)); }
});
addEventListener("jarvis-unit", () => { refit(); for (const { el } of open.values()) requestAnimationFrame(() => applyMin(el)); });
addEventListener("jarvis-mode", refit);               // fired by shell.js once the mode changed: the screen the dashboard returns to may differ
