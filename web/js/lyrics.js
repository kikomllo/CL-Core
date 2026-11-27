// Karaoke lyrics between the orb and the command bar, "Lyrics Revolver" style: the upcoming lines (small, 0-5 of them), the current line (big),
// the finished ones (small, 0-5). How many of each and the font size are settings (lyrics-settings.js, Settings > Display); the font size scales
// the big and the small lines together. Advancing by one line plays a 650 ms InOutCubic revolver: every line moves one place (the next line grows
// and slides up into the current row, the current one shrinks into the first previous row, the others shift, a brand-new line grows in at the top
// and the oldest one fades out at the bottom). Port of clUI.LyricsDisplay + the artifact's two fixes (the new line animates instead of popping in).
//
// Wrapping: a line is wrapped ONCE, at the style it will have as the current line (the big one), and shown with those line breaks
// in every row (white-space: pre), so growing or shrinking it never re-wraps; a small row just shows the big row's breaks at a
// smaller size. Rows are as tall as their lines need (nothing is clipped) and sit around a centre line: the current line is centred on it, the
// next ones stack upward and the previous ones downward. The column is placed in the gap between the visualizer and the highest pill (lyrics-model.js
// plan): its bottom edge stays at its default place and always keeps a padding above the pills, extra lines stretch along the top, and when
// the gap is too small the column is lowered towards the pills and then its font is shrunk to fit.
import { bridge } from "./bridge.js";
import { skinCss, activeSkin } from "./skin.js";
import { CUR_PX, LINE_HEIGHT, REF_BOTTOM, rowPx, gapAt, opacityAt, reach, plan } from "./lyrics-model.js";
import { getLyricsSettings } from "./lyrics-settings.js";

const DUR = 650, ENTRY_OFFSET = 12, DIM_W = 400, CUR_W = 800;
const VIS_BOTTOM = { jarvis: 105, ultron: 160 }, PILL_PAD = 28;      // design px: how low the visualizer reaches below the screen centre (the rings are 80 u, the Ultron head 150 u high, plus a margin), and the padding kept above the pills
const PAD = 16, MAX_LINES = 4, MAX_WIDTH = 700, GUTTER = 32;     // design px; MAX_WIDTH / GUTTER match #lyrics' width
const CUR = "rgba(255,170,0,.9)", DIM = "rgba(255,170,0,.588)";
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const lerp = (a, b, t) => a + (b - a) * t;
const KEY = "jarvis.lyrics.enabled";

const root = document.getElementById("lyrics");
const row = (cls) => Object.assign(document.createElement("div"), { className: "ly-row " + cls });
const rCur = row("ly-cur");
let nextRows = [], prevRows = [], S = getLyricsSettings();      // S: {before, after, size}; nextRows[k] shows line index+1+k, prevRows[k] line index-1-k
let eff = S.size, bottom = REF_BOTTOM, shown = { before: S.before, after: S.after };      // what is really used: the font factor (the user's, shrunk when the gap to the pills is too small), the column's bottom edge (design px below the centre line) and the rows that fit
const range = (n) => Array.from({ length: n }, (_, k) => k);
/** The row's font size by its distance from the current line (the further, the smaller). */
const sized = (el, d) => { el.style.fontSize = `calc(${rowPx(d) * eff} * var(--u))`; el.style.opacity = opacityAt(d); return el; };
const restRows = () => [rCur, ...nextRows, ...prevRows];
function buildRows() {
  nextRows = range(shown.after).map((k) => sized(row("ly-next"), k + 1)); prevRows = range(shown.before).map((k) => sized(row("ly-prev"), k + 1));
  root.replaceChildren(...restRows());
  root.style.setProperty("--ly-s", eff);
}
buildRows();

let lines = [], index = -1, animating = false, epoch = 0;
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
  const key = `${width}|${u}|${eff}|${t}`;
  if (wrapCache.has(key)) return wrapCache.get(key);
  measure.font = `${CUR_W} ${CUR_PX * eff * u}px ${family}`;
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

/** Tops and heights of the rows (design px * unit, relative to the centre line) for the given line counts: the current row, the next rows
 *  (nearest first, stacked upward), the previous rows (nearest first, stacked downward) and where the previous column starts (`prev0`, also
 *  when there are no previous rows). Every top carries the shift that keeps the column's bottom where the default layout had it. */
