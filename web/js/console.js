// The full terminal: xterm.js (vendored in web/vendor/xterm, no network) drawing the shell that src/clConsole.py keeps alive (jarvis/console/*).
// Nothing is interpreted on this side or in between: keystrokes go to the shell as typed (paste included) and its output comes back as it
// printed, so typing, colours, full-screen programs, selection and scrollback are a terminal's own. The shell belongs to the console service, not to
// this window: closing the window or restarting the dashboard leaves it (and whatever runs in it) alone, and a window that opens later gets a replay.
import { bridge } from "./bridge.js";
import { skinCss } from "./skin.js";

const SERVICE = { input: "jarvis/console/input", resize: "jarvis/console/resize", attach: "jarvis/console/attach", out: "jarvis/console/out" };
const FONT_PX = 13;                                  // design px; the dashboard's unit scales it (zoom included)
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

/** A terminal in `host` (an element with a size). Returns { fit, focus, dispose }. Needs loadXterm() to have finished. */
export function createConsole(host) {
  const term = new window.Terminal({ fontFamily: FONT, fontSize: fontPx(), theme: theme(), cursorBlink: true, allowTransparency: true, scrollback: SCROLLBACK,
                                     macOptionIsMeta: true, allowProposedApi: true });
  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(host);

  let sizeKey = "", timer = 0;
  term.onData((d) => bridge.pub(SERVICE.input, d));       // a shell that ended starts again on the next key (the service does that)
  term.onResize(({ cols, rows }) => bridge.pub(SERVICE.resize, { cols, rows }));

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

  const off = bridge.on(SERVICE.out, (m) => {
    if (!m || typeof m !== "object") return;
    if (m.reset) term.reset();
    if (m.d) term.write(m.d);
    if (m.exit !== undefined && m.exit !== null) { term.write("\r\n\x1b[2m[the shell ended: press any key for a new one]\x1b[0m\r\n"); }
  });

  function fit() {
    if (!host.clientWidth || !host.clientHeight) return;            // hidden: nothing to measure
    term.options.fontSize = fontPx();
    fitAddon.fit();
    const key = `${term.cols}x${term.rows}`;
    if (key !== sizeKey) { sizeKey = key; bridge.pub(SERVICE.resize, { cols: term.cols, rows: term.rows }); }
  }
  const later = () => { clearTimeout(timer); timer = setTimeout(fit, 60); };
  const ro = new ResizeObserver(later); ro.observe(host);
  window.addEventListener("jarvis-unit", later); window.addEventListener("jarvis-widget-zoom", later);

  fit();
  bridge.pub(SERVICE.attach, "1");                                   // the shell starts if it is not running; whatever it printed lately comes back as a replay
  return {
    fit, focus: () => term.focus(),
    dispose() { off(); ro.disconnect(); clearTimeout(timer); window.removeEventListener("jarvis-unit", later); window.removeEventListener("jarvis-widget-zoom", later); term.dispose(); },
  };
}
