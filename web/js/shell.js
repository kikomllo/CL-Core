// The page inside the desktop shell (shell/main.js): window.jarvisShell exists only there, so in a plain browser tab every function here
// does nothing. The shell reports its mode ("overlay": the orb alone in a small click-through box, "fullscreen": the dashboard) as
// <html data-mode>, which style.css uses; jarvis/sys/ui_control (the keybinds, the tray, a voice command) asks the shell to switch.
import { bridge } from "./bridge.js";

const shell = window.jarvisShell || null;
export const inShell = !!shell;
/** True when the shell was started as a restart or crash recovery (JARVIS_REBOOT=1), not as a cold start of the ecosystem. */
export const startedAsRestart = !!(shell && shell.reboot);
/** Asks the shell to show the dashboard (a window cannot be opened in the overlay). The page learns of it as "jarvis-mode", then "jarvis-mode-settled". */
export const requestFullscreen = () => { if (shell) shell.setMode("fullscreen"); };
export const currentMode = () => document.documentElement.dataset.mode || "fullscreen";

/** `onMode(mode)` runs now and on every change (the unit and the orb depend on the window's role). */
export function startShell(onMode) {
  if (!shell) return;
  const root = document.documentElement;
  const frames = (n) => new Promise((done) => { const step = () => (n-- > 0 ? requestAnimationFrame(step) : done()); step(); });
  let veilTimer = null;
  // Veiled while the window changes size (see shell/main.js): the old layout never shows in the new box. The veil lifts once the new mode is
  // laid out and has had a few frames to paint in the new size, and anyway after 1.5 s, so a switch that never arrives cannot leave it blank.
  const unveil = async () => { await frames(3); await new Promise((r) => setTimeout(r, 60)); clearTimeout(veilTimer); root.classList.remove("switching"); };
  if (shell.onPrepare) shell.onPrepare(async () => {
    root.classList.add("switching");
    clearTimeout(veilTimer); veilTimer = setTimeout(() => root.classList.remove("switching"), 1500);
    await frames(2);                                                     // the veil is painted
    shell.prepared();
  });
  const apply = (mode) => {
    root.dataset.mode = mode; onMode(mode); window.dispatchEvent(new Event("jarvis-mode"));
    (root.classList.contains("switching") ? unveil() : frames(3)).then(() => window.dispatchEvent(new Event("jarvis-mode-settled")));       // the window has its new size
  };
  apply(shell.mode);
  shell.onMode(apply);
  bridge.on("jarvis/sys/ui_control", (p) => {
    const a = p && p.action;
    if (a === "set_fullscreen") shell.setMode("fullscreen");
    else if (a === "set_overlay") shell.setMode("overlay");
  });
}
