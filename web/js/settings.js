// Settings: System, Updates and Debug tabs (the rest of the Qt Settings widget is still to port).
// Data: GET/POST /api/settings (clWebUI.py -> utils/clWebSettings.py, an allow-list over core.json). A change is saved
// there, then the live action that goes with it is dispatched from here, exactly as the Qt widget did.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";
import { audioTab, keybindsTab, presenceTab } from "./settings-tabs.js";
import { lightsTab, spotifyTab } from "./settings-lights.js";

const toggle = (key, label, after) => ({ kind: "toggle", key, label, after });
const select = (key, label, options, extra = {}) => ({ kind: "select", key, label, options, ...extra });
const flag = (name, label) => toggle(`debug_flags.${name}`, label, (on) => bridge.act("debug.set_flag", { flag: name, enabled: on }));

const TABS = [
  { id: "system", label: "System", sections: [
    { title: "Behavior", rows: [
      toggle("silent_mode", "Silent Mode (No Voice Output)", (on) => bridge.act("state.daemon", { action: on ? "silent_mode_on" : "silent_mode_off" })),
      toggle("enable_followup", "Follow-ups", (on) => bridge.act("state.daemon", { action: on ? "followup_on" : "followup_off" })),
    ] },
    { title: "Startup", rows: [toggle("start_on_login", "Start JARVIS when I log in")] },
    { title: "Persona (look and voice, needs a reboot)", rows: [select("theme", "Assistant", ["jarvis", "ultron"], { reboot: true })] },
    { title: "System Core", rows: [select("ecosystem_state", "Ecosystem Mode", ["normal", "debug", "background"], { after: (v) => bridge.act("state.change", { action: v }) })] },
    { title: "Inference Engine", rows: [
      select("stt_model", "STT Model Size", ["tiny", "base", "small", "medium", "large"], { reboot: true }),
      select("stt_processing_mode", "Multi-Language Transcription", ["parallel", "sequential"], { reboot: true }),
      select("hardware", "Hardware Acceleration", ["cpu", "cuda"], { reboot: true }),
    ] },
  ] },
  { id: "lights", label: "Lights", custom: lightsTab },
  { id: "spotify", label: "Spotify", custom: spotifyTab },
  { id: "updates", label: "Updates", sections: [
    { title: "Update Settings", rows: [select("update_mode", "Update Mode", ["confirm", "direct"])] },
  ] },
  { id: "keybinds", label: "Keybinds", custom: keybindsTab },
  { id: "audio", label: "Audio", custom: audioTab },
  { id: "presence", label: "Presence", custom: presenceTab },
  { id: "debug", label: "Debug", sections: [
    { title: "Diagnostics (console spam, off by default)", rows: [
      flag("wakeword_debug_logging", "Wake Word Confidence Logs"),
      flag("wakeword_debug_saving", "Wake Word Debug Clips (saved to data/scratch)"),
    ] },
    { title: "Training Data Capture (for future model retraining)", rows: [
      flag("capture_wakeword_positive", "Save successful wake word triggers"),
      flag("capture_stt_training_data", "Save correct STT-to-action pairs"),
    ] },
  ] },
];

const valueOf = (values, key) => (key.startsWith("debug_flags.") ? values.debug_flags?.[key.slice(12)] : values[key]);

export const settingsWindow = {
  id: "widget_settings", title: "Settings", w: 470, h: 500,
  build(body) {
    let values = {}, tab = TABS[0].id;
    const controls = new Map();                   // key -> set(value): the page is built once and synced in place (an open dropdown survives)
    const tabs = h("div", { class: "tabs-pill" });
    const pages = h("div", { class: "set-pages" });
    const reboot = h("button", { class: "btn mod-wide set-reboot", hidden: true, onclick: () => {
      bridge.act("system.restart_all"); reboot.hidden = true;
    } }, "Save & Reboot Ecosystem");
    body.append(h("div", { class: "mod" }, h("div", { class: "mod-head" }, h("div", { class: "mod-title" }, "Settings")), tabs, pages, reboot));

    async function save(row, value) {
      try {
        const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: row.key, value }) });
        if (!r.ok) throw new Error(r.status);
        const stored = (await r.json()).value;
        if (stored !== value) controls.get(row.key)?.(stored);     // e.g. start on login that the OS refused
        else { row.after?.(value); if (row.reboot) reboot.hidden = false; }
      } catch { load(); }                                           // the write failed: show what is really saved
    }

    function control(row) {
      if (row.kind === "toggle") {
        const sw = h("button", { class: "switch", role: "switch", "aria-label": row.label, onclick: () => {
          const on = !sw.classList.contains("on"); set(on); save(row, on);
        } }, h("i"));
        const set = (on) => { sw.classList.toggle("on", !!on); sw.setAttribute("aria-checked", String(!!on)); };
        controls.set(row.key, set);
        return sw;
      }
      const sel = h("select", { class: "mod-select", "aria-label": row.label, onchange: () => save(row, sel.value) },
        row.options.map((o) => h("option", { value: o }, o)));
      controls.set(row.key, (v) => { if (v != null) sel.value = v; });
      return sel;
    }

    const customs = [], customById = new Map();   // the tabs with their own builders: {sync(values)?, show()?, destroy()?}
    for (const t of TABS) {
      const page = h("div", { class: "set-page", "data-tab": t.id });
      if (t.custom) { const c = t.custom(page) || {}; customs.push(c); customById.set(t.id, c); }
      for (const s of t.sections || []) {
        page.append(h("div", { class: "set-section" }, s.title));
        for (const row of s.rows) page.append(h("label", { class: "set-row" }, h("span", { class: "set-label" }, row.label), control(row)));
      }
      pages.append(page);
    }
    function showTab() {
      tabs.replaceChildren(...TABS.map((t) => h("button", { class: "tab-pill", "aria-pressed": String(t.id === tab), onclick: () => { tab = t.id; showTab(); customById.get(t.id)?.show?.(); } }, t.label)));
      for (const p of pages.children) p.hidden = p.dataset.tab !== tab;
    }
    function sync() { for (const [key, set] of controls) set(valueOf(values, key)); customs.forEach((c) => c.sync?.(values)); }

    async function load() {
      try { values = await (await fetch("/api/settings")).json(); sync(); } catch { /* server not up yet: the next poll retries */ }
    }
    showTab(); load();
    // core.json can change under us (a voice command flips silent mode), so follow it like the Qt widget does
    const poll = setInterval(() => { if (!pages.contains(document.activeElement) || document.activeElement.tagName !== "SELECT") load(); }, 3000);
    return { onClose: () => { clearInterval(poll); customs.forEach((c) => c.destroy?.()); } };
  },
};
