// The page inside the desktop shell (shell/main.js): window.jarvisShell exists only there, so in a plain browser tab every function here
// does nothing. The shell reports its mode ("overlay": the orb alone in a small click-through box, "fullscreen": the dashboard) as
// <html data-mode>, which style.css uses; jarvis/sys/ui_control (the keybinds, the tray, a voice command) asks the shell to switch.
import { bridge } from "./bridge.js";

const shell = window.jarvisShell || null;
export const inShell = !!shell;
export const currentMode = () => document.documentElement.dataset.mode || "fullscreen";

/** `onMode(mode)` runs now and on every change (the unit and the orb depend on the window's role). */
export function startShell(onMode) {
  if (!shell) return;
  const apply = (mode) => { document.documentElement.dataset.mode = mode; onMode(mode); window.dispatchEvent(new Event("jarvis-mode")); };
  apply(shell.mode);
  shell.onMode(apply);
  bridge.on("jarvis/sys/ui_control", (p) => {
    const a = p && p.action;
    if (a === "set_fullscreen") shell.setMode("fullscreen");
    else if (a === "set_overlay") shell.setMode("overlay");
  });
}
