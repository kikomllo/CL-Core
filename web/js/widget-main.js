// The page of a floating (unpinned) window: widget.html?id=<window id>, loaded by shell/main.js in a window of its own. It shows that one window
// filling the whole page; moving (the title bar), resizing, closing and pinning back are done through the shell (ui.js, widgetMode).
import { setSkin } from "./skin.js";
import { openWindow, iconsReady } from "./ui.js";
import { bridge } from "./bridge.js";
import { mediaWindow } from "./media.js";
import { lightsWindow } from "./lights.js";
import { todoWindow } from "./todo.js";
import { notesWindow } from "./notes.js";
import { remindersWindow } from "./reminders.js";
import { settingsWindow } from "./settings.js";
import { logsWindow } from "./logs.js";
import { updatesWindow } from "./updates.js";
import { claudeWindow } from "./claude.js";

const DEFS = [mediaWindow, lightsWindow, remindersWindow, todoWindow, notesWindow, settingsWindow, updatesWindow, claudeWindow, logsWindow];

// One fluid unit like the dashboard's, from the monitor the window is on (not the window: it is small), and again when it is dragged to another.
function setUnit() {
  const long = Math.max(screen.width, screen.height), short = Math.min(screen.width, screen.height);
  document.documentElement.style.setProperty("--u", Math.min(long / 1920, short / 1080) + "px");
}
setUnit(); addEventListener("resize", setUnit);

fetch("/api/config").then((r) => r.json()).catch(() => ({})).then((s) => setSkin(s.theme));

const id = new URLSearchParams(location.search).get("id");
const def = DEFS.find((d) => d.id === id);
if (def) {
  document.title = def.title; openWindow(def);
  // The shell shows the window only when this says it is built, so what appears is complete: the icons are in, the data connection is up and
  // the first data has arrived (the dashboard's copy stays under it until then). Never later than 1.5 s, whatever is missing.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const connected = new Promise((r) => bridge.onConnection((up) => up && r()));
  Promise.race([(async () => { await iconsReady(); await connected; await sleep(180); await iconsReady(); })(), sleep(1500)])
    .then(() => window.jarvisShell && window.jarvisShell.floatReady());
} else if (window.jarvisShell) window.jarvisShell.closeSelf();          // an id that is no window: nothing to show
