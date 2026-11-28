// The startup cover. The supervisor publishes where the boot is (jarvis/sys/boot_state, retained); while it is under way <html data-boot="1"> keeps
// the windows and the dock off the screen (css/26-boot.css), the orb runs its boot look (orb.js listens for "jarvis-boot") and, on the dashboard,
// a readout under the orb lists the modules as they report in. The rules are in boot-model.js. Floating windows (widget.html) only get the
// attribute: they stay clear until the system is online, then come in.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";
import { nextPhase, covering, describe, DEFAULT_TIMEOUT_MS } from "./boot-model.js";

const SETTLE_FALLBACK_MS = 2500;            // no boot state at all (no supervisor): do not hold the windows back for ever

/** Starts the cover. `ready` resolves when the windows may open: the system is online (or the boot gave up waiting, or there is no cover). The
 *  windows ask for their data when they open, and a service that is still starting does not answer, so they open only after everything is up
 *  (they stay hidden until the cover lifts anyway). */
export function startBoot({ readout = true } = {}) {
  const root = document.documentElement;
  let settle; const ready = new Promise((resolve) => { settle = resolve; });
  setTimeout(() => { if (!phase) settle(); }, SETTLE_FALLBACK_MS);
  let state = null, phase = null, enabled = true, timeoutMs = DEFAULT_TIMEOUT_MS, loaded = false, el = null, rows = new Map(), timer = 0;
  const ui = {};

  fetch("/api/config").then((r) => r.json()).catch(() => ({})).then((s) => {
    const c = (s && s.boot_cover) || {};
    enabled = c.enabled !== false;
    timeoutMs = (Number(c.timeout_s) || DEFAULT_TIMEOUT_MS / 1000) * 1000;
    loaded = true; evaluate();
  });
  bridge.on("jarvis/sys/boot_state", (p) => { if (p && typeof p.started === "number") { state = p; evaluate(); } });

  function build() {
    ui.title = h("div", { class: "boot-title" }, "Starting JARVIS");
    ui.fill = h("i"); ui.count = h("div", { class: "boot-count" }); ui.sub = h("div", { class: "boot-sub" });
    ui.time = h("span", { class: "win-time" }); ui.log = h("ul", { class: "boot-log" });
    el = h("div", { id: "boot", class: "gone", role: "status", "aria-live": "polite" },
      h("div", { class: "boot-stack" }, ui.title, h("div", { class: "boot-rule" }, ui.fill), ui.count, ui.sub),
      h("div", { class: "win panel boot-win" },
        h("div", { class: "win-head" }, h("div", { class: "win-dot" }), h("span", { class: "win-title" }, "Startup"), ui.time), ui.log));
    document.body.append(el);
  }

  function render(since) {
    const d = describe(state, phase.phase, since);
    ui.title.textContent = d.title; ui.count.textContent = d.count; ui.sub.textContent = d.sub;
    ui.sub.classList.toggle("warn", d.warn);
    ui.fill.style.width = `${(d.progress * 100).toFixed(1)}%`;
    ui.time.textContent = `${(since / 1000).toFixed(1)} s`;
    const n = d.rows.length, cols = Math.min(4, Math.max(2, Math.ceil(n / 5))), per = Math.ceil(n / cols);
    ui.log.style.setProperty("--cols", cols); ui.log.style.setProperty("--rows", per);
    d.rows.forEach((r, i) => {
      let li = rows.get(r.name);
      if (!li) { li = h("li", {}, h("span", { class: "d" }, ""), h("span", { class: "nm" }, r.name), h("span", { class: "tm" }, "")); rows.set(r.name, li); ui.log.append(li); }
      const cls = `${r.state}${(i + 1) % per === 0 || i === n - 1 ? " col-end" : ""}`;
      if (li.className !== cls) li.className = cls;
      const tm = li.lastChild; if (tm.textContent !== r.time) tm.textContent = r.time;
    });
  }

  function evaluate() {
    if (!loaded) return;
    const now = Date.now(), was = phase && covering(phase.phase);
    phase = nextPhase(phase, state, now, timeoutMs);
    if (!enabled || phase.phase === "online" || phase.phase === "done") settle();
    const on = enabled && covering(phase.phase);
    if (on && readout) { if (!el) build(); render(now - phase.since); el.classList.remove("gone"); }
    else if (el) el.classList.add("gone");
    if (on) root.dataset.boot = "1"; else delete root.dataset.boot;
    if (on !== !!was) window.dispatchEvent(new CustomEvent("jarvis-boot", { detail: { on } }));
    if (on && !timer) timer = setInterval(evaluate, 500);        // the clock alone ends a stalled boot and the "System online" hold
    else if (!on && timer) { clearInterval(timer); timer = 0; }
  }
  return { ready };
}
