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
  try { fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true }); fs.writeFileSync(STATE_FILE, JSON.stringify({ mode, displayId, floating: floatingState() })); } catch (e) { console.log("[SHELL] could not save state:", e.message); }
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
  // no menu means no Ctrl+R / F5 either: handle them here (a code change to web/ is one keypress away)
  win.webContents.on("before-input-event", (e, input) => {
    const kind = geo.reloadKind(input);
    if (!kind) return;
    e.preventDefault();
    if (kind === "hard") win.webContents.reloadIgnoringCache(); else win.webContents.reload();
  });
  win.webContents.on("render-process-gone", (_e, d) => { console.log("[SHELL] renderer gone:", d.reason); if (win && !win.isDestroyed()) win.reload(); });
  win.webContents.once("did-finish-load", () => { loaded = true; onFirstLoad(); });
  win.loadURL(URL);
}

// The window is resized before the page learns the new mode (a separate process: the mode arrives a moment later), so for a few frames the
// old layout was drawn in the new box: the dashboard's windows squeezed into the little overlay box, or the overlay's orb stretched over the
// monitor. So the page is veiled first (html.switching: everything invisible, transparent) and only once it says so is the window moved.
const preparedWaiters = [];
ipcMain.on("shell:prepared", () => { while (preparedWaiters.length) preparedWaiters.shift()(); });
function pagePrepared() {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 250);                       // a page that never answers must not block the switch
    preparedWaiters.push(() => { clearTimeout(timer); resolve(); });
    win.webContents.send("shell:prepare");
  });
}

let switchSeq = 0;
/** Move and re-flag the window for `next`, tell the page, show it. Never shows anything in test mode. */
async function setMode(next, initial = false) {
  if (!geo.MODES.includes(next) || !win || win.isDestroyed()) return;
  const seq = ++switchSeq;
  if (!initial && loaded && (TEST || win.isVisible())) {
    await pagePrepared();
    if (seq !== switchSeq || win.isDestroyed()) return;           // a newer switch took over
  }
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

// ---------- unpinned windows ----------
// A dashboard window can be unpinned (like the Qt widgets): it becomes a window of its own, always on top of other apps, that can be dragged
// to any monitor and that stays when the dashboard is just the overlay. One renderer per floating window, each loading widget.html?id=...
const floats = new Map();            // window id -> BrowserWindow
const testLog = [];                  // TEST only: what happened to the floating windows and when
// TEST normally shows nothing; JARVIS_SHELL_SHOW_FLOATS=1 lets the floating windows show (to inspect the real OS windows)
const SHOW_FLOATS = !TEST || process.env.JARVIS_SHELL_SHOW_FLOATS === "1";
const tl = (...a) => { if (TEST) testLog.push([Date.now() % 100000, ...a]); };
let saveTimer = null;
const floatingState = () => Object.fromEntries([...floats].filter(([, w]) => !w.isDestroyed()).map(([id, w]) => [id, w.getBounds()]));
const floatingIds = () => [...floats.keys()];
const floatIdOf = (sender) => { for (const [id, w] of floats) if (!w.isDestroyed() && w.webContents === sender) return id; return null; };
const notifyFloating = () => { if (win && !win.isDestroyed()) win.webContents.send("shell:floating", floatingIds()); };
const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveState, 400); };

