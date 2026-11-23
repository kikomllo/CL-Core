// The Settings window's Audio, Keybinds and Presence tabs (the same controls as the Qt Settings widget). Each builder fills a tab page
// and returns {sync(values)?, destroy()?}: sync gets the polled /api/settings snapshot, destroy runs when the window closes.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";
import { loadDevices, setDevice } from "./audiopills.js";
import { comboFromEvent, pttFromCode, pretty } from "./keycapture.js";
import { presenceText, pairingSteps, pairingResult, countdownText, once } from "./presence-model.js";

const section = (title) => h("div", { class: "set-section" }, title);
const note = (text) => h("div", { class: "dim set-note" }, text);
const row = (label, control) => h("label", { class: "set-row" }, h("span", { class: "set-label" }, label), control);
const JSON_POST = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** A switch that saves `key` through /api/settings; set(on) updates it from outside without saving. */
function settingSwitch(label, key, onSaved) {
  const sw = h("button", { class: "switch", role: "switch", "aria-label": label, onclick: async () => {
    const on = !sw.classList.contains("on"); set(on);
    try { const r = await JSON_POST("/api/settings", { key, value: on }); if (!r.ok) throw new Error(r.status); onSaved?.(on); } catch { set(!on); }
  } }, h("i"));
  const set = (on) => { sw.classList.toggle("on", !!on); sw.setAttribute("aria-checked", String(!!on)); };
  return { el: row(label, sw), set };
}

// ---------------------------------------------------------------- Audio
export function audioTab(page) {
  page.append(section("Devices (applies live, no reboot needed)"));
  const selects = {};
  for (const [kind, label] of [["input", "Microphone"], ["output", "Speaker"]]) {
    const sel = h("select", { class: "mod-select", "aria-label": label, disabled: true }, h("option", {}, "Loading…"));
    selects[kind] = sel;
    page.append(row(label, sel));
    loadDevices(kind).then((d) => {                                     // the first listing can take seconds, so the box says so meanwhile
      sel.replaceChildren(...d.options.map((o) => h("option", { value: o.value, title: o.value }, o.label)));
      sel.value = d.current; sel.disabled = false;
    }).catch(() => { sel.replaceChildren(h("option", {}, "Could not list devices")); });
    sel.addEventListener("change", () => setDevice(kind, sel.value));
  }
  return {
    sync(values) {                                                      // a device changed elsewhere (a pill, a voice command)
      for (const [kind, sel] of Object.entries(selects)) {
        const cur = values.audio_settings?.[`${kind}_device`];
        if (cur && !sel.disabled && sel.value !== cur && document.activeElement !== sel) sel.value = cur;
      }
    },
  };
}

