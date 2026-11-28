// The full terminal: xterm.js (vendored in web/vendor/xterm, no network) drawing the shell that src/clConsole.py keeps alive (jarvis/console/*).
// Nothing is interpreted on this side or in between: keystrokes go to the shell as typed (paste included) and its output comes back as it
// printed, so typing, colours, full-screen programs, selection and scrollback are a terminal's own. The shell belongs to the console service, not to
// this window: closing the window or restarting the dashboard leaves it (and whatever runs in it) alone, and a window that opens later gets a replay.
import { bridge } from "./bridge.js";
import { skinCss } from "./skin.js";

/** The two shells a terminal can show. `resize(cols, rows)` tells the service the new size. */
export const SHELL = { input: "jarvis/console/input", attach: "jarvis/console/attach", out: "jarvis/console/out",
                       missing: "The console service is not running. Restart JARVIS (python boot.py) to start it.",
                       resize: (cols, rows) => bridge.pub("jarvis/console/resize", { cols, rows }) };
// minRows: Claude's input box is not drawn at all in a grid shorter than about 38 rows (measured against the live session: a frame at row 36 of a
// 40-row grid is there, at 36 rows or fewer it is gone). So the text shrinks to fit 40 rows in the window instead of the grid getting shorter.
export const CLAUDE = { input: "jarvis/claude/input", attach: "jarvis/claude/attach", out: "jarvis/claude/out", minRows: 40,
                        missing: "The Claude bridge did not answer. Restart JARVIS (python boot.py) to start it.",
                        resize: (cols, rows) => bridge.act("claude.resize", { cols, rows }) };
const FONT_PX = 13;                                  // design px; the dashboard's unit scales it (zoom included)
const MIN_FONT_PX = 7;                               // a terminal that needs more rows than fit shrinks its text down to this, then scrolls
const SCROLLBACK = 5000;

// the dashboard's orange-only scheme: every ANSI colour is a step on the amber ramp (dark to bright), so a coloured program stays readable
const ANSI = ["#3a2410", "#ff7733", "#ffbb44", "#ffd27a", "#c97a2a", "#e0884a", "#ffcf9a", "#e8d5c0",
              "#7a5a3a", "#ff9966", "#ffd070", "#ffe3a3", "#e6a05c", "#f0a870", "#ffe2c0", "#fff4e6"];
const theme = () => {
  const names = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
  const t = { background: "rgba(0,0,0,0)", foreground: skinCss("#ffe6cc"), cursor: skinCss("#ffaa00"), cursorAccent: "#140a00", selectionBackground: skinCss("rgba(255,150,0,.35)") };
  ANSI.forEach((c, i) => { t[i < 8 ? names[i] : "bright" + names[i - 8][0].toUpperCase() + names[i - 8].slice(1)] = skinCss(c); });
  return t;
};

let loading = null;
/** Loads xterm.js and its fit addon once (plain scripts: they define window.Terminal and window.FitAddon). */
export function loadXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  return loading || (loading = new Promise((resolve, reject) => {
    const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "/vendor/xterm/xterm.css"; document.head.append(css);
    const script = (src) => new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => rej(new Error(`could not load ${src}`)); document.head.append(s); });
    script("/vendor/xterm/xterm.js").then(() => script("/vendor/xterm/addon-fit.js")).then(resolve, (e) => { loading = null; reject(e); });
  }));
}

const unit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;
const fontPx = () => Math.max(9, Math.round(FONT_PX * unit() * 2) / 2);
const FONT = '"Cascadia Mono", "Cascadia Code", "JetBrains Mono", ui-monospace, Consolas, "DejaVu Sans Mono", monospace';

/** A terminal in `host` (an element with a size) on `service` (SHELL or CLAUDE). Returns { fit, focus, dispose }. Needs loadXterm() to have finished. */
export function createConsole(host, service = SHELL) {
  const term = new window.Terminal({ fontFamily: FONT, fontSize: fontPx(), theme: theme(), cursorBlink: true, allowTransparency: true, scrollback: SCROLLBACK,
                                     macOptionIsMeta: true, allowProposedApi: true });
  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(host);
  host.xterm = term;                                  // for the tests and for poking at it from the console

  let timer = 0;
  term.onData((d) => bridge.pub(service.input, d));       // a shell that ended starts again on the next key (the service does that)
  term.onResize(({ cols, rows }) => service.resize(cols, rows));      // every change of size, whoever made it: the service's shell always has this size

  // copy: Ctrl+Shift+C, or Ctrl+C while something is selected (otherwise Ctrl+C interrupts the program); paste: Ctrl+V / Ctrl+Shift+V
  // (the browser's own paste event, which xterm turns into the shell's input; the key itself must not also send ^V)
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== "keydown") return true;
    const k = e.key.toLowerCase();
    if (e.ctrlKey && k === "c" && (e.shiftKey || term.hasSelection())) {
      if (term.hasSelection()) navigator.clipboard?.writeText(term.getSelection()).catch(() => {});
      return false;
    }
    if (e.ctrlKey && k === "v") return false;
    return true;
  });

  let heard = false;
  const off = bridge.on(service.out, (m) => {
    if (!m || typeof m !== "object") return;
    heard = true;
    if (m.reset) {                                                    // a replay: drawn at the size it was printed at, then the window's own size is fitted
      term.reset();
      if (m.cols && m.rows) term.resize(m.cols, m.rows);
      term.write(m.d || "", fit);
      return;
    }
    if (m.d) term.write(m.d);
    if (m.exit !== undefined && m.exit !== null) { term.write("\r\n\x1b[2m[the shell ended: press any key for a new one]\x1b[0m\r\n"); }
  });

  function fit() {
    if (!host.clientWidth || !host.clientHeight) return;            // hidden: nothing to measure
    let size = fontPx();
    term.options.fontSize = size;
    let dims = fitAddon.proposeDimensions();
    if (!dims) return;
    if (service.minRows && dims.rows < service.minRows) {              // too few rows at this size: a smaller font gives the window the rows it needs
      size = Math.max(MIN_FONT_PX, Math.floor(size * dims.rows / service.minRows * 2) / 2);
      term.options.fontSize = size;
      dims = fitAddon.proposeDimensions() || dims;
    }
    if (service.minRows) term.resize(dims.cols, Math.max(dims.rows, service.minRows));      // (below MIN_FONT_PX the grid is taller than the window and scrolls)
    else fitAddon.fit();                                              // a changed size reaches the service through onResize
  }
  const later = () => { clearTimeout(timer); timer = setTimeout(fit, 60); };
  const ro = new ResizeObserver(later); ro.observe(host);
  window.addEventListener("jarvis-unit", later); window.addEventListener("jarvis-widget-zoom", later);

  fit();
  bridge.pub(service.attach, "1");                                   // the shell starts if it is not running; whatever it printed lately comes back as a replay
  // a service that is not running never answers: say so, instead of a terminal that silently takes no input
  const quiet = setTimeout(() => { if (!heard) term.write("\x1b[33m" + service.missing + "\x1b[0m\r\n"); }, 3000);
  return {
    fit, focus: () => term.focus(),
    dispose() { off(); ro.disconnect(); clearTimeout(timer); clearTimeout(quiet); window.removeEventListener("jarvis-unit", later); window.removeEventListener("jarvis-widget-zoom", later); term.dispose(); },
  };
}
