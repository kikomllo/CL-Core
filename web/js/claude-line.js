// Local line editing for the Claude window (tests/test_webClaudeLine.py runs it under node). Port of the Qt widget's local editing
// (ui/clClaudeWidget.py): while Claude's framed `❯` input box, or a readline-style shell prompt in /terminal, is showing, the line is
// edited ENTIRELY here -- text, cursor, selection, Home/End, word moves, Ctrl+A/E/U/K/W, paste -- and nothing goes to the session until
// it has to: Enter overwrites the CLI's line (Ctrl+E Ctrl+U + the local text) and presses Enter in one message, so the CLI can never
// disagree with what is on screen. Keys that need the CLI's own copy of the line (Tab, Up/Down, other Ctrl+letters) sync first and then
// re-adopt whatever the CLI does with it. Typing therefore never waits for the MQTT -> bridge -> terminal -> snapshot round trip.
//
// Pure: no DOM, the clock is injected. Every method that talks to the session returns the "effects" to send, in order:
//   {keys: "..."} claude.keys   {control: "UP"} claude.control_key   {text: "/terminal"} claude.ask   {copy: "..."} text for the clipboard
import { cursorCell, MODE_SWITCH } from "./claude-model.js";

export const HOLD_S = 0.8;       // after a sync/Enter the snapshots may still show the old CLI line: do not re-adopt it yet
export const ADOPT_S = 1.5;      // after Tab/Up/Down, how long to wait for the CLI's own new line
const CHIP = /^\[⧉[^\]]*\]\s?/;  // the IDE-selection chip Claude draws inside its box: not text of the line

const isSpace = (c) => /\s/.test(c);
const oneChar = (k) => typeof k === "string" && [...k].length === 1;

export class LocalLine {
  constructor(now = () => performance.now() / 1000) {
    this.now = now;
    this.buf = ""; this.pos = 0; this.synced = ""; this.selAnchor = null;
    this.holdUntil = 0; this.adopt = false; this.adoptFrom = ""; this.adoptDeadline = 0;
    this.rows = []; this.cursor = null; this.mode = null; this.idle = null;
    this.shellStart = null; this.shellTouched = false;
    this.prompt = null;                                      // {kind: "claude" | "shell", row, start, line} while a line can be edited locally
  }

  // ---------------------------------------------------------------- the screen
  /** A new snapshot from the bridge (jarvis/claude/screen). */
  setScreen(msg) {
    this.rows = String(msg?.text ?? "").split("\n");
    this.cursor = cursorCell(msg);
    this.idle = msg?.idle ?? null;
    if ((msg?.mode ?? null) !== this.mode) { this.shellStart = null; this.shellTouched = false; }
    this.mode = msg?.mode ?? null;
    this.refresh();
  }

  refresh() {
    this.#updateShellStart();
    this.prompt = this.#computePrompt();
    if (this.prompt) this.#adoptSnapshot();
  }

  /** True while the hold after a sync is running: the view should call refresh() again once it is over (nothing else may arrive). */
  get holdLeft() { return Math.max(0, this.holdUntil - this.now()); }

  #shellReady() { return this.mode === "terminal" && this.idle === true && this.cursor !== null; }

  #updateShellStart() {
    if (!this.#shellReady()) { this.shellStart = null; this.shellTouched = false; return; }
    if (this.shellTouched || this.now() < this.holdUntil) return;
    this.shellStart = this.cursor.col > 0 ? this.cursor.col : null;      // the prompt's own length, whatever characters it has
  }

  static framedRow(rows) {
    for (let i = 1; i < rows.length - 1; i++) {
      if (rows[i].trimStart().startsWith("❯") && rows[i - 1].startsWith("─") && rows[i + 1].startsWith("─")) return i;
    }
    return null;
  }

