// The Claude window: the voice bridge's terminal (jarvis/claude/screen, published by src/clClaudeBridge.py) drawn as rows of text, with
// the keyboard forwarded to it. The bridge already emulates the terminal, so the page only draws its snapshots. While Claude's input box
// (or a readline shell prompt) is showing, the line is edited LOCALLY (claude-line.js) and only reaches the session on Enter, so typing
// never waits for the round trip; everywhere else (menus, a program running in the shell, cmd.exe) it is a pure mirror: each key goes to
// the session as a terminal would send it and the screen is whatever the next snapshot says.
// "/terminal" and "/claude" (typed, or the button) switch between Claude and a plain shell; neither session stops on a switch.
// Resizing the window resizes the real terminal grid (claude.resize, debounced): the columns always fit the window, so it never scrolls sideways.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";
import { screenRows, cursorCell, cutAfter, hidesSuggestion, keyAction, submitAction, nextShadow, gridFor, isBusy, cellRuns, lineSpans } from "./claude-model.js";
import { LocalLine } from "./claude-line.js";

const RESIZE_DEBOUNCE_MS = 300;
// The grid follows the window's width, so there is never a horizontal scrollbar. The rows never go below 40: Claude redraws a frame as tall as
// the conversation, and in a shorter grid the bottom of it (the input box) falls off the screen, so a short window scrolls vertically to the
// bottom instead. The columns never go below 40 (the window's own minimum width is about 44). Only one client may size the terminal: with the
// Qt dashboard's Claude widget also open the two overwrite each other (the Qt UI is off by default now that the desktop shell is the UI).
const MIN_ROWS = 40, MIN_COLS = 40;

/** One span as a DOM node: colours are brightness steps on the skin's accent (see claude-model.js). */
function spanEl(s) {
  const el = document.createElement("span");
  const runs = cellRuns(s.text);
  if (runs.every((r) => !r.sym)) el.textContent = s.text;
  else for (const r of runs) { if (r.sym) { const i = document.createElement("i"); i.className = "cl-sym"; i.textContent = r.text; el.append(i); } else el.append(r.text); }
  const st = [];
  if (s.sel) el.className = "cl-sel";
  if (s.reverse) st.push("background:var(--accent)", "color:#140a00");
  else {
    if (s.fgV != null) st.push(`color:color-mix(in srgb, var(--accent) ${Math.round(s.fgV * 100)}%, color-mix(in srgb, var(--accent) 45%, #000))`);
    if (s.bgV != null) st.push(`background:color-mix(in srgb, var(--accent) ${Math.round((30 + 90 * s.bgV) / 2.55)}%, transparent)`);
  }
  if (s.bold) st.push("font-weight:700");
  if (s.italic) st.push("font-style:italic");
  if (s.underline) st.push("text-decoration:underline");
  if (st.length) el.style.cssText = st.join(";");
  return el;
}

