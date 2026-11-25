// Smart Lights, "Lights module" artifact: a disc + name + switch per light, a colour popover, Turn all off.
// Data: jarvis/sys/light_status {network, lights:[{name,is_on,offline,brand,colour,rgb,kelvin,brightness}]}.
import { bridge } from "./bridge.js";
import { h, icon } from "./ui.js";
import { pickerSpot } from "./picker-spot.js";
import { mergeSwatches, swatchCommand, wheelColour, knobOffset, rgbToHsv, liveSender } from "./swatch-model.js";

const PALETTE = [["red", [255, 0, 0]], ["orange", [255, 165, 0]], ["yellow", [255, 255, 0]], ["green", [0, 255, 0]],
  ["cyan", [0, 255, 255]], ["blue", [0, 0, 255]], ["purple", [128, 0, 128]], ["hot pink", [255, 105, 180]]];
const WHITES = [["warm white", 20, "warm"], ["daylight", 50, "day"], ["coolwhite", 80, "cool"]];
const KELVIN = { wiz: [2700, 6500], tapo: [2500, 6500] };
const kRange = (brand) => KELVIN[String(brand).toLowerCase()] || KELVIN.tapo;
const tempToK = (brand, t) => { const [lo, hi] = kRange(brand); return Math.round(lo + Math.max(0, Math.min(100, t)) * (hi - lo) / 100); };
const kToTemp = (brand, k) => { const [lo, hi] = kRange(brand); return Math.round(Math.max(0, Math.min(1, (k - lo) / (hi - lo))) * 100); };
function whiteTint(k) {
  const stops = [[2500, [255, 170, 90]], [4600, [255, 244, 228]], [6500, [190, 214, 255]]];
  k = Math.max(2500, Math.min(6500, k));
  for (let i = 0; i < 2; i++) {
    const [k0, c0] = stops[i], [k1, c1] = stops[i + 1];
    if (k <= k1) { const f = (k - k0) / (k1 - k0); return c0.map((a, j) => Math.round(a + (c1[j] - a) * f)); }
  }
  return stops[2][1];
}
// The colour swatches the user changed ({name: [r, g, b]}), kept in core.json (settings.light_swatches) through /api/settings so the Qt picker and
// every window of the UI agree. Read when the window builds and again after every picker opens, so a change made elsewhere shows next time.
let savedSwatches = {};
async function loadSwatches() {
  try { const r = await fetch("/api/settings"); if (r.ok) savedSwatches = (await r.json()).light_swatches || {}; } catch { /* server not up yet: the defaults show */ }
}
/** Saves one swatch (rgb), or puts its default back (null). Returns whether the server took it. */
async function saveSwatch(name, rgb) {
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: "light_swatch." + name, value: rgb }) });
    if (!r.ok) return false;
    if (rgb) savedSwatches = { ...savedSwatches, [name]: rgb }; else { savedSwatches = { ...savedSwatches }; delete savedSwatches[name]; }
    return true;
  } catch { return false; }
}

const shown = (l) => (l.rgb ? l.rgb : whiteTint(l.kelvin || 3000));
const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
const target = (name) => name.toLowerCase().replace(/ /g, "_");

