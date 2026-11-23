// The Settings window's Lights and Spotify tabs: the `.env` credentials and config/devices.json (GET/POST /api/env, /api/devices; the
// allow-lists are in utils/clWebDevices.py). Secrets (the Tapo password, the Spotify client secret) are never sent to this page: a
// secret field only knows "saved" or "not saved", and writes only when something is typed. The services read both files at start, so
// each tab shows a restart button once something changed (system.restart_module light / music), as the Qt widget does.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";

const section = (title) => h("div", { class: "set-section" }, title);
const note = (text) => h("div", { class: "dim set-note" }, text);
const post = async (url, body) => {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `error ${r.status}`);
  return d;
};

/** A labelled text field saved to one .env key when it changes. */
function envField(key, label, ctx, { secret = false, placeholder = "" } = {}) {
  const input = h("input", { class: "mod-input", type: secret ? "password" : "text", autocomplete: secret ? "new-password" : "off", spellcheck: "false", "aria-label": label, placeholder });
  input.addEventListener("change", async () => {
    if (secret && !input.value) return;                                   // an empty secret box means "leave it as it is"
    try {
      const d = await post("/api/env", { key, value: input.value });
      ctx.saved(); ctx.say("");
      if (secret) { input.value = ""; input.placeholder = d.set ? "saved: type to replace" : placeholder; } else input.value = d.value;
    } catch (e) { ctx.say(`${label}: ${e.message}`); }
  });
  return {
    el: h("label", { class: "field-col" }, h("span", { class: "dim field-label" }, label), input),
    load(env) {
      if (secret) input.placeholder = env.secrets?.[key] ? "saved: type to replace" : placeholder;
      else if (document.activeElement !== input) input.value = env.values?.[key] || "";
    },
  };
}

/** A restart button that appears once something changed. */
function restartBar(label, target) {
  const btn = h("button", { class: "btn mod-wide", hidden: true, onclick: () => { bridge.act("system.restart_module", { target }); btn.hidden = true; } }, label);
  return { el: btn, show: () => { btn.hidden = false; } };
}

/** A button that asks twice before it does something destructive. */
function confirmButton(label, onConfirm, cls = "btn danger") {
  let timer = null;
  const btn = h("button", { class: cls, onclick: () => {
    if (btn.dataset.armed) { clearTimeout(timer); delete btn.dataset.armed; btn.textContent = label; onConfirm(); return; }
    btn.dataset.armed = "1"; btn.textContent = "Click again to confirm";
    timer = setTimeout(() => { delete btn.dataset.armed; btn.textContent = label; }, 3000);
  } }, label);
  return btn;
}

