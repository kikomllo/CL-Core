// Karaoke lyrics between the orb and the command bar, "Lyrics Revolver" style: next (small), current (big), previous (small).
// Advancing by one line plays a 650 ms InOutCubic revolver: the next line grows and slides up into the current row, the current
// shrinks into the previous row, and a brand-new next line grows in from a small size. Port of clUI.LyricsDisplay + the artifact's two fixes
// (the previous row is hidden during the move; the new next line animates instead of popping in).
//
// Wrapping: a line is wrapped ONCE, at the style it will have as the current line (the big one), and shown with those line breaks
// in every row (white-space: pre), so growing or shrinking it never re-wraps; a small row just shows the big row's breaks at a
// smaller size. Rows are as tall as their lines need (nothing is clipped) and sit around a fixed centre line, so the current line
// stays put while the next one grows upward and the previous one downward.
import { bridge } from "./bridge.js";
import { skinCss } from "./skin.js";

const DUR = 650, DIM_PX = 14, CUR_PX = 26, ENTRY_PX = 8, ENTRY_OFFSET = 12, DIM_W = 400, CUR_W = 800;
const LINE_HEIGHT = 1.15, GAP = 4, PAD = 16, MAX_LINES = 4, MAX_WIDTH = 700, GUTTER = 32;     // design px; MAX_WIDTH / GUTTER match #lyrics' width
const CUR = "rgba(255,170,0,.9)", DIM = "rgba(255,170,0,.588)";
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const lerp = (a, b, t) => a + (b - a) * t;
const KEY = "jarvis.lyrics.enabled";

const root = document.getElementById("lyrics");
const row = (cls) => Object.assign(document.createElement("div"), { className: "ly-row " + cls });
const rNext = row("ly-next"), rCur = row("ly-cur"), rPrev = row("ly-prev");
root.append(rNext, rCur, rPrev);

let lines = [], index = -1, animating = false;
let lyricsTrack = null, currentTrack = null, playing = false, pos = 0, capturedAt = 0;
let enabled = true;
try { enabled = localStorage.getItem(KEY) !== "0"; } catch { /* private mode */ }

const unit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;
const text = (i) => (i >= 0 && i < lines.length ? lines[i].text : "");
const position = () => (playing ? pos + (performance.now() - capturedAt) / 1000 : pos);
const indexFor = (p) => { let r = -1; for (let i = 0; i < lines.length; i++) { if (lines[i].time <= p) r = i; else break; } return r; };
const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];

// ---- wrapping at the current line's style ----
const measure = document.createElement("canvas").getContext("2d");
const wrapCache = new Map();
/** The line's text broken to fit the big style: an array of display lines (at most MAX_LINES, the last ellipsised). */
function wrap(t) {
  if (!t) return [];
  const u = unit(), width = Math.min(MAX_WIDTH * u, innerWidth - GUTTER * u) - PAD * u, family = getComputedStyle(document.body).fontFamily;
  const key = `${width}|${u}|${t}`;
  if (wrapCache.has(key)) return wrapCache.get(key);
  measure.font = `${CUR_W} ${CUR_PX * u}px ${family}`;
  const fits = (s) => measure.measureText(s).width <= width;
  const out = [];
  let cur = "";
  for (let word of t.split(/\s+/).filter(Boolean)) {
    while (!fits(word)) {                                            // a word wider than the row: break it by characters
      let n = word.length;
      while (n > 1 && !fits(word.slice(0, n))) n--;
      if (cur) { out.push(cur); cur = ""; }
      out.push(word.slice(0, n)); word = word.slice(n);
    }
    const next = cur ? cur + " " + word : word;
    if (fits(next)) cur = next; else { if (cur) out.push(cur); cur = word; }
  }
  if (cur) out.push(cur);
  if (out.length > MAX_LINES) { out.length = MAX_LINES; out[MAX_LINES - 1] = out[MAX_LINES - 1].replace(/\s*\S{0,2}$/, "") + "…"; }
  if (wrapCache.size > 400) wrapCache.clear();
  wrapCache.set(key, out);
  return out;
}
const count = (t) => wrap(t).length;
const height = (n, px, u) => n * px * u * LINE_HEIGHT;

/** Tops and heights of the three rows (design px * unit, relative to the centre line) for the given line counts. */
function metrics(curN, nextN, prevN) {
  const u = unit(), Hc = height(curN, CUR_PX, u), Hn = height(nextN, DIM_PX, u), Hp = height(prevN, DIM_PX, u), g = GAP * u;
  return { u, cur: { top: -Hc / 2, h: Hc }, next: { top: -Hc / 2 - g - Hn, h: Hn }, prev: { top: Hc / 2 + g, h: Hp } };
}
const setRow = (el, t, top) => { el.textContent = wrap(t).join("\n"); el.style.top = top + "px"; };