  #padded(row, index) {
    // Trailing spaces are trimmed from every published row but the real cursor still sits after them; an empty prompt arrives as "❯".
    if (this.cursor && this.cursor.row === index && this.cursor.col > row.length) return row.padEnd(this.cursor.col);
    return row && "❯$#%".includes(row[row.length - 1]) ? row + " " : row;
  }

  #computePrompt() {
    const framed = LocalLine.framedRow(this.rows);
    if (framed !== null) {
      const row = this.#padded(this.rows[framed], framed), i = row.indexOf("❯");
      let start = i >= 0 ? i + 2 : row.length;
      const chip = CHIP.exec(row.slice(start));
      if (chip) start += chip[0].length;
      return { kind: "claude", row: framed, start, line: row.slice(start).trimEnd() };
    }
    if (this.#shellReady() && this.shellStart !== null) {
      const { col, row: r } = this.cursor, start = this.shellStart;
      if (r >= 0 && r < this.rows.length && col >= start) return { kind: "shell", row: r, start, line: this.rows[r].padEnd(col).slice(start, col) };   // after the cursor is the shell's suggestion
    }
    return null;
  }

  #setLine(line, pos = null) {
    this.buf = this.synced = line;
    this.pos = pos === null ? line.length : Math.max(0, Math.min(pos, line.length));
    this.selAnchor = null;
  }

  #adoptSnapshot() {
    const now = this.now(), { row: target, start, line } = this.prompt;
    if (this.adopt) {
      if (now > this.adoptDeadline) this.adopt = false;
      else if (line !== this.adoptFrom.trimEnd()) { this.adopt = false; this.#setLine(line); return; }
    }
    if (this.buf === this.synced && now >= this.holdUntil && line !== this.buf.trimEnd()) {
      this.#setLine(line, this.cursor && this.cursor.row === target ? this.cursor.col - start : null);
    }
  }

  // ---------------------------------------------------------------- what to draw
  get active() { return this.prompt !== null; }
  get kind() { return this.prompt ? this.prompt.kind : null; }
  get selection() { return this.selAnchor === null || this.selAnchor === this.pos ? null : [Math.min(this.selAnchor, this.pos), Math.max(this.selAnchor, this.pos)]; }

  /** {row, start, text, cursor: {row, col}, selection} for the line being edited, or null. */
  get view() {
    if (!this.prompt) return null;
    const { row, start } = this.prompt;
    return { row, start, text: this.buf, cursor: { row, col: start + this.pos }, selection: this.selection };
  }

  // ---------------------------------------------------------------- editing
  #touch() { if (this.kind === "shell") this.shellTouched = true; }       // fixes the measured prompt start (a shell prompt only)
  #hold() { this.holdUntil = this.now() + HOLD_S; }

  #edit(start, end, text) {
    this.#touch();
    this.buf = this.buf.slice(0, start) + text + this.buf.slice(end);
    this.pos = start + text.length; this.selAnchor = null;
  }
  #move(pos, extend) {
    this.#touch();
    pos = Math.max(0, Math.min(pos, this.buf.length));
    if (extend) { if (this.selAnchor === null) this.selAnchor = this.pos; } else this.selAnchor = null;
    this.pos = pos;
  }
  #wordLeft(p) { while (p > 0 && isSpace(this.buf[p - 1])) p--; while (p > 0 && !isSpace(this.buf[p - 1])) p--; return p; }
  #wordRight(p) { const n = this.buf.length; while (p < n && !isSpace(this.buf[p])) p++; while (p < n && isSpace(this.buf[p])) p++; return p; }
  #span() { return this.selection || [this.pos, this.pos]; }

  /** Typed text or a paste (line breaks become spaces: the box is one line). */
  insert(text) {
    if (!this.active) return [];
    const [a, b] = this.#span();
    this.#edit(a, b, String(text).replace(/\r\n|\n|\r/g, " "));
    return [];
  }

  /** Overwrite the CLI's line with the local one (end-of-line, kill-line, then the text). */
  #sync() {
    const fx = [];
    if (this.buf !== this.synced) { fx.push({ keys: "\x05\x15" + this.buf }); this.synced = this.buf; }
    this.#hold();
    return fx;
  }

  // For keys whose meaning depends on the CLI's own copy of the line (Tab completion, history, most Ctrl shortcuts): sync first, send the
  // key, then adopt whatever the CLI does with the line.
  #afterSync(effect) {
    this.#touch();
    const fx = [...this.#sync(), effect];
    this.adopt = true; this.adoptFrom = this.synced; this.adoptDeadline = this.now() + ADOPT_S;
    this.selAnchor = null;
    return fx;
  }

  #enter() {
    const cmd = this.buf.trim().toLowerCase();
    if (MODE_SWITCH.includes(cmd)) {                       // a widget-level mode switch, not something to type into the CLI
      this.buf = ""; this.pos = 0; this.selAnchor = null; this.#hold();
      return [{ text: cmd }];
    }
    const fx = [...this.#sync(), { control: "ENTER" }];
    this.buf = this.synced = ""; this.pos = 0; this.selAnchor = null;
    this.shellStart = null; this.shellTouched = false;     // the next prompt is measured afresh once the hold is over
    this.refresh();
    return fx;
  }

  /** One key press (a KeyboardEvent-like object). Returns null when the line editor has no opinion (the caller forwards it as a plain
   *  mirror key), otherwise {effects, paste?}: effects to send, and `paste: true` for Ctrl+V (the page reads the clipboard). */
  key(e) {
    if (!this.active) return null;
    const { key } = e, ctrl = !!e.ctrlKey, shift = !!e.shiftKey, alt = !!e.altKey;
    if (e.metaKey) return null;
    const n = this.buf.length, sel = this.selection, word = ctrl || alt, k = oneChar(key) ? key.toLowerCase() : key;
    const done = (effects = []) => ({ effects });

    if (key === "Backspace" && word) { this.#edit(...(sel || [this.#wordLeft(this.pos), this.pos]), ""); return done(); }
    if (key === "Delete" && word) { this.#edit(...(sel || [this.pos, this.#wordRight(this.pos)]), ""); return done(); }
    if (key === "ArrowLeft") {
      if (sel && !shift && !word) this.#move(sel[0], false); else this.#move(word ? this.#wordLeft(this.pos) : this.pos - 1, shift);
      return done();
    }
    if (key === "ArrowRight") {
      if (sel && !shift && !word) this.#move(sel[1], false); else this.#move(word ? this.#wordRight(this.pos) : this.pos + 1, shift);
      return done();
    }
    if (key === "Home") { this.#move(0, shift); return done(); }
    if (key === "End") { this.#move(n, shift); return done(); }
    if (ctrl && !alt && oneChar(key) && /[a-z]/.test(k)) {
      if (k === "a") {
        if (this.kind === "shell") this.#move(0, false);                  // readline: beginning of line
        else { this.selAnchor = n ? 0 : null; this.pos = n; }             // Claude's box: select all
        return done();
      }
      if (k === "c") {
        if (sel) return done([{ copy: this.buf.slice(sel[0], sel[1]) }]);
        this.buf = this.synced = ""; this.pos = 0; this.selAnchor = null; this.#hold();
        return done([{ keys: "\x03" }]);
      }
      if (k === "x") { if (!sel) return done(); const text = this.buf.slice(sel[0], sel[1]); this.#edit(sel[0], sel[1], ""); return done([{ copy: text }]); }
      if (k === "v") return { effects: [], paste: true };
      if (k === "u") { this.#edit(0, this.pos, ""); return done(); }
      if (k === "k") { this.#edit(this.pos, n, ""); return done(); }
      if (k === "w") { this.#edit(this.#wordLeft(this.pos), this.pos, ""); return done(); }
      if (k === "e") { this.#move(n, false); return done(); }
      if (k === "z") return null;                                          // would suspend the CLI: never sent
      return done(this.#afterSync({ keys: String.fromCharCode(k.charCodeAt(0) - 96) }));
    }
    if (ctrl) return null;
    if (key === "Delete") {
      if (sel) this.#edit(sel[0], sel[1], ""); else if (this.pos < n) this.#edit(this.pos, this.pos + 1, "");
      return done();
    }
    if (key === "Backspace") {
      if (sel) this.#edit(sel[0], sel[1], ""); else if (this.pos > 0) this.#edit(this.pos - 1, this.pos, "");
      return done();
    }
    if (key === "Enter") return done(this.#enter());
    if (key === "Escape") return done([{ control: "ESCAPE" }]);
    if (key === "Tab" && shift) return done([{ control: "SHIFT_TAB" }]);
    if (key === "Tab") return done(this.#afterSync({ keys: "\t" }));
    if (key === "ArrowUp" || key === "ArrowDown") return done(this.#afterSync({ control: key === "ArrowUp" ? "UP" : "DOWN" }));
    if (!alt && oneChar(key)) { this.insert(key); return done(); }
    return null;
  }
}
