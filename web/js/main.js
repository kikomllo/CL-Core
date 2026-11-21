import { bridge } from "./bridge.js";
import { startOrb } from "./orb.js";
import { startClock } from "./clock.js";
import { setSkin } from "./skin.js";
import { h, icon, toggleWindow, isOpen, openWindow, loadPinned } from "./ui.js";
import { mediaWindow } from "./media.js";
import { lightsWindow } from "./lights.js";
import { todoWindow } from "./todo.js";
import { notesWindow } from "./notes.js";
import { remindersWindow } from "./reminders.js";
import "./lyrics.js";

// One fluid unit (UIScaler.scale_for): min(long/1920, short/1080), so a screen scales the same turned either way.
let U = 1;
function setUnit() {
  const long = Math.max(innerWidth, innerHeight), short = Math.min(innerWidth, innerHeight);
  U = Math.min(long / 1920, short / 1080);
  document.documentElement.style.setProperty("--u", U + "px");
  fitDock();
}

const conn = document.getElementById("conn");
bridge.onConnection((up) => { conn.classList.toggle("off", !up); conn.textContent = up ? "" : "connecting…"; });

fetch("/api/config").then(r => r.json()).catch(() => ({})).then((settings) => {
  setSkin(settings.theme);
  startClock(settings.clock_settings);
});

// ----- dock: one capsule pill per widget (left of the command bar), the command bar itself -----
// id, icon, label: the same order and labels as the Qt dock. Only the widgets ported so far have a window.
const WIDGETS = [
  { def: mediaWindow, icon: "music.svg", label: "Music" },
  { def: lightsWindow, icon: "lights.svg", label: "Lights" },
  { def: remindersWindow, icon: "reminders.svg", label: "Reminders" },
  { def: todoWindow, icon: "todos.svg", label: "To-Do List" },
  { def: notesWindow, icon: "notes.svg", label: "Quick Notes" },
  { icon: "settings.svg", label: "Settings" },
  { icon: "updates.svg", label: "Updates" },
  { icon: "claude.svg", label: "Claude" },
  { icon: "debug.svg", label: "Debug Logs" },
];
const dock = document.getElementById("dock"), pillRow = document.getElementById("pills");
const pills = WIDGETS.map((w) => {
  const b = h("button", { class: "pill", "aria-label": w.label, title: w.def ? "" : `${w.label} (not ported yet)`,
    onclick: () => w.def && toggleWindow(w.def) }, h("span", { class: "icon-slot" }, icon(w.icon, { size: 20 })), h("span", { class: "label" }, w.label));
  if (!w.def) b.style.opacity = ".45";
  pillRow.append(b);
  return { b, def: w.def };
});
window.addEventListener("windows-changed", () => pills.forEach(({ b, def }) => def && b.classList.toggle("on", isOpen(def.id))));

// The row wraps above the bar when the margin beside it is too narrow (a portrait or small window), like a flex row.
function fitDock() {
  const room = (innerWidth - Math.min(600 * U, innerWidth - 40 * U)) / 2 - 40 * U;
  const need = pillRow.scrollWidth ? 9 * 44 * U + 8 * 8 * U : 0;
  dock.classList.toggle("compact", room < need);
}

// ----- command bar: Enter sends the text as a voice command (voice.submit), like the Qt bar -----
const cmd = document.getElementById("cmd");
cmd.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const text = cmd.value.trim();
  if (text) bridge.act("voice.submit", { text });
  cmd.value = "";
});

setUnit(); addEventListener("resize", setUnit);
startOrb(document.getElementById("orb"), () => U);

// pinned windows come back after a restart; ?open=media,... opens more (handy for screenshots)
const byId = (id) => WIDGETS.find(x => x.def && (x.def.id === id || x.label.toLowerCase() === id));
for (const id of loadPinned()) { const w = byId(id); if (w) openWindow(w.def); }
for (const id of (new URLSearchParams(location.search).get("open") || "").split(",").filter(Boolean)) {
  const w = byId(id); if (w && !isOpen(w.def.id)) openWindow(w.def);
}
