// The page's only handle on the shell: which mode the window is in, a way to ask for another, and a callback for when it changes.
// Runs before the page's scripts, so `mode` is already right for the first paint.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvisShell", {
  mode: ipcRenderer.sendSync("shell:get-mode"),
  reboot: ipcRenderer.sendSync("shell:get-reboot"),                                  // started as a restart (not a cold start): the page puts its open windows back
  setMode: (mode) => ipcRenderer.send("shell:set-mode", mode),
  setVisible: (visible) => ipcRenderer.send("shell:set-visible", !!visible),         // the overlay window is hidden while the orb shows nothing
  onMode: (fn) => { ipcRenderer.on("shell:mode", (_event, mode) => fn(mode)); },
  onPrepare: (fn) => { ipcRenderer.on("shell:prepare", () => fn()); },             // a mode switch is coming: hide the page, then call prepared()
  prepared: () => ipcRenderer.send("shell:prepared"),
  // unpinned (floating) windows: each is its own always-on-top window, see shell/main.js
  floating: () => ipcRenderer.sendSync("shell:get-floating"),                         // ids of the windows that float now
  onFloating: (fn) => { ipcRenderer.on("shell:floating", (_e, ids) => fn(ids)); },
  onDocked: (fn) => { ipcRenderer.on("shell:docked", (_e, id, rect) => fn(id, rect)); },   // a floating window was pinned again: open it in the dashboard, where it was (rect: dashboard coordinates)
  dockReady: (id) => ipcRenderer.send("shell:dock-ready", id),                       // the dashboard has drawn it: the floating one can close
  onFloatShown: (fn) => { ipcRenderer.on("shell:float-shown", (_e, id) => fn(id)); },  // a window just unpinned is on screen: the dashboard can drop its copy
  floatReady: () => ipcRenderer.send("shell:float-ready"),                           // from a floating page: built, show me
  float: (id, bounds) => ipcRenderer.send("shell:float", id, bounds || null),         // bounds: {x, y, width, height} in screen px, or just {width, height}
  closeFloating: (id) => ipcRenderer.send("shell:close-floating", id),
  closeSelf: () => ipcRenderer.send("shell:close-float"),                             // from inside a floating window
  dockSelf: () => ipcRenderer.send("shell:dock-float"),
  resizeSelf: (width, height) => ipcRenderer.send("shell:float-resize", width, height),
  minSelf: (width, height) => ipcRenderer.send("shell:float-min", width, height),
});
