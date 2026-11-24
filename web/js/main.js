import { bridge } from "./bridge.js";
import { startOrb } from "./orb.js";
import { startClock } from "./clock.js";
import { setSkin } from "./skin.js";
import { brain } from "./state.js";
import { h, icon, toggleWindow, isOpen, openWindow, closeWindow, openIds, canFloat } from "./ui.js";
import { parseIds, windowsToRestore } from "./session-model.js";
import { getGlobal, startZoomInput } from "./zoom.js";
import { windowForAction, showPlan } from "./ui-control-model.js";
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
import { startShell, currentMode, inShell, startedAsRestart } from "./shell.js";
import { bindPillLabel } from "./pillmarquee.js";
import "./lyrics.js";

// One fluid unit (UIScaler.scale_for): min(long/1920, short/1080), so a screen scales the same turned either way.
let U = 1;
function setUnit() {
  // the overlay's window is a small box: its unit comes from the monitor it sits on, so the orb is the size it is on the dashboard
  const box = currentMode() === "overlay" ? screen : { width: innerWidth, height: innerHeight };
  const long = Math.max(box.width, box.height), short = Math.min(box.width, box.height);
  // the zoom (Ctrl + / - / 0, Settings) is a factor on the unit; the overlay is a fixed box and is not zoomed
  U = Math.min(long / 1920, short / 1080) * (currentMode() === "overlay" ? 1 : getGlobal());
  document.documentElement.style.setProperty("--u", U + "px");
  document.documentElement.style.setProperty("--ug", U + "px");      // the same, for a window that has a zoom of its own on top
  fitDock();
  window.dispatchEvent(new Event("jarvis-unit"));                      // windows refit (ui.js), terminals measure their cells again
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
// Windows that are unpinned float as windows of their own (shell/main.js); the dock pill of one closes it, and the pill shows it as on.
const SHELL = window.jarvisShell || null;
let floating = new Set(canFloat ? SHELL.floating() : []);
const toggleAny = (def) => { if (canFloat && floating.has(def.id)) SHELL.closeFloating(def.id); else toggleWindow(def); };
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
    onclick: () => w.def && toggleAny(w.def) }, h("span", { class: "icon-slot" }, icon(w.icon, { size: 20 })), label);
  if (!w.def) b.style.opacity = ".45";
  bindPillLabel(b, label, text, () => PILL_LABEL_CAP * U);
  pillRow.append(b);
  return { b, def: w.def };
});
// the windows that are open are remembered (not just the pinned ones), so a reload or a restart of the UI puts them back; saved only once the
// restore below is done, or a half-restored page would overwrite the list it is still reading
const OPEN_KEY = "jarvis.open.v1";
let restored = false;
const paintPills = () => pills.forEach(({ b, def }) => def && b.classList.toggle("on", isOpen(def.id) || floating.has(def.id)));
if (canFloat) {
  SHELL.onFloating((ids) => { floating = new Set(ids); paintPills(); });
  SHELL.onDocked((id, rect) => dockBack(id, rect));         // pinned again from its own window: back into the dashboard, where it was
}
window.addEventListener("windows-changed", () => {
  paintPills();
  if (restored) { try { localStorage.setItem(OPEN_KEY, JSON.stringify(openIds())); } catch { /* private mode */ } }
});

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
setUnit(); addEventListener("resize", setUnit); addEventListener("jarvis-zoom", setUnit); startZoomInput();
startShell(setUnit);          // in the desktop shell: <html data-mode> follows the window's mode, ui_control switches it
startOrb(document.getElementById("orb"), () => U);

// "show the logs" / "show my todo list" (jarvis/sys/ui_control): in the dashboard the window opens there; while only the overlay is up it floats as a
// window of its own on top of other apps, like an unpinned Qt widget, and the overlay stays as it is.
const pendingShows = new Set();                                   // windows to open in the dashboard once it is up and laid out
const openPending = () => {
  if (currentMode() === "overlay") return;                         // the settle of some other change
  window.removeEventListener("jarvis-mode-settled", openPending);
  for (const def of pendingShows) openWindow(def);                 // openWindow focuses a window that is already open
  pendingShows.clear();
};
// The floating window is closed only after this copy is drawn exactly where it was (SHELL.dockReady), so the hand-over has no gap.
function dockBack(id, rect) {
  const w = byId(id);
  if (!w) return SHELL.dockReady(id);
  if (currentMode() === "overlay") { openWhenDrawn(w.def); return SHELL.dockReady(id); }      // nothing is drawn in the overlay: it opens with the dashboard
  if (!isOpen(w.def.id)) openWindow(w.def, rect && Number.isFinite(rect.x) ? rect : undefined);
  requestAnimationFrame(() => requestAnimationFrame(() => SHELL.dockReady(id)));          // two frames: it has been painted
}
function openWhenDrawn(def) {
  if (currentMode() !== "overlay") return void openWindow(def);
  pendingShows.add(def); window.addEventListener("jarvis-mode-settled", openPending);       // the overlay draws no windows: it opens with the dashboard
}
bridge.on("jarvis/sys/ui_control", (p) => {
  const w = byId(windowForAction(p && p.action));
  if (!w) return;
  if (canFloat && floating.has(w.def.id)) return SHELL.float(w.def.id);        // already floating: bring it forward
  if (showPlan(currentMode(), canFloat) === "float") {
    if (isOpen(w.def.id)) closeWindow(w.def.id);                   // a copy waiting in the (hidden) dashboard would be a second one
    return SHELL.float(w.def.id, { width: Math.round(w.def.w * U), height: Math.round(w.def.h * U) });
  }
  openWindow(w.def);
});

// pinned windows come back after a restart; ?open=media,... opens more (handy for screenshots)
const byId = (id) => WIDGETS.find(x => x.def && (x.def.id === id || x.label.toLowerCase() === id));
// A continuing session (a page reload: sessionStorage survives it; a shell restart or crash recovery: JARVIS_REBOOT) puts back every window that
// was open; a cold start of the ecosystem opens only the pinned ones. A plain browser tab has no restart signal, so it always restores.
let continuing = !inShell;
try { continuing = continuing || startedAsRestart || sessionStorage.getItem("jarvis.seen") === "1"; sessionStorage.setItem("jarvis.seen", "1"); } catch { /* private mode */ }
let lastOpen = [];
try { lastOpen = parseIds(localStorage.getItem(OPEN_KEY)); } catch { /* private mode */ }
for (const id of windowsToRestore({ open: lastOpen, restoreAll: continuing })) { const w = byId(id); if (w && !floating.has(w.def.id)) openWindow(w.def); }      // the floating ones are restored by the shell
for (const id of (new URLSearchParams(location.search).get("open") || "").split(",").filter(Boolean)) {
  const w = byId(id); if (w && !isOpen(w.def.id)) openWindow(w.def);
}
restored = true;
window.dispatchEvent(new CustomEvent("windows-changed"));       // saves the list as it now is