/** A small inline "name" prompt that replaces a button until it is answered. */
function inlinePrompt(button, placeholder, onSubmit) {
  const holder = h("div", { class: "dev-prompt" });
  button.addEventListener("click", () => {
    const input = h("input", { class: "mod-input", placeholder, "aria-label": placeholder, maxlength: 64 });
    const done = (ok) => { const v = input.value.trim(); holder.replaceChildren(button); if (ok && v) onSubmit(v); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
    holder.replaceChildren(input, h("button", { class: "btn", onclick: () => done(true) }, "Add"), h("button", { class: "bare", "aria-label": "Cancel", onclick: () => done(false) }, "×"));
    input.focus();
  });
  holder.append(button);
  return holder;
}

const titleCase = (s) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ---------------------------------------------------------------- Lights
export function lightsTab(page) {
  const restart = restartBar("Save & Reboot Smart Lights", "light"), msg = h("div", { class: "dim set-note err-note", "aria-live": "polite" }, "");
  const ctx = { saved: () => restart.show(), say: (t) => { msg.textContent = t; } };
  const open = new Set();                                                   // which light blocks are expanded (kept across redraws)
  let devices = { networks: {} };

  // global defaults and Tapo credentials (.env)
  const type = h("select", { class: "mod-select", "aria-label": "Default light type" }, [["", "(none)"], ["wiz", "wiz"], ["tapo", "tapo"]].map(([v, t]) => h("option", { value: v }, t)));
  type.addEventListener("change", async () => { try { await post("/api/env", { key: "LIGHT_TYPE", value: type.value }); ctx.saved(); ctx.say(""); } catch (e) { ctx.say(e.message); } });
  const ip = envField("LIGHT_IP", "Default IP Address", ctx, { placeholder: "192.168.1.100" });
  const mac = envField("LIGHT_MAC", "Default MAC Address", ctx, { placeholder: "aa:bb:cc:dd:ee:ff" });
  const email = envField("TAPO_EMAIL", "Email", ctx), password = envField("TAPO_PASSWORD", "Password", ctx, { secret: true });
  page.append(section("Global Light Settings"), h("label", { class: "set-row" }, h("span", { class: "set-label" }, "Default Light Type"), type), ip.el, mac.el,
    section("Tapo Account Credentials"), email.el, password.el);
  fetch("/api/env").then((r) => r.json()).then((env) => { type.value = env.values.LIGHT_TYPE || ""; [ip, mac, email, password].forEach((f) => f.load(env)); }).catch(() => {});

  // WiFi networks and their lights (devices.json)
  const networks = h("div", { class: "dev-nets" });
  const addNet = inlinePrompt(h("button", { class: "btn dashed mod-wide" }, "+ Add WiFi Network"), "WiFi network name (SSID)", (name) => op("add_network", { network: name }));
  page.append(networks, addNet, restart.el, msg);

  async function op(name, args) {
    try { devices = await post("/api/devices", { op: name, ...args }); ctx.saved(); ctx.say(""); draw(); } catch (e) { ctx.say(e.message); draw(); }
  }

  const field = (label, value, onChange, placeholder = "") => {
    const input = h("input", { class: "mod-input", value, placeholder, "aria-label": label, spellcheck: "false" });
    input.addEventListener("change", () => onChange(input.value));
    return h("label", { class: "field-col" }, h("span", { class: "dim field-label" }, label), input);
  };
  const choice = (label, options, value, onChange) => {
    const sel = h("select", { class: "mod-select", "aria-label": label }, options.map((o) => h("option", { value: o }, o)));
    sel.value = value; sel.addEventListener("change", () => onChange(sel.value));
    return h("label", { class: "set-row" }, h("span", { class: "set-label" }, label), sel);
  };

  function lightBlock(net, name, info, allNets) {
    const id = `${net}/${name}`, expanded = open.has(id);
    const sub = info.ip ? `${(info.type || "wiz").toUpperCase()} - ${info.ip}` : (info.type || "wiz").toUpperCase();
    const head = h("button", { class: "dev-light-head", "aria-expanded": String(expanded), onclick: () => { expanded ? open.delete(id) : open.add(id); draw(); } },
      `${expanded ? "▼" : "▶"}  ${name}`, h("span", { class: "dim" }, `  [${sub}]`));
    const body = h("div", { class: "dev-light-body", hidden: !expanded },
      field("Name", name, (v) => { open.delete(id); open.add(`${net}/${v.trim().toLowerCase().replace(/ /g, "_")}`); op("update_light", { network: net, name, field: "name", value: v }); }),
      choice("WiFi Network", allNets, net, (to) => op("move_light", { network: net, name, to })),
      choice("Type", ["wiz", "tapo"], info.type || "wiz", (v) => op("update_light", { network: net, name, field: "type", value: v })),
      field("IP Address", info.ip || "", (v) => op("update_light", { network: net, name, field: "ip", value: v }), "192.168.1.100"),
      field("MAC Address", info.mac || "", (v) => op("update_light", { network: net, name, field: "mac", value: v }), "aa:bb:cc:dd:ee:ff"),
      confirmButton("Remove Light", () => op("remove_light", { network: net, name })));
    return h("div", { class: "dev-light" }, head, body);
  }

  function draw() {
    const names = Object.keys(devices.networks);
    networks.replaceChildren(...names.map((net) => {
      const lights = devices.networks[net];
      const addLight = inlinePrompt(h("button", { class: "btn dashed" }, `+ Add Light to ${net}`), "Light name, e.g. kitchen", (name) => { open.add(`${net}/${name.trim().toLowerCase().replace(/ /g, "_")}`); op("add_light", { network: net, name }); });
      return h("div", { class: "dev-net" },
        h("div", { class: "dev-net-head" }, h("span", { class: "dev-net-name" }, net), confirmButton("Remove Network", () => op("remove_network", { network: net }))),
        ...Object.entries(lights).map(([n, info]) => lightBlock(net, n, info, names)), addLight);
    }));
  }
  fetch("/api/devices").then((r) => r.json()).then((d) => { devices = d; draw(); }).catch(() => ctx.say("Could not load the saved lights."));
  return {};
}

// ---------------------------------------------------------------- Spotify
export function spotifyTab(page) {
  const restart = restartBar("Save & Reboot Spotify", "music"), msg = h("div", { class: "dim set-note err-note", "aria-live": "polite" }, "");
  const ctx = { saved: () => restart.show(), say: (t) => { msg.textContent = t; } };
  const id = envField("SPOTIPY_CLIENT_ID", "Client ID", ctx), secret = envField("SPOTIPY_CLIENT_SECRET", "Client Secret", ctx, { secret: true });
  const uri = envField("SPOTIPY_REDIRECT_URI", "Redirect URI", ctx, { placeholder: "http://127.0.0.1:8888/callback" });
  page.append(section("API Credentials"), note("From your app on developer.spotify.com. The client secret is stored in .env and is never shown here."),
    id.el, secret.el, uri.el, restart.el, msg);
  fetch("/api/env").then((r) => r.json()).then((env) => [id, secret, uri].forEach((f) => f.load(env))).catch(() => {});
  return {};
}
