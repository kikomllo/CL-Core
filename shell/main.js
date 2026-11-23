// The JARVIS desktop shell: ONE transparent frameless window (one renderer, so one Chromium's worth of memory) that shows the web dashboard
// served by src/clWebUI.py. Two modes, like the Qt dashboard it replaces: "overlay" (a small always-on-top click-through box at the
// bottom right of the primary monitor, only the orb) and "fullscreen" (the whole dashboard on one monitor, interactive). The page asks
// for a switch when jarvis/sys/ui_control says so (web/js/shell.js) and this process only moves and re-flags the window.
// Environment: JARVIS_WEB_PORT, JARVIS_REBOOT ("1" = reopen in the saved mode), JARVIS_SHELL_PARENT (the supervisor's launcher: the
// shell quits when it is gone), JARVIS_SHELL_TEST=1 + JARVIS_SHELL_SHOT=<png> (render once without showing anything, save a screenshot; JARVIS_SHELL_EVAL=<js> runs in the page first, JARVIS_SHELL_HOVER=x,y moves the pointer).
const { app, BrowserWindow, Menu, screen, ipcMain } = require("electron");
const fs = require("fs");
const os = require("os");
const path = require("path");
const geo = require("./geometry");

const ROOT = path.resolve(__dirname, "..");
const TEST = process.env.JARVIS_SHELL_TEST === "1";
const URL = `http://127.0.0.1:${process.env.JARVIS_WEB_PORT || "8765"}/`;
const STATE_FILE = path.join(ROOT, "data", "shell_state.json");
const PARENT = Number(process.env.JARVIS_SHELL_PARENT) || 0;

app.setPath("userData", TEST ? path.join(os.tmpdir(), "jarvis-shell-test") : path.join(ROOT, "data", "shell_profile"));
// A small, steady page: cap V8's heap, no spell checker, no menu, no crash/metrics reporters.
app.commandLine.appendSwitch("js-flags", "--max-old-space-size=384");
app.commandLine.appendSwitch("disable-features", "SpareRendererForSitePerProcess,Translate,MediaRouter");
Menu.setApplicationMenu(null);

let win = null, mode = "overlay", displayId = null, loaded = false;
let overlayShown = false;        // the page says whether the orb has anything to show; until it does the overlay window stays hidden

function readState() {
  try { return geo.normalizeState(JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))); } catch { return geo.normalizeState(null); }
}
function saveState() {
  if (TEST) return;
  try { fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true }); fs.writeFileSync(STATE_FILE, JSON.stringify({ mode, displayId })); } catch (e) { console.log("[SHELL] could not save state:", e.message); }
}

function createWindow() {
  win = new BrowserWindow({
    show: false, frame: false, transparent: true, backgroundColor: "#00000000", hasShadow: false, resizable: false, movable: false,
    minimizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true, title: "JARVIS",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, spellcheck: false, devTools: !!process.env.JARVIS_SHELL_DEBUG },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(URL)) e.preventDefault(); });
  // the server is started by the supervisor next to this process: until it answers keep trying instead of showing an error page
  win.webContents.on("did-fail-load", (_e, code, _desc, _url, isMainFrame) => { if (isMainFrame && code !== -3) setTimeout(() => win && !win.isDestroyed() && win.loadURL(URL), 700); });
  win.webContents.on("render-process-gone", (_e, d) => { console.log("[SHELL] renderer gone:", d.reason); if (win && !win.isDestroyed()) win.reload(); });
  win.webContents.once("did-finish-load", () => { loaded = true; onFirstLoad(); });
  win.loadURL(URL);
}

/** Move and re-flag the window for `next`, tell the page, show it. Never shows anything in test mode. */
function setMode(next) {
  if (!geo.MODES.includes(next) || !win || win.isDestroyed()) return;
  const displays = screen.getAllDisplays().map((d) => ({ id: d.id, bounds: d.bounds }));
  const picked = geo.pickDisplay(next, displays, screen.getPrimaryDisplay().id, displayId, next === "fullscreen" ? screen.getCursorScreenPoint() : null);
  const d = screen.getAllDisplays().find((x) => x.id === picked.id) || screen.getPrimaryDisplay();
  mode = next;
  if (next === "fullscreen") displayId = d.id;
  const bounds = next === "overlay" ? geo.overlayBounds(d.bounds, d.workArea) : d.bounds;
  win.setBounds(bounds);
  win.setIgnoreMouseEvents(next === "overlay");                          // the overlay never takes a click
  win.setFocusable(next !== "overlay");
  win.setAlwaysOnTop(next === "overlay", "screen-saver");               // like the Qt overlay; the dashboard is an ordinary window
  win.webContents.send("shell:mode", next);
  if (!TEST && loaded) { if (next === "overlay") { if (overlayShown) win.showInactive(); else win.hide(); } else { win.show(); win.focus(); } }
  saveState();
}

