// Shared UI helpers: inline-SVG icons (recoloured with currentColor) and the floating-window manager.
import { toLayout, fromLayout, windowsDrawn } from "./layout-model.js";

const svgCache = new Map();

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

export const unit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;

// ---------- windows ----------
const LAYOUT_KEY = "jarvis.layout.v1";
const loadLayout = () => { try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) || {}; } catch { return {}; } };
const saveLayout = (l) => { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(l)); } catch { /* private mode */ } };
const PIN_KEY = "jarvis.pinned.v1";
export const loadPinned = () => { try { return JSON.parse(localStorage.getItem(PIN_KEY)) || []; } catch { return []; } };
function setPinned(id, on) {
  const s = new Set(loadPinned()); on ? s.add(id) : s.delete(id);
  try { localStorage.setItem(PIN_KEY, JSON.stringify([...s])); } catch { /* private mode */ }
}
const open = new Map();
let zTop = 10;

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
export function fitMin(el, u = unit()) {
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
  return m;
}

/** def = {id, title, w, h (design px), build(body, win) -> {onClose?}}; win = {close, el, head, title, pin} */
export function openWindow(def) {
  if (open.has(def.id)) { focus(open.get(def.id).el); return open.get(def.id); }
  const u = unit(), layout = loadLayout()[def.id];
  const el = h("div", { class: "win panel", "data-id": def.id });
  const pinned = loadPinned().includes(def.id);
  const pin = h("button", { class: "chrome-btn pin" + (pinned ? " on" : ""), title: "Keep open across restarts", "aria-label": "Pin",
    onclick: () => { const on = pin.classList.toggle("on"); setPinned(def.id, on); } }, icon("pin.svg", { size: 12 }));
  const titleEl = h("span", { class: "win-title" }, def.title);
  const head = h("div", { class: "win-head" }, h("div", { class: "win-dot" }),
    titleEl, pin,
    h("button", { class: "chrome-btn", title: "Close", "aria-label": "Close", onclick: () => closeWindow(def.id) }, icon("close.svg", { size: 12 })));
  const body = h("div", { class: "win-body" });
  const grip = h("div", { class: "win-grip" });
  el.append(head, body, grip);
  document.getElementById("widgets").append(el);

  const W = innerWidth, H = innerHeight;
  const w = layout ? layout.w * u : def.w * u, hh = layout ? layout.h * u : def.h * u;
  // a window never moved yet goes in the first free spot of a tidy row along the top (the Qt dashboard's arrangement)
  const spot = layout ? null : freeSpot(w, hh, u);
  const x = layout ? layout.fx * W : spot.x, y = layout ? layout.fy * H : spot.y;
  Object.assign(el.style, { width: w + "px", height: hh + "px", minWidth: FIT_FLOOR.w * u + "px", minHeight: FIT_FLOOR.h * u + "px" });
  place(el, x, y);
  if (!layout) persist(def.id, el);               // every open window has a saved layout, the source of truth when the viewport changes
  focus(el);
  el.addEventListener("pointerdown", () => focus(el));

  drag(head, el, () => persist(def.id, el));
  resizeGrip(grip, el, () => persist(def.id, el));
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
  persist(id, w.el);
  w.api.onClose?.(); w.el.remove(); open.delete(id);
  window.dispatchEvent(new CustomEvent("windows-changed"));
}
export const isOpen = (id) => open.has(id);
export const toggleWindow = (def) => (open.has(def.id) ? closeWindow(def.id) : openWindow(def));

function focus(el) { el.style.zIndex = ++zTop; }
function place(el, x, y) {
  const r = el.getBoundingClientRect(), W = innerWidth, H = innerHeight;
  el.style.left = Math.max(0, Math.min(x, W - r.width)) + "px";
  el.style.top = Math.max(0, Math.min(y, H - r.height)) + "px";
}
function persist(id, el) {
  const r = el.getBoundingClientRect(), l = loadLayout();
  if (!r.width || !r.height) return;                    // not drawn (display: none): there is nothing to save
  l[id] = toLayout(r, unit(), innerWidth, innerHeight);
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
  if (!windowsDrawn(document.documentElement.dataset.mode)) return;
  const u = unit(), layouts = loadLayout();
  open.forEach(({ el }, id) => {
    const l = layouts[id];
    if (!l) return;
    const r = fromLayout(l, u, innerWidth, innerHeight);
    Object.assign(el.style, { width: r.w + "px", height: r.h + "px", left: r.x + "px", top: r.y + "px" });
  });
}
addEventListener("resize", refit);
addEventListener("jarvis-mode", refit);               // fired by shell.js once the mode changed: the screen the dashboard returns to may differ
