// The Claude terminal's pure logic (tests/test_webClaude.py runs it under node). The bridge (src/clClaudeBridge.py) has already emulated
// the terminal, so the page does not need xterm.js: jarvis/claude/screen is a full snapshot {text, mode, idle?, cursor?: [col, row],
// ansi?} and the page draws its rows and forwards keys as claude.keys / claude.control_key / claude.resize.
//
// Colours follow the Qt widget: the terminal keeps the dashboard's single accent, so every ANSI colour is reduced to how bright it is
// (0..1) and the view draws it on a ramp from dim to the skin's accent. Greys go by luminance (a shell's grey suggestion stays dim),
// saturated colours by their strongest channel (ANSI blue is dark but must stay readable).

const BASE16 = [
  [0, 0, 0], [205, 49, 49], [13, 188, 121], [229, 229, 16], [36, 114, 200], [188, 63, 188], [17, 168, 205], [204, 204, 204],
  [102, 102, 102], [241, 76, 76], [35, 209, 139], [245, 245, 67], [59, 142, 234], [214, 112, 214], [41, 184, 219], [255, 255, 255],
];

export function xtermColor(n) {
  if (n < 16) return BASE16[n];
  if (n < 232) { n -= 16; const lv = (v) => (v === 0 ? 0 : 55 + 40 * v); return [lv(Math.floor(n / 36)), lv(Math.floor(n / 6) % 6), lv(n % 6)]; }
  const g = 8 + 10 * (n - 232);
  return [g, g, g];
}

export const luminance = ([r, g, b]) => (0.299 * r + 0.587 * g + 0.114 * b) / 255;

/** How bright a colour is, 0..1 (the Qt widget's `_brightness`). */
export function brightness(rgb) {
  const mx = Math.max(...rgb), mn = Math.min(...rgb);
  const sat = mx === 0 ? 0 : (mx - mn) / mx;                       // HSV saturation, then value = mx/255
  return Math.max(luminance(rgb), sat * (mx / 255));
}

const fresh = () => ({ fg: null, bg: null, bold: false, dim: false, italic: false, underline: false, reverse: false });

/** Apply one SGR parameter string ("1;38;5;208") to a style object, in place. */
export function applySgr(style, params) {
  const codes = params ? params.split(/[;:]/).map((p) => (/^\d+$/.test(p) ? Number(p) : 0)) : [0];
  for (let i = 0; i < codes.length; i++) {
    const c = codes[i];
    if (c === 0) Object.assign(style, fresh());
    else if (c === 1) style.bold = true;
    else if (c === 2) style.dim = true;
    else if (c === 3) style.italic = true;
    else if (c === 4) style.underline = true;
    else if (c === 7) style.reverse = true;
    else if (c === 22) { style.bold = false; style.dim = false; }
    else if (c === 23) style.italic = false;
    else if (c === 24) style.underline = false;
    else if (c === 27) style.reverse = false;
    else if (c >= 30 && c <= 37) style.fg = xtermColor(c - 30);
    else if (c >= 90 && c <= 97) style.fg = xtermColor(c - 90 + 8);
    else if (c >= 40 && c <= 47) style.bg = xtermColor(c - 40);
    else if (c >= 100 && c <= 107) style.bg = xtermColor(c - 100 + 8);
    else if (c === 39) style.fg = null;
    else if (c === 49) style.bg = null;
    else if ((c === 38 || c === 48) && i + 1 < codes.length) {
      const key = c === 38 ? "fg" : "bg";
      if (codes[i + 1] === 5 && i + 2 < codes.length) { style[key] = xtermColor(codes[i + 2] & 255); i += 2; }
      else if (codes[i + 1] === 2 && i + 4 < codes.length) { style[key] = codes.slice(i + 2, i + 5).map((v) => v & 255); i += 4; }
    }
  }
}

/** What a span looks like once the colours are reduced to brightness: {text, fgV, bgV, bold, italic, underline, reverse}.
 *  fgV is null for the default colour (a dim span with no colour is drawn at 0.43, the Qt widget's grey 110). */