export const claudeWindow = {
  id: "widget_claude", title: "Claude", w: 760, h: 480,
  build(body, win) {
    let msg = null, shadow = "", resizeTimer = null, holdTimer = null, lastGrid = "", pinned = true, cell = { w: 8, h: 17 };
    const line = new LocalLine();
    const probe = h("div", { class: "cl-row cl-probe", "aria-hidden": "true" }, "M".repeat(100));      // one row of 100 cells: gives the cell width and the row height
    const rowsEl = h("div", { class: "cl-rows" });
    const cursor = h("div", { class: "cl-cursor", "aria-hidden": "true", hidden: true });
    const hint = h("div", { class: "cl-hint", "aria-hidden": "true" }, "click to type");
    const content = h("div", { class: "cl-content" }, rowsEl, cursor);      // the cursor scrolls with the rows
    const screen = h("div", { class: "cl-screen", role: "log", "aria-live": "off", "aria-label": "Claude terminal: click and type", tabindex: 0 }, content, probe);

    // the title bar carries the state: a mode chip with a busy pulse after the title, the Claude/shell switch before the pin
    const chip = h("span", { class: "cl-chip" }, h("i", { class: "cl-pulse" }), h("span", {}, "…"));
    const swap = h("button", { class: "chrome-btn cl-swap", title: "Switch between the Claude session and a plain shell (/terminal, /claude)", "aria-label": "Switch between Claude and a plain shell",
      onclick: () => { send("text", currentMode() === "claude" ? "/terminal" : "/claude"); screen.focus(); } }, ">_");
    win.title.append(chip); win.head.insertBefore(swap, win.pin);
    body.append(h("div", { class: "cl-wrap" }, screen, hint));

    const currentMode = () => (msg && msg.mode === "claude" ? "claude" : msg ? "terminal" : "claude");
    function send(kind, value) {
      if (kind === "text") bridge.act("claude.ask", { text: value });
      else if (kind === "keys") bridge.act("claude.keys", { keys: value });
      else bridge.act("claude.control_key", { control_key: value });
    }

    // ---- drawing ----
    let spansByRow = [], rowEls = [];
    const rowOf = (spans) => {
      const row = h("div", { class: "cl-row" });
      spans.forEach((sp) => row.append(spanEl(sp)));
      if (!row.firstChild) row.append(document.createTextNode(" "));        // an empty row keeps its height
      return row;
    };
    const withLine = (i) => { const v = line.view; return v && v.row === i ? lineSpans(spansByRow[i], v.start, v.text, v.selection) : spansByRow[i]; };
    function drawLine() {                                                  // a local edit: only the line being edited changes
      const v = line.view;
      if (!v || !rowEls[v.row]) return draw();
      const next = rowOf(withLine(v.row));
      rowEls[v.row].replaceWith(next); rowEls[v.row] = next;
      placeCursor(v.cursor);
    }
    function draw() {
      const at = cursorCell(msg), cut = hidesSuggestion(msg);
      spansByRow = screenRows(msg).map((spans, i) => (cut && at && i === at.row ? cutAfter(spans, at.col) : spans));
      rowEls = spansByRow.map((_, i) => rowOf(withLine(i)));
      rowsEl.replaceChildren(...rowEls);
      placeCursor(line.view?.cursor ?? at);
      if (pinned) screen.scrollTop = screen.scrollHeight;
      const m = currentMode();
      const busy = isBusy(msg);
      chip.lastChild.textContent = busy ? "working" : m === "claude" ? "session" : "shell";
      chip.classList.toggle("busy", busy);
      swap.classList.toggle("on", m !== "claude");
    }
    function placeCursor(at) {
      cursor.hidden = !at;
      if (!at) return;
      cursor.style.left = `${at.col * cell.w}px`; cursor.style.top = `${at.row * cell.h}px`;
      cursor.style.width = `${cell.w}px`; cursor.style.height = `${cell.h}px`;
    }
    function measure() {
      const r = probe.getBoundingClientRect();
      if (r.width) cell = { w: r.width / 100, h: r.height };
    }

    // ---- the window only re-measures its cell (a zoom or font change moves the cursor) ----
    function resize() {
      measure();
      const cs = getComputedStyle(screen), pad = (a, b) => parseFloat(cs[a]) + parseFloat(cs[b]);
      const w = screen.clientWidth - pad("paddingLeft", "paddingRight") - 1, hh = screen.clientHeight - pad("paddingTop", "paddingBottom");      // -1: a fractional cell never tips a column over
      if (w > 0 && hh > 0) {
        const g = gridFor(w, hh, cell.w, cell.h, MIN_ROWS, MIN_COLS), key = `${g.cols}x${g.rows}`;
        if (key !== lastGrid) { lastGrid = key; bridge.act("claude.resize", g); }      // a real resize is backend work: only when the grid changed
      }
      placeCursor(line.view?.cursor ?? cursorCell(msg));
    }
    const ro = new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, RESIZE_DEBOUNCE_MS); });
    ro.observe(screen);
    const onUnit = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, RESIZE_DEBOUNCE_MS); };      // a zoom: the cell is another size, the window may not be
    window.addEventListener("jarvis-unit", onUnit);

    // ---- following the bottom (where the input box is) unless you scroll up ----
    screen.addEventListener("scroll", () => { pinned = screen.scrollTop + screen.clientHeight >= screen.scrollHeight - 4; });

    // ---- keyboard ----
    function run(effects) {                                                // what a local edit has to tell the session (usually nothing)
      for (const f of effects) {
        if (f.keys !== undefined) send("keys", f.keys);
        else if (f.control) send("control", f.control);
        else if (f.text) send("text", f.text);
        else if (f.copy !== undefined) navigator.clipboard?.writeText(f.copy).catch(() => {});
      }
    }
    function afterLocal() {
      drawLine();
      clearTimeout(holdTimer);                                             // once a hold is over the screen may need another look: nothing else may arrive
      if (line.holdLeft > 0) holdTimer = setTimeout(() => { line.refresh(); draw(); }, line.holdLeft * 1000 + 50);
    }
    function apply(a) {                                                    // the pure mirror: no local line here
      if (a.control === "ENTER") {
        const sw = submitAction(shadow);
        if (sw) { send("keys", "\x7f".repeat(sw.erase)); send("text", sw.text); shadow = ""; return; }
      }
      send(a.control ? "control" : "keys", a.control ?? a.keys);
      shadow = nextShadow(shadow, a);
    }
    screen.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.key.toLowerCase() === "c" && getSelection()?.toString()) return;      // the browser copies a mouse selection
      const local = line.key(e);
      if (local) {                                                         // editing the line here: nothing leaves until Enter or a key that needs the CLI
        e.preventDefault(); pinned = true; screen.scrollTop = screen.scrollHeight;
        run(local.effects);
        if (!local.paste) afterLocal();
        return;
      }
      const a = keyAction(e, !!getSelection()?.toString());
      if (!a) return;
      if (a.copy) return;                                                // the browser copies the selection
      e.preventDefault();
      pinned = true; screen.scrollTop = screen.scrollHeight;            // typing brings you back to the input
      if (a.paste) return;                                               // handled by the paste event, which has the text
      apply(a);
    });
    screen.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = e.clipboardData?.getData("text");
      if (!text) return;
      if (line.active) { line.insert(text); afterLocal(); return; }
      send("keys", text); shadow = nextShadow(shadow, { keys: text });
    });
    screen.addEventListener("click", () => { if (!getSelection()?.toString()) screen.focus(); });

    const off = bridge.on("jarvis/claude/screen", (m) => { if (m && typeof m === "object") { msg = m; line.setScreen(m); draw(); } });
    queueMicrotask(() => { measure(); resize(); });
    return { onClose: () => { off(); ro.disconnect(); window.removeEventListener("jarvis-unit", onUnit); clearTimeout(resizeTimer); clearTimeout(holdTimer); } };
  },
};
