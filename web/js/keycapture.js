// Turns a browser keydown into the shortcut strings config/keybinds.json uses (what the Qt KeybindCaptureEdit does with Qt key
// events). Pure, so it is tested under node (tests/test_webSettingsTabs.py). Uses KeyboardEvent.code, which tells left from right.

const MODIFIER_CODES = new Set(["ControlLeft", "ControlRight", "AltLeft", "AltRight", "ShiftLeft", "ShiftRight", "MetaLeft", "MetaRight", "OS", "AltGraph"]);
const PTT = {
  ShiftLeft: "KEY_LEFTSHIFT", ShiftRight: "KEY_RIGHTSHIFT", ControlLeft: "KEY_LEFTCTRL", ControlRight: "KEY_RIGHTCTRL",
  AltLeft: "KEY_LEFTALT", AltRight: "KEY_RIGHTALT", CapsLock: "KEY_CAPSLOCK", Space: "KEY_SPACE",
  ...Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`F${i + 13}`, `KEY_F${i + 13}`])),             // F13..F24
};

/** Push-to-talk is one raw, left/right-aware key from the supported set: the KEY_* name, or null. */
export const pttFromCode = (code) => PTT[code] || null;

/** A shortcut from a keydown: {value} to save it, {pending: true} while only modifiers are down, {clear: true} for Esc,
 *  {error} for a key the backend's parsers do not take (letters and digits only: they parse the same on Windows and Linux). */
export function comboFromEvent(e) {
  if (e.code === "Escape") return { clear: true };
  if (MODIFIER_CODES.has(e.code)) return { pending: true };
  const m = /^Key([A-Z])$/.exec(e.code) || /^Digit(\d)$/.exec(e.code);
  if (!m) return { error: "Unsupported -- letters/numbers only" };
  const parts = [];
  if (e.ctrlKey) parts.push("<ctrl>");
  if (e.altKey) parts.push("<alt>");
  if (e.shiftKey) parts.push("<shift>");
  if (e.metaKey) parts.push("<super>");
  parts.push(m[1].toLowerCase());
  return { value: parts.join("+") };
}

/** "<ctrl>+<alt>+a" -> "Ctrl+Alt+A" for display; KEY_* names and empty stay as they are. */
export function pretty(internal) {
  if (!internal || internal.startsWith("KEY_")) return internal || "";
  return internal.split("+").map((p) => (p.startsWith("<") && p.endsWith(">") ? p[1].toUpperCase() + p.slice(2, -1) : p.toUpperCase())).join("+");
}
