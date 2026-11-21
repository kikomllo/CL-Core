// Shared UI helpers: inline-SVG icons (recoloured with currentColor) and the floating-window manager.
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

/** def = {id, title, w, h (design px), minW, minH, build(body, win) -> {onClose?}} */
export function openWindow(def) {
  if (open.has(def.id)) { focus(open.get(def.id).el); return open.get(def.id); }
  const u = unit(), layout = loadLayout()[def.id];
  const el = h("div", { class: "win panel", "data-id": def.id });
  const pinned = loadPinned().includes(def.id);
  const pin = h("button", { class: "chrome-btn pin" + (pinned ? " on" : ""), title: "Keep open across restarts", "aria-label": "Pin",
    onclick: () => { const on = pin.classList.toggle("on"); setPinned(def.id, on); } }, icon("pin.svg", { size: 12 }));
  const head = h("div", { class: "win-head" }, h("div", { class: "win-dot" }),
    h("span", { class: "win-title" }, def.title), pin,
    h("button", { class: "chrome-btn", title: "Close", "aria-label": "Close", onclick: () => closeWindow(def.id) }, icon("close.svg", { size: 12 })));
  const body = h("div", { class: "win-body" });
  const grip = h("div", { class: "win-grip" });
  el.append(head, body, grip);
  document.getElementById("widgets").append(el);

  const W = innerWidth, H = innerHeight;
  const w = layout ? layout.w * u : def.w * u, hh = layout ? layout.h * u : def.h * u;
  // a window never moved yet opens centred, nudged down-right for each window already open so they don't hide each other
  const nudge = open.size * 36 * u;
  const x = layout ? layout.fx * W : (W - w) / 2 + nudge, y = layout ? layout.fy * H : (H - hh) / 2 + nudge;
  Object.assign(el.style, { width: w + "px", height: hh + "px", minWidth: (def.minW || 200) * u + "px", minHeight: (def.minH || 120) * u + "px" });
  place(el, x, y);
  focus(el);
  el.addEventListener("pointerdown", () => focus(el));

  drag(head, el, () => persist(def.id, el));
  resizeGrip(grip, el, () => persist(def.id, el));
  const inst = { el, body, def, api: {} };
  open.set(def.id, inst);
  inst.api = def.build(body, { close: () => closeWindow(def.id), el }) || {};
  window.dispatchEvent(new CustomEvent("windows-changed"));
  return inst;
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
  const u = unit(), r = el.getBoundingClientRect(), l = loadLayout();
  l[id] = { fx: r.left / innerWidth, fy: r.top / innerHeight, w: r.width / u, h: r.height / u };
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
// keep every window inside the viewport when it changes (a monitor swap, a rotated screen)
addEventListener("resize", () => open.forEach(({ el }) => { const r = el.getBoundingClientRect(); place(el, r.left, r.top); }));