function openFloat(id, req) {
  if (!/^[\w-]{1,64}$/.test(String(id))) return;
  const existing = floats.get(id);
  if (existing && !existing.isDestroyed()) { existing.show(); existing.focus(); return; }
  const displays = screen.getAllDisplays().map((d) => ({ id: d.id, bounds: d.bounds }));
  const b = geo.floatBounds(req, displays, screen.getPrimaryDisplay().workArea, floats.size);
  const w = new BrowserWindow({
    ...b, show: false, frame: false, transparent: true, backgroundColor: "#00000000", hasShadow: false, resizable: true, minimizable: false,
    maximizable: false, fullscreenable: false, skipTaskbar: true, alwaysOnTop: true, title: "JARVIS",
    minWidth: geo.FLOAT_MIN.width, minHeight: geo.FLOAT_MIN.height,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, spellcheck: false, devTools: !!process.env.JARVIS_SHELL_DEBUG },
  });
  floats.set(id, w);
  // Windows animates a window that appears (a small zoom-in) and one that closes. So the window is "shown" right away, fully transparent and
  // click-through, while its page loads; the animation runs unseen, and showFloat only raises the opacity: nothing grows in front of the user.
  if (SHOW_FLOATS) { w.setOpacity(0); w.setIgnoreMouseEvents(true); w.showInactive(); }
  w.setAlwaysOnTop(true, "screen-saver");                                  // above other apps, like the overlay
  w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  w.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(URL)) e.preventDefault(); });
  w.webContents.on("did-fail-load", (_e, code, _desc, _url, isMainFrame) => { if (isMainFrame && code !== -3) setTimeout(() => !w.isDestroyed() && w.loadURL(`${URL}widget.html?id=${encodeURIComponent(id)}`), 700); });
  w.webContents.on("render-process-gone", () => { if (!w.isDestroyed()) w.reload(); });
  w.webContents.on("before-input-event", (e, input) => {
    const kind = geo.reloadKind(input);
    if (kind) { e.preventDefault(); if (kind === "hard") w.webContents.reloadIgnoringCache(); else w.webContents.reload(); }
  });
  w.on("moved", saveSoon); w.on("resized", saveSoon);
  w.on("resize", () => tl("resize", id, w.getBounds().width, w.getBounds().height));
  w.on("closed", () => { if (floats.get(id) === w) { floats.delete(id); shownFloats.delete(id); } notifyFloating(); saveSoon(); });
  setTimeout(() => showFloat(id), 3000);                                    // a page that never says it is ready is shown anyway
  w.loadURL(`${URL}widget.html?id=${encodeURIComponent(id)}`);
  notifyFloating(); saveSoon();
}
// Unpinning hands a window over without a gap: the dashboard keeps its copy until the floating one is on screen, exactly over it, and only then
// removes it (`shell:float-shown`); pinning back is the same the other way round (`shell:docked`, `shell:dock-ready`).
const shownFloats = new Set();
function showFloat(id) {
  const w = floats.get(id);
  if (!w || w.isDestroyed() || shownFloats.has(id)) return;
  shownFloats.add(id);
  tl("show", id, w.getBounds().width, w.getBounds().height);
  if (SHOW_FLOATS) { w.setIgnoreMouseEvents(false); w.setOpacity(1); }
  setTimeout(() => { if (win && !win.isDestroyed()) win.webContents.send("shell:float-shown", id); }, 60);        // let it paint over the copy first
}
const dockTimers = new Map();
function finishDock(id) { clearTimeout(dockTimers.get(id)); dockTimers.delete(id); closeFloat(id); }
const closeFloat = (id) => { const w = floats.get(id); if (w && !w.isDestroyed()) { try { w.setOpacity(0); } catch { /* gone */ } w.close(); } };      // invisible first: no closing animation