// ---------------------------------------------------------------- Keybinds
export function keybindsTab(page) {
  page.append(section("Global Shortcuts"));
  const rows = h("div", { class: "kb-rows" }), msg = h("div", { class: "dim set-note", "aria-live": "polite" }, "");
  page.append(rows, msg);
  const save = async (action, field, value) => {
    try {
      const r = await JSON_POST("/api/keybinds", { action, field, value });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      msg.textContent = ""; return true;
    } catch (e) { msg.textContent = `Could not save: ${e.message}`; return false; }
  };

  function capture(action, entry) {
    const isPtt = action === "push_to_talk";
    let committed = entry.key, recording = false;
    const input = h("input", { class: "mod-input kb-capture", readonly: true, value: pretty(committed), "aria-label": `${action} shortcut`,
      title: "Click, then press the keys. Esc clears it." });
    input.addEventListener("focus", () => { recording = true; input.value = isPtt ? "Press a key..." : "Press keys..."; });
    input.addEventListener("blur", () => { recording = false; input.value = pretty(committed); });
    input.addEventListener("keydown", async (e) => {
      if (!recording || e.repeat) return;
      e.preventDefault(); e.stopPropagation();
      let result;
      if (e.code === "Escape") result = { clear: true };
      else if (isPtt) { const k = pttFromCode(e.code); result = k ? { value: k } : { error: "Unsupported -- Ctrl/Alt/Shift/CapsLock/Space/F13-F24 only" }; }
      else result = comboFromEvent(e);
      if (result.pending) return;                                       // a bare modifier: keep waiting for the trigger key
      if (result.error) { input.value = result.error; return; }
      const value = result.clear ? "" : result.value;
      if (await save(action, "key", value)) committed = value;
      input.blur();
    });
    return input;
  }

  async function load() {
    let data;
    try { data = await (await fetch("/api/keybinds")).json(); } catch { rows.replaceChildren(note("Failed to load keybinds.")); return; }
    rows.replaceChildren(...Object.entries(data).map(([action, entry]) => {
      const mode = h("select", { class: "mod-select kb-mode", "aria-label": `${action} mode` }, ["single", "continuous"].map((m) => h("option", { value: m }, m)));
      mode.value = entry.mode;
      mode.addEventListener("change", () => save(action, "mode", mode.value));
      return h("div", { class: "set-row kb-row" }, h("span", { class: "set-label" }, action), capture(action, entry), mode);
    }));
  }
  page.append(h("button", { class: "btn mod-wide", title: "Restart the shortcut listener so changes apply",
    onclick: () => bridge.act("system.restart_module", { target: "keybinds" }) }, "Refresh Keybinds"));
  load();
  return {};
}

// ---------------------------------------------------------------- Presence
// The monitor SPEAKS its answer to monitor.status ("No device is paired..."), so it is asked once per session and only when the user opens this tab:
// asking while the window was built made every open of Settings say it. The tab itself shows the state from the retained presence topic.
const askStatusOnce = once(() => bridge.act("monitor.status"));

