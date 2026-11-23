import { bridge } from "./bridge.js";
import { startOrb } from "./orb.js";
import { startClock } from "./clock.js";
import { setSkin } from "./skin.js";
import { brain } from "./state.js";
import { h, icon, toggleWindow, isOpen, openWindow, loadPinned } from "./ui.js";
import { mediaWindow } from "./media.js";
import { lightsWindow } from "./lights.js";
import { todoWindow } from "./todo.js";
import { notesWindow } from "./notes.js";
import { remindersWindow } from "./reminders.js";
import { settingsWindow } from "./settings.js";
import { startAudioPills } from "./audiopills.js";
import { logsWindow } from "./logs.js";
import { updatesWindow } from "./updates.js";
import { claudeWindow } from "./claude.js";
import { startCalendar } from "./calendar.js";
import { startShell, currentMode } from "./shell.js";
import { bindPillLabel } from "./pillmarquee.js";
import "./lyrics.js";

// One fluid unit (UIScaler.scale_for): min(long/1920, short/1080), so a screen scales the same turned either way.
let U = 1;
function setUnit() {
  // the overlay's window is a small box: its unit comes from the monitor it sits on, so the orb is the size it is on the dashboard
  const box = currentMode() === "overlay" ? screen : { width: innerWidth, height: innerHeight };
  const long = Math.max(box.width, box.height), short = Math.min(box.width, box.height);
  U = Math.min(long / 1920, short / 1080);
  document.documentElement.style.setProperty("--u", U + "px");
  fitDock();
}

const conn = document.getElementById("conn");
bridge.onConnection((up) => { conn.classList.toggle("off", !up); conn.textContent = up ? "" : "connecting…"; });

fetch("/api/config").then(r => r.json()).catch(() => ({})).then((settings) => {
  setSkin(settings.theme);
  showState(settings.ecosystem_state === "debug");
  startClock(settings.clock_settings);
});

// "State: IDLE" top-left, only in debug mode, like the Qt overlay
function showState(on) {
  const el = document.getElementById("state");
  el.hidden = !on;
  if (on) { const set = (st) => { el.textContent = `State: ${st}`; }; set(brain.state); brain.listeners.add(set); }
}

// ----- dock: one capsule pill per widget (left of the command bar), the command bar itself -----
// id, icon, label: the same order and labels as the Qt dock. Only the widgets ported so far have a window.
const WIDGETS = [
  { def: mediaWindow, icon: "music.svg", label: "Music" },
  { def: lightsWindow, icon: "lights.svg", label: "Lights" },
  { def: remindersWindow, icon: "reminders.svg", label: "Reminders" },
  { def: todoWindow, icon: "todos.svg", label: "To-Do List" },
  { def: notesWindow, icon: "notes.svg", label: "Quick Notes" },
  { def: settingsWindow, icon: "settings.svg", label: "Settings" },
  { def: updatesWindow, icon: "updates.svg", label: "Updates" },
  { def: claudeWindow, icon: "claude.svg", label: "Claude" },
  { def: logsWindow, icon: "debug.svg", label: "Debug Logs" },
];
const PILL_LABEL_CAP = 170;          // design px: the widest a pill's label opens (a longer text scrolls)
const dock = document.getElementById("dock"), pillRow = document.getElementById("pills");
const pills = WIDGETS.map((w) => {
  const text = h("span", { class: "mq" }, w.label), label = h("span", { class: "label" }, text);
  const b = h("button", { class: "pill", "aria-label": w.def ? w.label : `${w.label} (not ported yet)`,      // no title: the label opening on hover is the hint
    onclick: () => w.def && toggleWindow(w.def) }, h("span", { class: "icon-slot" }, icon(w.icon, { size: 20 })), label);
  if (!w.def) b.style.opacity = ".45";
  bindPillLabel(b, label, text, () => PILL_LABEL_CAP * U);
  pillRow.append(b);
  return { b, def: w.def };
});
window.addEventListener("windows-changed", () => pills.forEach(({ b, def }) => def && b.classList.toggle("on", isOpen(def.id))));

// The row wraps above the bar when the margin beside it is too narrow (a portrait or small window), like a flex row.
function fitDock() {
  const room = (innerWidth - Math.min(600 * U, innerWidth - 40 * U)) / 2 - 40 * U;
  const need = pillRow.scrollWidth ? 9 * 44 * U + 8 * 8 * U : 0;
  dock.classList.toggle("compact", room < need);
  document.body.classList.toggle("dock-compact", room < need);      // the lyrics rise above the wrapped rows (the mic/speaker pills sit highest)
}

// ----- command bar: Enter sends the text as a voice command (voice.submit), like the Qt bar -----
const cmd = document.getElementById("cmd");
cmd.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const text = cmd.value.trim();
  if (text) bridge.act("voice.submit", { text });
  cmd.value = "";
});

startAudioPills(document.getElementById("audio-pills"));
const calendar = startCalendar();
if (new URLSearchParams(location.search).get("open")?.split(",").includes("calendar")) calendar.toggle(true);
setUnit(); addEventListener("resize", setUnit);
startShell(setUnit);          // in the desktop shell: <html data-mode> follows the window's mode, ui_control switches it
startOrb(document.getElementById("orb"), () => U);

// pinned windows come back after a restart; ?open=media,... opens more (handy for screenshots)
const byId = (id) => WIDGETS.find(x => x.def && (x.def.id === id || x.label.toLowerCase() === id));
for (const id of loadPinned()) { const w = byId(id); if (w) openWindow(w.def); }
for (const id of (new URLSearchParams(location.search).get("open") || "").split(",").filter(Boolean)) {
  const w = byId(id); if (w && !isOpen(w.def.id)) openWindow(w.def);
}