function metrics(curN, nextNs, prevNs) {
  const u = unit(), Hc = height(curN, CUR_PX * eff, u);
  const cur = { top: -Hc / 2 + (bottom - reach(shown.before, eff)) * u, h: Hc }, next = [], prev = [];
  let top = cur.top;
  nextNs.forEach((n, k) => { const h = height(n, rowPx(k + 1) * eff, u); top -= gapAt(k + 1, eff) * u + h; next.push({ top, h }); });
  top = cur.top + Hc;
  let prev0 = top + gapAt(1, eff) * u, end = top;        // end: the bottom of the lowest line that is there
  prevNs.forEach((n, k) => { const h = height(n, rowPx(k + 1) * eff, u); top += gapAt(k + 1, eff) * u; prev.push({ top, h }); if (n) end = top + h; top += h; });
  // lines that wrap to more rows than the nominal layout assumed would reach below the bottom edge (towards the pills): the whole column is raised
  const lift = Math.min(0, bottom * u - end);
  if (lift) { cur.top += lift; prev0 += lift; for (const r of next) r.top += lift; for (const r of prev) r.top += lift; }
  return { u, cur, next, prev, prev0 };
}
/** The resting layout when `idx` is the current line. */
const metricsAt = (idx) => metrics(count(text(idx)), range(shown.after).map((k) => count(text(idx + 1 + k))), range(shown.before).map((k) => count(text(idx - 1 - k))));
const setRow = (el, t, top) => { el.textContent = wrap(t).join("\n"); el.style.top = top + "px"; };

function refreshTexts() {
  const m = metricsAt(index);
  setRow(rCur, text(index), m.cur.top);
  nextRows.forEach((r, k) => setRow(r, text(index + 1 + k), m.next[k].top));
  prevRows.forEach((r, k) => setRow(r, text(index - 1 - k), m.prev[k].top));
}
/** Fits the column into the gap between the visualizer and the highest pill (see plan in lyrics-model.js). True when the font factor or the
 *  bottom edge changed, so the rows have to be styled and laid out again. */
function applyPlan() {
  const u = unit(), H = innerHeight, bottomPx = parseFloat(getComputedStyle(root).bottom);
  const c = H - (Number.isFinite(bottomPx) ? bottomPx : 240 * u);                // the centre line's y (it is hidden, so it cannot be measured)
  let pillsTop = H;
  for (const el of document.querySelectorAll("#dock .pill, #dock .text-bar")) { const r = el.getBoundingClientRect(); if (r.height) pillsTop = Math.min(pillsTop, r.top); }
  const p = plan(S, { visTop: (H / 2 + (VIS_BOTTOM[activeSkin()] || VIS_BOTTOM.jarvis) * u - c) / u, maxBottom: (pillsTop - PILL_PAD * u - c) / u });
  const next = S.size * p.fit;
  if (next === eff && p.bottom === bottom && p.before === shown.before && p.after === shown.after) return false;
  eff = next; bottom = p.bottom; shown = { before: p.before, after: p.after }; wrapCache.clear();
  return true;
}
/** Everything that depends on the geometry or the settings, redone: the plan, the rows' sizes, the line breaks, the layout. */
function relayoutAll() {
  applyPlan(); cancelAnim(); buildRows(); refreshTexts();
}
function refreshVisibility() {
  const show = enabled && playing && lines.length > 0 && same(lyricsTrack, currentTrack);
  const was = root.hidden;
  root.hidden = !show;
  if (was && show) relayoutAll();                       // the gap may have changed while it was hidden (a window, a zoom)
}

/** Stops a revolver in progress (or ends it) and shows the resting rows again. */
function cancelAnim() {
  epoch++; animating = false;
  root.querySelectorAll(".ly-temp").forEach((d) => d.remove());
  restRows().forEach((r) => (r.style.visibility = ""));
}