export function presenceTab(page) {
  let presence = null, pairing = null, countdownTimer = null, lastCode = null, names = [];
  const offs = [];

  // paired device
  page.append(section("Paired Device"));
  const status = h("div", { class: "set-text" }, presenceText(null));
  const testMode = h("input", { type: "checkbox", id: "presence-test" });
  const testLabel = h("label", { class: "dim kb-test", title: "The phone broadcasts one fixed value instead of a rotating token. Anyone nearby could copy it, so use this for testing only." },
    testMode, " Test mode: use a phone advertiser app (e.g. nRF Connect)");
  const pairBtn = h("button", { class: "btn", onclick: () => bridge.act("monitor.pair", { static: testMode.checked }) }, "Pair new device");
  const cancelBtn = h("button", { class: "btn", onclick: () => bridge.act("monitor.cancel") }, "Cancel pairing");
  const unpairBtn = h("button", { class: "btn", onclick: () => bridge.act("monitor.unpair") }, "Unpair");
  const buttons = h("div", { class: "set-btnrow" }, pairBtn, cancelBtn, unpairBtn);
  const message = h("div", { class: "set-text dim", "aria-live": "polite" }, "");

  const value = (title) => {                                              // a titled, selectable value with a Copy button
    const v = h("div", { class: "set-value" }, ""), head = h("div", { class: "dim set-note" }, title);
    const copy = h("button", { class: "btn", onclick: () => { if (v.textContent) { navigator.clipboard?.writeText(v.textContent).catch(() => {}); message.textContent = `${title} copied.`; } } }, "Copy");
    return { el: h("div", { class: "set-valrow" }, head, h("div", { class: "set-valline" }, v, copy)), v };
  };
  const steps = h("div", { class: "set-text set-steps" }, ""), uuid = value("Service UUID"), code = value("Pairing code"), data = value("Service data (hex)");
  const hint = h("div", { class: "dim set-note" }, "");
  const panel = h("div", { class: "set-panel", hidden: true }, section("Pair your device"), steps, uuid.el, code.el, data.el, hint);
  page.append(status, testLabel, buttons, panel, message);

  function view() {
    status.textContent = presenceText(presence);
    const waiting = !!(pairing && pairing.state === "waiting");
    pairBtn.hidden = waiting; testLabel.hidden = waiting; cancelBtn.hidden = !waiting;
    unpairBtn.hidden = !((presence && presence.paired) && !waiting);
    panel.hidden = !waiting;
    clearInterval(countdownTimer);
    if (!waiting) return;
    const isStatic = !!pairing.static;
    steps.textContent = pairingSteps(pairing);
    code.el.hidden = isStatic; data.el.hidden = !isStatic;
    code.v.textContent = pairing.code || "";
    if (pairing.code !== lastCode) {                                      // the beacon's UUID (and the test-mode service data) come from the server
      lastCode = pairing.code; uuid.v.textContent = ""; data.v.textContent = "";
      fetch(`/api/pairing-info?${new URLSearchParams({ code: pairing.code || "", static: isStatic ? "1" : "0" })}`).then((r) => r.json())
        .then((d) => { uuid.v.textContent = d.uuid; data.v.textContent = d.data || ""; }).catch(() => {});
    }
    const tick = () => { hint.textContent = countdownText(pairing.expires_at, Date.now() / 1000); };
    tick(); countdownTimer = setInterval(tick, 1000);
  }
  offs.push(bridge.on("jarvis/sys/presence", (p) => { presence = p; view(); }));
  offs.push(bridge.on("jarvis/monitor/pairing", (p) => {
    if (!p || typeof p !== "object") return;
    if (p.state === "waiting") { pairing = p; message.textContent = ""; } else { pairing = null; lastCode = null; message.textContent = pairingResult(p); }
    view();
  }));
  view();

  // presence lights
  page.append(section("Presence Lights"),
    note("When your paired device arrives in the evening (19:00 to 07:00), the chosen lights turn on. When it leaves, they turn off."));
  const lightsEnabled = settingSwitch("Control lights with my presence", "automation_settings.presence_lights.enabled");
  const lightList = h("div", { class: "set-lights" }), noLights = note("No saved lights yet. Add lights first, then pick them here.");
  page.append(lightsEnabled.el, lightList, noLights);
  const lightSwitches = new Map();
  function showLights(values) {
    const rule = values.presence_lights || {}, all = values.light_names || [];
    if (all.join("|") !== names.join("|")) {
      names = all; lightSwitches.clear();
      lightList.replaceChildren(...all.map((n) => {
        const s = settingSwitch(n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), `automation_settings.presence_lights.light.${n}`);
        lightSwitches.set(n, s); return s.el;
      }));
      noLights.hidden = all.length > 0;
    }
    lightsEnabled.set(rule.enabled);
    for (const [n, s] of lightSwitches) s.set((rule.lights || []).includes(n));
  }

  // Wi-Fi gate
  page.append(section("Wi-Fi Gate"),
    note("Optional: stop scanning entirely until your phone joins this Wi-Fi network, then scan as usual for room-level detection. Saves power when nobody's home. "
      + "Needs your phone's Wi-Fi MAC address to stay fixed on this network (in the phone's Wi-Fi settings, turn off 'Randomized MAC' / 'Private Wi-Fi address' "
      + "for this network, then copy the address shown there)."));
  const gate = settingSwitch("Only scan while my phone is on the home Wi-Fi", "monitor_settings.wifi_gate_enabled");
  const mac = h("input", { class: "mod-input", spellcheck: "false", "aria-label": "Phone's Wi-Fi MAC address", placeholder: "aa:bb:cc:dd:ee:ff" });
  mac.addEventListener("change", () => JSON_POST("/api/settings", { key: "monitor_settings.wifi_mac", value: mac.value }).catch(() => {}));
  page.append(gate.el, row("Phone's Wi-Fi MAC address", mac));

  return {
    sync(values) {
      showLights(values);
      gate.set(values.monitor_settings?.wifi_gate_enabled);
      if (document.activeElement !== mac) mac.value = values.monitor_settings?.wifi_mac || "";
    },
    show: askStatusOnce,                                                   // settings.js calls it when the user opens this tab
    destroy() { clearInterval(countdownTimer); offs.forEach((off) => off()); },
  };
}