function refreshTexts() {
  const m = metrics(count(text(index)), count(text(index + 1)), count(text(index - 1)));
  setRow(rCur, text(index), m.cur.top); setRow(rNext, text(index + 1), m.next.top); setRow(rPrev, text(index - 1), m.prev.top);
}
function refreshVisibility() {
  const show = enabled && playing && lines.length > 0 && same(lyricsTrack, currentTrack);
  root.hidden = !show;
}

function promote(target) {
  const oldCur = text(index), oldNext = text(index + 1), oldPrev = text(index - 1);
  index = target;
  const newNext = text(index + 1);
  if (!oldNext) { refreshTexts(); return; }
  animating = true;
  [rCur, rNext, rPrev].forEach(r => (r.style.visibility = "hidden"));
  const mk = (t) => { const d = row("ly-temp"); d.textContent = wrap(t).join("\n"); root.append(d); return d; };
  const up = mk(oldNext), down = mk(oldCur), entry = newNext ? mk(newNext) : null;
  const A = metrics(count(oldCur), count(oldNext), count(oldPrev)), B = metrics(count(oldNext), count(newNext), count(oldCur)), u = A.u;
  const nUp = count(oldNext), nDown = count(oldCur), nEntry = count(newNext), t0 = performance.now();
  const step = (now) => {
    const lin = Math.min(1, (now - t0) / DUR), e = ease(lin);
    const fUp = lerp(DIM_PX, CUR_PX, e), fDown = lerp(CUR_PX, DIM_PX, e);
    // The three moving lines are one column, top to bottom: the new next line, the line rising into the centre, the line sinking
    // into the previous row. The column is anchored by the sinking line's top (it travels from the centre to the previous row), and
    // the others stack above it from their current heights, so they can never overlap whatever their line counts, and the first
    // and last frames are exactly the resting layouts. Each keeps its line breaks (no re-wrapping while it resizes).
    const g = GAP * u, fE = lerp(ENTRY_PX, DIM_PX, e);
    const hUp = height(nUp, fUp, u), hEntry = height(nEntry, fE, u);
    const downTop = lerp(A.cur.top, B.prev.top, e), upTop = downTop - g - hUp;
    Object.assign(up.style, { top: upTop + "px", color: skinCss(CUR), fontSize: `calc(${fUp} * var(--u))`, fontWeight: lerp(DIM_W, CUR_W, e) });
    Object.assign(down.style, { top: downTop + "px", color: skinCss(DIM), fontSize: `calc(${fDown} * var(--u))`, fontWeight: lerp(CUR_W, DIM_W, e) });
    if (entry) Object.assign(entry.style, { top: upTop - g - hEntry + ENTRY_OFFSET * u * (1 - e) + "px", color: skinCss(DIM), fontSize: `calc(${fE} * var(--u))`, fontWeight: DIM_W, opacity: e });
    if (lin < 1) requestAnimationFrame(step);
    else { up.remove(); down.remove(); entry?.remove(); animating = false; refreshTexts(); [rCur, rNext, rPrev].forEach(r => (r.style.visibility = "")); }
  };
  requestAnimationFrame(step);
}

setInterval(() => {
  if (root.hidden || animating || !lines.length) return;
  const n = indexFor(position());
  if (n === index) return;
  if (index >= 0 && n > index) promote(n);       // forward: always the revolver
  else { index = n; refreshTexts(); }             // first line or a rewind: snap
}, 100);

bridge.on("jarvis/sys/spotify_lyrics", (d) => {
  lyricsTrack = [d.title || "Unknown", d.artist || "Unknown"];
  lines = d.found ? (d.lines || []) : [];
  index = -1; if (!animating) refreshTexts();
  refreshVisibility();
});
bridge.on("jarvis/sys/media_status", (d) => {
  if (typeof d !== "object") return;
  currentTrack = [d.title || "Unknown", d.artist || "Unknown"];
  playing = d.status === "Playing"; pos = d.position || 0; capturedAt = performance.now();
  refreshVisibility();
});
// the line breaks depend on the width, the unit and the font: redo them when any of those change
const relayout = () => { wrapCache.clear(); if (!animating) refreshTexts(); };
addEventListener("resize", relayout);
document.fonts?.ready.then(relayout);

export const lyrics = {
  get enabled() { return enabled; },
  setEnabled(on) {
    enabled = on; refreshVisibility();
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private mode */ }
  },
};