export const lightsWindow = {
  id: "widget_light_control", title: "Smart Lights", w: 380, h: 420,
  build(body) {
    const count = h("span", { class: "mod-count" }, "");
    const net = h("div", { class: "dim" }, "");
    const refresh = h("button", { class: "round sm", title: "Refresh", "aria-label": "Refresh", onclick: () => bridge.act("light.set", { action: "refresh_lights", light_target: "all" }) }, icon("refresh.svg", { size: 14 }));
    const list = h("div", { class: "mod-list" }, h("div", { class: "dim mod-empty" }, "Loading lights…"));
    const allOff = h("button", { class: "btn mod-wide", onclick: () => bridge.act("light.set", { action: "off", light_target: "all", silent: true }) }, "Turn all off");
    body.append(h("div", { class: "mod" },
      h("div", { class: "mod-head" }, h("div", {}, h("div", { class: "mod-title" }, "Lights", count), net), refresh), list, allOff));

    let lights = [], picker = null, wheel = null;
    const closeWheel = () => { wheel?.el.remove(); wheel = null; };
    const closePicker = () => { closeWheel(); picker?.el.remove(); picker = null; };
    loadSwatches();

    function row(l) {
      const t = target(l.name), off = !!l.offline, c = css(shown(l));
      const ic = l.kind === "plug" ? "plug.svg" : "lights.svg";                   // a plug (the heater) is not a bulb
      // The bulb disc shows the light's state and colour, and clicking it opens the colour popover (a light without colour is just the indicator).
      const discStyle = l.is_on && !off ? `background:${c};box-shadow:0 0 calc(12 * var(--u)) ${c}66` : "";
      const discClass = "disc" + (l.is_on && !off ? " on" : "");
      const disc = l.colour === false
        ? h("span", { class: discClass, style: discStyle }, icon(ic, { size: 18 }))
        : h("button", { class: discClass + " pick", "aria-label": `Colour of ${l.name}`, "aria-haspopup": "dialog", disabled: off, style: discStyle,
            onclick: (e) => openPicker(l, e.currentTarget) }, icon(ic, { size: 18 }));
      const sw = h("button", { class: "switch" + (l.is_on ? " on" : ""), role: "switch", "aria-checked": String(!!l.is_on), "aria-label": l.name, disabled: off,
        onclick: () => { bridge.act("light.set", { action: "toggle", light_target: t, silent: true }); l.is_on = !l.is_on; render(); } }, h("i"));
      const rm = h("button", { class: "bare", title: "Remove light", "aria-label": "Remove " + l.name,
        onclick: () => { bridge.act("system.discovery", { action: "intent_remove_light", target_str: t }); lights = lights.filter(x => x !== l); render(); } }, icon("close.svg", { size: 12 }));
      return h("div", { class: "mod-row" + (off ? " offline" : "") }, disc,
        h("div", { class: "mod-main" }, h("div", { class: "mod-name" }, l.name), h("div", { class: "dim" }, (off ? "Offline" : l.is_on == null ? "Not responding…" : l.is_on ? "On" : "Off") + (l.kind === "plug" ? " · Plug" : ""))), sw, rm);
    }

    function render() {
      count.textContent = lights.length ? `${lights.filter(l => l.is_on && !l.offline).length} ON` : "";
      list.replaceChildren(...(lights.length ? lights.map(row) : [h("div", { class: "dim mod-empty" }, "No lights configured for this network.")]));
    }

    // Colour popover: 8 swatches, 3 whites, brightness + temperature sliders; sliders send once they settle (200 ms).
    function openPicker(l, anchor) {
      const same = picker?.t === l.name; closePicker(); if (same) return;
      const t = target(l.name), brand = l.brand || "";
      let lum = l.brightness ?? 100, temp = l.kelvin ? kToTemp(brand, l.kelvin) : 50, timer = null, pending = null;
      const send = (f) => { clearTimeout(timer); pending = null; bridge.act("light.set", { action: "on", light_target: t, silent: true, ...f }); };
      const queue = (f) => { pending = f; clearTimeout(timer); timer = setTimeout(() => pending && send(pending), 200); };
      const sw = (name, rgb, label) => h("button", { class: "sw", title: name, "aria-label": name, style: `--c:${css(rgb)}`, onclick: () => send({ color: name, lum }) }, label || "");
      const swatches = mergeSwatches(PALETTE, savedSwatches);
      // A colour swatch: a click sets the light to it; a right click opens the wheel to make it another colour, which stays.
      const paletteSw = (s) => {
        const el = h("button", { class: "sw", title: `${s.name}: right-click to change`, "aria-label": s.name, style: `--c:${css(s.rgb)}`,
          onclick: () => send(swatchCommand(s, lum)), oncontextmenu: (e) => { e.preventDefault(); openWheel(s, el); } });
        return el;
      };
      function openWheel(s, swEl) {
        closeWheel();
        const fallback = PALETTE.find(([n]) => n === s.name)[1];
        const hsv = rgbToHsv(s.rgb);
        const knob = h("i", { class: "wh-knob" });
        const place = (hue, sat) => { const k = knobOffset(hue, sat, 1); knob.style.left = 50 + k.x * 50 + "%"; knob.style.top = 50 + k.y * 50 + "%"; };
        place(hsv.h, hsv.s);
        const live = liveSender((rgb) => send({ rgb, lum }), 120);          // the bulb follows the drag, at most a few messages a second
        const disc = h("div", { class: "wh-disc" }, knob);
        const paint = (e) => {
          const r = disc.getBoundingClientRect();
          const c = wheelColour(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2), r.width / 2);
          s.rgb = c.rgb; s.custom = true;
          swEl.style.setProperty("--c", css(c.rgb)); place(c.h, c.s); live.push(c.rgb);
        };
        let dragging = false;
        disc.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; dragging = true; try { disc.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ } paint(e); });
        disc.addEventListener("pointermove", (e) => { if (dragging) paint(e); });
        const release = () => { if (!dragging) return; dragging = false; live.flush(); saveSwatch(s.name, s.rgb); };     // kept for good when the finger lifts
        disc.addEventListener("pointerup", release); disc.addEventListener("pointercancel", release);
        const reset = h("button", { class: "btn", onclick: () => {
          const f = rgbToHsv(fallback); s.rgb = fallback; s.custom = false; swEl.style.setProperty("--c", css(fallback)); place(f.h, f.s); saveSwatch(s.name, null);
        } }, "Reset");
        const el = h("div", { class: "picker panel wheel-pop" },
          h("div", { class: "pk-title" }, s.name, h("span", { class: "dim" }, " · drag to change it")),
          disc, h("div", { class: "pk-row" }, reset, h("button", { class: "btn", onclick: closeWheel }, "Done")));
        document.body.append(el);
        const spot = pickerSpot(swEl.getBoundingClientRect(), el.getBoundingClientRect(), { width: innerWidth, height: innerHeight });
        el.style.left = spot.left + "px"; el.style.top = spot.top + "px";
        wheel = { el, name: s.name };
      }
      const slider = (cap, val, lo, hi, fmt, on) => {
        const v = h("span", {}, fmt(val));
        const s = h("input", { type: "range", class: "slider", min: lo, max: hi, value: val });
        const fill = () => s.style.setProperty("--fill", ((s.value - lo) / (hi - lo)) * 100 + "%");
        s.addEventListener("input", () => { fill(); v.textContent = fmt(+s.value); on(+s.value); }); fill();
        return h("div", { class: "pk-slider" }, h("div", { class: "pk-cap dim" }, h("span", {}, cap), v), s);
      };
      const el = h("div", { class: "picker panel" },
        h("div", { class: "pk-title" }, l.name, h("span", { class: "dim" }, { wiz: " · WiZ", tapo: " · Tapo" }[String(brand).toLowerCase()] || "")),
        h("div", { class: "pk-swatches" }, swatches.map(paletteSw)),
        h("div", { class: "pk-swatches" }, WHITES.map(([n, tp, lab]) => sw(n, whiteTint(tempToK(brand, tp)), lab))),
        slider("BRIGHTNESS", lum, 1, 100, (v) => v + " %", (v) => { lum = v; queue({ lum }); }),
        slider("TEMPERATURE", temp, 0, 100, (v) => tempToK(brand, v) + " K", (v) => { temp = v; queue({ temp, lum }); }));
      document.body.append(el);
      const spot = pickerSpot(anchor.getBoundingClientRect(), el.getBoundingClientRect(), { width: innerWidth, height: innerHeight });
      el.style.left = spot.left + "px"; el.style.top = spot.top + "px";
      picker = { el, t: l.name };
      loadSwatches();                                                       // a change made elsewhere shows the next time it opens
    }
    const away = (e) => {
      if (wheel && !wheel.el.contains(e.target)) closeWheel();             // the wheel closes on any click outside it; the picker may stay
      if (picker && !picker.el.contains(e.target) && !(wheel?.el.contains(e.target)) && !e.target.closest(".disc.pick")) closePicker();
    };
    const escape = (e) => { if (e.key === "Escape" && (wheel || picker)) { wheel ? closeWheel() : closePicker(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);

    const off = bridge.on("jarvis/sys/light_status", (d) => {
      if (!d || typeof d !== "object") return;
      if (d.network) { net.textContent = d.network; }
      lights = d.lights || []; render();
    });
    bridge.act("light.set", { action: "refresh_lights", light_target: "all" });     // fresh status when the window opens
    return { onClose() { off(); closePicker(); document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", escape); } };
  },
};