function onFirstLoad() {
  setMode(mode);
  if (TEST) return runTest();
  console.log(`[SHELL] ready mode=${mode}`);
}

async function runTest() {
  let evalResult;
  if (process.env.JARVIS_SHELL_EVAL) evalResult = await win.webContents.executeJavaScript(process.env.JARVIS_SHELL_EVAL);      // e.g. put the orb in a state without touching MQTT; its value is printed
  if (process.env.JARVIS_SHELL_HOVER) {                                                                           // "x,y": put the pointer there once the page has settled
    await new Promise((r) => setTimeout(r, 1500));
    const [x, y] = process.env.JARVIS_SHELL_HOVER.split(",").map(Number);
    win.webContents.sendInputEvent({ type: "mouseMove", x: x - 3, y: y - 3 });                                    // two moves: the first one only places the pointer
    await new Promise((r) => setTimeout(r, 120));
    win.webContents.sendInputEvent({ type: "mouseMove", x, y });
  }
  await new Promise((r) => setTimeout(r, Number(process.env.JARVIS_SHELL_WAIT) || 2500));
  const info = { eval: evalResult, mode, bounds: win.getBounds(), dataMode: await win.webContents.executeJavaScript("document.documentElement.dataset.mode"), shell: await win.webContents.executeJavaScript("typeof window.jarvisShell") };
  if (process.env.JARVIS_SHELL_AFTER) info.after = await win.webContents.executeJavaScript(process.env.JARVIS_SHELL_AFTER);      // a probe run after the wait, just before the screenshot
  if (process.env.JARVIS_SHELL_SHOT) { const img = await win.webContents.capturePage(); fs.writeFileSync(process.env.JARVIS_SHELL_SHOT, img.toPNG()); info.shot = process.env.JARVIS_SHELL_SHOT; }
  console.log("[SHELL-TEST] " + JSON.stringify(info));
  app.quit();
}

ipcMain.on("shell:get-mode", (e) => { e.returnValue = mode; });
ipcMain.on("shell:set-mode", (_e, next) => setMode(next));
// The overlay is only a window while the orb shows something: hidden, it costs no frames and no compositing, and nothing is on screen at rest.
ipcMain.on("shell:set-visible", (_e, visible) => {
  overlayShown = !!visible;
  if (TEST || !win || win.isDestroyed() || mode !== "overlay") return;
  if (overlayShown) win.showInactive(); else win.hide();
});

// After a restart the previous shell may still be running for up to its 2 s parent check: wait for its lock instead of giving up at once.
async function acquireLock() {
  if (TEST) return true;
  for (let i = 0; i < 12; i++) { if (app.requestSingleInstanceLock()) return true; await new Promise((r) => setTimeout(r, 500)); }
  return false;
}

Promise.all([app.whenReady(), acquireLock()]).then(([, locked]) => {
  if (!locked) { console.log("[SHELL] another shell is already running"); return app.quit(); }
  const saved = readState();
  displayId = saved.displayId;
  mode = TEST ? (process.env.JARVIS_SHELL_MODE === "fullscreen" ? "fullscreen" : "overlay") : geo.startMode(process.env.JARVIS_REBOOT === "1", saved);
  createWindow();
  const refit = () => { if (loaded) setMode(mode); };                      // a monitor was added, removed or changed its resolution
  screen.on("display-added", refit); screen.on("display-removed", refit); screen.on("display-metrics-changed", refit);
  if (PARENT && !TEST) setInterval(() => { try { process.kill(PARENT, 0); } catch { app.quit(); } }, 2000);   // the supervisor killed the launcher: go too
});
app.on("window-all-closed", () => app.quit());