function look(style, text) {
  let fgV = style.fg ? brightness(style.fg) : (style.dim ? brightness([110, 110, 110]) : null);
  if (style.dim && style.fg) fgV = brightness(style.fg.map((v) => Math.round(v / 1.8)));
  return { text, fgV, bgV: style.bg ? luminance(style.bg) : null, bold: style.bold, italic: style.italic, underline: style.underline, reverse: style.reverse };
}

const SGR = /\x1b\[([0-9;:]*)m/g;

/** The rows of a screen with SGR escapes: [[span, ...] per row]. The style carries across rows like a terminal's. */
export function ansiRows(ansi) {
  const style = fresh(), rows = [];
  for (const line of String(ansi).split("\n")) {
    const spans = [];
    let pos = 0, m;
    SGR.lastIndex = 0;
    while ((m = SGR.exec(line))) {
      if (m.index > pos) spans.push(look(style, line.slice(pos, m.index)));
      applySgr(style, m[1]);
      pos = m.index + m[0].length;
    }
    if (pos < line.length) spans.push(look(style, line.slice(pos)));
    rows.push(spans);
  }
  return rows;
}

/** A default-coloured span (`extra`: more properties, e.g. {sel: true} for the selected part of the line being edited). */
export const plainSpan = (text, extra = {}) => ({ ...look(fresh(), text), ...extra });

/** A row with the locally edited line on it: the first `start` columns keep their own spans (the prompt, padded with spaces if the row
 *  is shorter), then `text` in the default colour, its `selection` [a, b] marked. */
export function lineSpans(spans, start, text, selection = null) {
  const out = cutAfter(spans, start), used = out.reduce((n, s) => n + s.text.length, 0);
  if (used < start) out.push(plainSpan(" ".repeat(start - used)));
  const parts = selection ? [[text.slice(0, selection[0]), false], [text.slice(selection[0], selection[1]), true], [text.slice(selection[1]), false]] : [[text, false]];
  for (const [t, sel] of parts) if (t) out.push(plainSpan(t, sel ? { sel: true } : {}));
  return out;
}

/** The rows to draw for a snapshot: the ansi text when the bridge sent it (a shell with colours), otherwise the plain text. */
export function screenRows(msg) {
  if (!msg || typeof msg !== "object") return [];
  if (typeof msg.ansi === "string" && msg.ansi) return ansiRows(msg.ansi);
  return String(msg.text ?? "").split("\n").map((t) => (t ? [look(fresh(), t)] : []));
}

/** Where to draw the cursor: {row, col} or null. A shell's own suggestion lies beyond the cursor, so (like the Qt widget) at an idle
 *  prompt everything after the cursor on its row is dropped by the caller via cutAfterCursor. */
export function cursorCell(msg) {
  const c = msg && msg.cursor;
  return Array.isArray(c) && c.length === 2 && c.every(Number.isInteger) && c[0] >= 0 && c[1] >= 0 ? { col: c[0], row: c[1] } : null;
}

/** Cuts a row's spans after `col` characters (an idle shell prompt: what is drawn beyond the cursor is a suggestion, never shown). */
export function cutAfter(spans, col) {
  const out = [];
  let left = col;
  for (const s of spans) {
    if (left <= 0) break;
    out.push(s.text.length <= left ? s : { ...s, text: s.text.slice(0, left) });
    left -= s.text.length;
  }
  return out;
}

/** True when the screen is an idle readline shell prompt: its suggestion is hidden. */
export const hidesSuggestion = (msg) => !!msg && msg.mode === "terminal" && msg.idle === true && cursorCell(msg) !== null;

/** Is the session working? Claude shows "esc to interrupt" while it runs; a shell is busy when a program (not the prompt) is in front. */
export function isBusy(msg) {
  if (!msg || typeof msg !== "object") return false;
  if (msg.mode === "claude") return /esc to interrupt/i.test(String(msg.text ?? ""));
  return msg.idle === false;
}

/** Splits text into runs so symbols that a monospace font may not have (● ❯ ✻ ⎿ box drawing, arrows, dingbats) can each be pinned to
 *  exactly one cell: a fallback font's wider or narrower glyph would otherwise shift every column after it. [{text, sym}] */
export function cellRuns(text) {
  const runs = [];
  for (const ch of String(text)) {
    const cp = ch.codePointAt(0), sym = cp >= 0x2190 && cp <= 0x2BFF;
    const last = runs[runs.length - 1];
    if (sym) runs.push({ text: ch, sym: true });
    else if (last && !last.sym) last.text += ch;
    else runs.push({ text: ch, sym: false });
  }
  return runs;
}

const CONTROL = { ArrowUp: "UP", ArrowDown: "DOWN", ArrowLeft: "LEFT", ArrowRight: "RIGHT", Home: "HOME", End: "END", Delete: "DELETE", PageUp: "PAGEUP", PageDown: "PAGEDOWN", Escape: "ESCAPE", Enter: "ENTER" };

/** What a key press means for the session, as a terminal would send it: {control: "UP"} (claude.control_key), {keys: "a"} (claude.keys),
 *  {copy: true}/{paste: true} for the page to handle, or null to leave the key alone. `hasSelection` is for Ctrl+C: with text selected it
 *  copies, otherwise it interrupts. Ctrl+Z is left out on purpose: the CLI treats it as "suspend", which strands the session. */
export function keyAction(e, hasSelection = false) {
  const { key, ctrlKey: ctrl = false, shiftKey: shift = false, altKey: alt = false, metaKey: meta = false } = e;
  if (meta || key === "Shift" || key === "Control" || key === "Alt" || key === "Meta" || key === "Dead" || key === "Process") return null;
  if (ctrl && !alt && key.length === 1 && /[a-z]/i.test(key)) {
    const k = key.toLowerCase();
    if (k === "v") return { paste: true };
    if (k === "c" && hasSelection) return { copy: true };
    if (k === "z") return null;
    return { keys: String.fromCharCode(k.charCodeAt(0) - 96) };
  }
  if (key === "Tab") return shift ? { control: "SHIFT_TAB" } : { keys: "\t" };
  if (key === "Backspace") return { keys: ctrl ? "\x17" : "\x7f" };      // Ctrl+Backspace is readline's delete-previous-word
  if (CONTROL[key]) return { control: ctrl && (key === "ArrowLeft" || key === "ArrowRight") ? "CTRL_" + CONTROL[key] : CONTROL[key] };
  if (!ctrl && !alt && key.length >= 1 && [...key].length === 1) return { keys: key };      // one printable character (any alphabet, emoji)
  return null;
}

export const MODE_SWITCH = ["/terminal", "/claude"];

/** On Enter: `shadow` is what was typed since the last Enter. A typed mode switch must not run in the shell: erase it, then send the
 *  switch as text (claude.ask). Returns {erase, text} for that, or null for an ordinary Enter. */
export function submitAction(shadow) {
  const cmd = String(shadow).trim().toLowerCase();
  return MODE_SWITCH.includes(cmd) ? { erase: [...shadow].length, text: cmd } : null;
}

/** Keeps the typed-since-Enter shadow up to date: returns the new shadow after an action. */
export function nextShadow(shadow, action) {
  if (action.keys !== undefined) {
    if (action.keys === "\x7f") return [...shadow].slice(0, -1).join("");
    return action.keys.length === 1 && action.keys.charCodeAt(0) < 32 || action.keys === "\t" ? "" : shadow + action.keys;
  }
  return "";                                                         // any control key (arrows, Enter, Esc...) starts a fresh line
}

/** The terminal grid a pixel box holds: at least `minCols` x `minRows` (the bridge's own floor is 20x5). */
export const gridFor = (w, h, cw, lh, minRows = 5, minCols = 20) => ({ cols: Math.max(minCols, Math.floor(w / cw)), rows: Math.max(minRows, Math.floor(h / lh)) });