ipcMain.on("shell:get-floating", (e) => { e.returnValue = floatingIds(); });
ipcMain.on("shell:float", (_e, id, req) => {
  req = req && typeof req === "object" ? { ...req } : null;
  if (req && req.relative && win && !win.isDestroyed()) {                  // a rectangle in the dashboard page's own coordinates: put it on the screen exactly
    const d = win.getBounds();
    req = { x: Math.round(d.x + req.x), y: Math.round(d.y + req.y), width: req.width, height: req.height };
  }
  openFloat(String(id), req);
});
ipcMain.on("shell:float-ready", (e) => { const id = floatIdOf(e.sender); if (id) showFloat(id); });
ipcMain.on("shell:close-floating", (_e, id) => closeFloat(String(id)));
ipcMain.on("shell:close-float", (e) => { const id = floatIdOf(e.sender); if (id) closeFloat(id); });
ipcMain.on("shell:dock-float", (e) => {                                      // pin again: the window goes back into the dashboard
  const id = floatIdOf(e.sender), w = id && floats.get(id);
  if (!id || !w || w.isDestroyed()) return;
  if (!win || win.isDestroyed()) return closeFloat(id);
  const b = w.getBounds(), d = win.getBounds();
  dockTimers.set(id, setTimeout(() => finishDock(id), 1500));               // the dashboard is slow to answer: close it anyway
  win.webContents.send("shell:docked", id, { x: b.x - d.x, y: b.y - d.y, width: b.width, height: b.height });      // where it is, in the dashboard's coordinates
});
ipcMain.on("shell:dock-ready", (_e, id) => { if (dockTimers.has(String(id))) finishDock(String(id)); });      // the dashboard's copy is drawn: now the floating one can go
ipcMain.on("shell:float-resize", (e, width, height) => {
  const id = floatIdOf(e.sender), w = id && floats.get(id);
  if (!w || w.isDestroyed() || !Number.isFinite(width) || !Number.isFinite(height)) return;
  const b = w.getBounds();
  w.setBounds({ x: b.x, y: b.y, width: Math.max(geo.FLOAT_MIN.width, Math.round(width)), height: Math.max(geo.FLOAT_MIN.height, Math.round(height)) });
});
ipcMain.on("shell:float-min", (e, width, height) => {                        // what the window's content needs to fit (measured by the page)
  const id = floatIdOf(e.sender), w = id && floats.get(id);
  tl("min", id, Math.round(width), Math.round(height));
  if (w && !w.isDestroyed() && Number.isFinite(width) && Number.isFinite(height)) w.setMinimumSize(Math.max(geo.FLOAT_MIN.width, Math.round(width)), Math.max(geo.FLOAT_MIN.height, Math.round(height)));
});

function onFirstLoad() {
  setMode(mode, true);                                           // the first placement: nothing is drawn yet that could flash
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
  if (process.env.JARVIS_SHELL_FLOATS) {                                                                      // e.g. "1": report the floating windows and what their pages show
    await new Promise((r) => setTimeout(r, 1500));
    if (process.env.JARVIS_SHELL_FLOAT_EVAL) {                                                                // run in every floating page first (e.g. click its pin), then look again
      await Promise.all([...floats].map(([, w]) => w.webContents.executeJavaScript(process.env.JARVIS_SHELL_FLOAT_EVAL)));
      await new Promise((r) => setTimeout(r, 2000));
      if (process.env.JARVIS_SHELL_AFTER2) info.after2 = await win.webContents.executeJavaScript(process.env.JARVIS_SHELL_AFTER2);      // the dashboard, after that
    }
    info.floats = await Promise.all([...floats].map(async ([id, w]) => ({ id, bounds: w.getBounds(), onTop: w.isAlwaysOnTop(), min: w.getMinimumSize(),
      page: await w.webContents.executeJavaScript("JSON.stringify({mode: document.documentElement.dataset.mode, wins: [...document.querySelectorAll('.win')].map(x => x.dataset.id), inner: [innerWidth, innerHeight], u: getComputedStyle(document.documentElement).getPropertyValue('--u'), zoom: localStorage.getItem('jarvis.zoom.v1')})") })));
  }
  info.testLog = testLog;
  console.log("[SHELL-TEST] " + JSON.stringify(info));
  app.quit();
}

ipcMain.on("shell:get-mode", (e) => { e.returnValue = win && e.sender === win.webContents ? mode : "widget"; });
ipcMain.on("shell:get-reboot", (e) => { e.returnValue = process.env.JARVIS_REBOOT === "1"; });
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
  if (!TEST && process.env.JARVIS_REBOOT === "1") for (const [id, b] of Object.entries(saved.floating)) openFloat(id, b);      // a restart or crash recovery puts them back; a cold start opens clean
  const refit = () => { if (loaded) setMode(mode); };                      // a monitor was added, removed or changed its resolution
  screen.on("display-added", refit); screen.on("display-removed", refit); screen.on("display-metrics-changed", refit);
  if (PARENT && !TEST) setInterval(() => { try { process.kill(PARENT, 0); } catch { app.quit(); } }, 2000);   // the supervisor killed the launcher: go too
});
app.on("window-all-closed", () => app.quit());
