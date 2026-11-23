// The page's only handle on the shell: which mode the window is in, a way to ask for another, and a callback for when it changes.
// Runs before the page's scripts, so `mode` is already right for the first paint.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvisShell", {
  mode: ipcRenderer.sendSync("shell:get-mode"),
  setMode: (mode) => ipcRenderer.send("shell:set-mode", mode),
  setVisible: (visible) => ipcRenderer.send("shell:set-visible", !!visible),         // the overlay window is hidden while the orb shows nothing
  onMode: (fn) => { ipcRenderer.on("shell:mode", (_event, mode) => fn(mode)); },
});
