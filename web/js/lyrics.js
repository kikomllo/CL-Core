// Karaoke lyrics between the orb and the command bar, "Lyrics Revolver" style: next (small), current (big), previous (small).
// Advancing by one line plays a 650 ms InOutCubic revolver: the next line grows and slides up into the current row, the current
// shrinks into the previous row, and a brand-new next line grows in from a small size. Port of clUI.LyricsDisplay + the artifact's two fixes
// (the previous row is hidden during the move; the new next line animates instead of popping in).
import { bridge } from "./bridge.js";

const DUR = 650, DIM_PX = 14, CUR_PX = 26, DIM_W = 400, CUR_W = 800, ENTRY_PX = 8, ENTRY_OFFSET = 12;
const CUR = "rgba(255,170,0,.9)", DIM = "rgba(255,170,0,.588)";
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const KEY = "jarvis.lyrics.enabled";

const root = document.getElementById("lyrics");
const row = (cls) => Object.assign(document.createElement("div"), { className: "ly-row " + cls });
const rNext = row("ly-next"), rCur = row("ly-cur"), rPrev = row("ly-prev");
root.append(rNext, rCur, rPrev);

let lines = [], index = -1, animating = false;
let lyricsTrack = null, currentTrack = null, playing = false, pos = 0, capturedAt = 0;
let enabled = true;
try { enabled = localStorage.getItem(KEY) !== "0"; } catch { /* private mode */ }

const text = (i) => (i >= 0 && i < lines.length ? lines[i].text : "");
const position = () => (playing ? pos + (performance.now() - capturedAt) / 1000 : pos);
const indexFor = (p) => { let r = -1; for (let i = 0; i < lines.length; i++) { if (lines[i].time <= p) r = i; else break; } return r; };
const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];

function refreshTexts() { rCur.textContent = text(index); rNext.textContent = text(index + 1); rPrev.textContent = text(index - 1); }
function refreshVisibility() {
  const show = enabled && playing && lines.length > 0 && same(lyricsTrack, currentTrack);
  root.hidden = !show;
}

function promote(target) {
  const oldCur = text(index), oldNext = text(index + 1);
  index = target;
  const newNext = text(index + 1);
  if (!oldNext) { refreshTexts(); return; }
  animating = true;
  [rCur, rNext, rPrev].forEach(r => (r.style.visibility = "hidden"));
  const mk = (t) => { const d = row("ly-temp"); d.textContent = t; root.append(d); return d; };
  const up = mk(oldNext), down = mk(oldCur), entry = newNext ? mk(newNext) : null;
  const rowH = root.clientHeight / 3, t0 = performance.now();
  const step = (now) => {
    const lin = Math.min(1, (now - t0) / DUR), e = ease(lin);
    Object.assign(up.style, { top: rowH * e + "px", height: rowH + "px", color: CUR, fontSize: `calc(${DIM_PX + (CUR_PX - DIM_PX) * e} * var(--u))`, fontWeight: DIM_W + (CUR_W - DIM_W) * e });
    Object.assign(down.style, { top: rowH + rowH * e + "px", height: rowH + "px", color: DIM, fontSize: `calc(${CUR_PX - (CUR_PX - DIM_PX) * e} * var(--u))`, fontWeight: CUR_W - (CUR_W - DIM_W) * e });
    if (entry) Object.assign(entry.style, { top: ENTRY_OFFSET * (1 - e) + "px", height: rowH + "px", color: DIM, fontSize: `calc(${ENTRY_PX + (DIM_PX - ENTRY_PX) * e} * var(--u))`, fontWeight: DIM_W });
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

export const lyrics = {
  get enabled() { return enabled; },
  setEnabled(on) {
    enabled = on; refreshVisibility();
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private mode */ }
  },
};