function promote(target) {
  const oi = index, A = shown.after, B = shown.before, oldNext = text(oi + 1);
  index = target;
  if (!oldNext) { refreshTexts(); return; }
  const mine = ++epoch;
  animating = true;
  restRows().forEach((r) => (r.style.visibility = "hidden"));
  const mk = (t) => { const d = row("ly-temp"); d.textContent = wrap(t).join("\n"); root.append(d); return { el: d, n: count(t) }; };
  const up = mk(oldNext), down = mk(text(oi));
  // the column above the rising line, nearest first: the old next rows that shift one place, then the brand-new line (the last, it grows in)
  // each row also carries the distance it moves from and to: the font size follows it (a row one place closer grows a little, one place further shrinks)
  const above = range(A).map((k) => ({ ...mk(text(oi + 2 + k)), entry: k === A - 1, from: k + 2, to: k + 1 }));
  // the column below the sinking line, nearest first: the old previous rows, which shift one place; the oldest one leaves
  const below = range(B).map((k) => ({ ...mk(text(oi - 1 - k)), leaving: k === B - 1, from: k + 1, to: k + 2 }));
  const M0 = metricsAt(oi), M1 = metricsAt(oi + 1), u = M0.u, t0 = performance.now();
  const sz = (d) => rowPx(d) * eff, px = (f) => `calc(${f} * var(--u))`;
  const step = (now) => {
    if (mine !== epoch) return;
    const lin = Math.min(1, (now - t0) / DUR), e = ease(lin);
    const fUp = lerp(sz(1), sz(0), e), fDown = lerp(sz(0), sz(1), e);
    // The moving lines are one column, top to bottom: the new line, the shifting next lines, the line rising into the centre, the line sinking
    // into the previous row, the shifting previous lines. The column is anchored by the sinking line's top (it travels from the centre to the
    // first previous row), and the others stack above and below it from their current heights, so they can never overlap whatever their line
    // counts, and the first and last frames are exactly the resting layouts. Each keeps its line breaks (no re-wrapping while it resizes).
    const downTop = lerp(M0.cur.top, M1.prev0, e), upTop = downTop - gapAt(1, eff) * u - height(up.n, fUp, u);
    Object.assign(up.el.style, { top: upTop + "px", color: skinCss(CUR), fontSize: px(fUp), fontWeight: lerp(DIM_W, CUR_W, e), opacity: A ? 1 : e });
    Object.assign(down.el.style, { top: downTop + "px", color: skinCss(DIM), fontSize: px(fDown), fontWeight: lerp(CUR_W, DIM_W, e), opacity: B ? 1 : 1 - e });
    let top = upTop;
    for (const it of above) {
      const f = it.entry ? lerp(sz(it.to) * 0.6, sz(it.to), e) : lerp(sz(it.from), sz(it.to), e);
      top -= lerp(gapAt(it.from, eff), gapAt(it.to, eff), e) * u + height(it.n, f, u);
      Object.assign(it.el.style, { top: top + (it.entry ? ENTRY_OFFSET * u * (1 - e) : 0) + "px", color: skinCss(DIM), fontSize: px(f), fontWeight: DIM_W, opacity: it.entry ? e * opacityAt(it.to) : lerp(opacityAt(it.from), opacityAt(it.to), e) });
    }
    top = downTop + height(down.n, fDown, u);
    for (const it of below) {
      const f = lerp(sz(it.from), sz(it.to), e);
      top += lerp(gapAt(it.from, eff), gapAt(it.to, eff), e) * u;
      Object.assign(it.el.style, { top: top + "px", color: skinCss(DIM), fontSize: px(f), fontWeight: DIM_W, opacity: lerp(opacityAt(it.from), opacityAt(it.to), e) * (it.leaving ? 1 - e : 1) });
      top += height(it.n, f, u);
    }
    if (lin < 1) requestAnimationFrame(step);
    else { cancelAnim(); refreshTexts(); }
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
const relayout = () => { wrapCache.clear(); if (applyPlan()) relayoutAll(); else if (!animating) refreshTexts(); };
// the lines before / after or the font size changed (here, or in the Settings window): rebuild the rows and lay them out again
addEventListener("jarvis-lyrics-settings", () => { S = getLyricsSettings(); relayoutAll(); });
addEventListener("jarvis-unit", relayout); addEventListener("skin-changed", relayout);                // a zoom, or the dock wrapping or unwrapping: the pills moved
addEventListener("resize", relayout);
document.fonts?.ready.then(relayout);

export const lyrics = {
  get enabled() { return enabled; },
  setEnabled(on) {
    enabled = on; refreshVisibility();
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private mode */ }
  },
};
