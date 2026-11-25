// The lyrics settings (lines before / after, font size): kept in localStorage, which the dashboard and the floating Settings window share, so a
// change in one reaches the other through the "storage" event. The maths is in lyrics-model.js.
import { parseSettings } from "./lyrics-model.js";

const KEY = "jarvis.lyrics.v1";
const load = () => { try { return parseSettings(JSON.parse(localStorage.getItem(KEY))); } catch { return parseSettings(null); } };
let state = load();
const announce = () => window.dispatchEvent(new CustomEvent("jarvis-lyrics-settings"));

export const getLyricsSettings = () => ({ ...state });

/** Changes some of the settings ({before?, after?, size?}); what is out of range is clamped. */
export function setLyricsSettings(patch) {
  const next = parseSettings({ ...state, ...patch });
  if (next.before === state.before && next.after === state.after && next.size === state.size) return;
  state = next;
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private mode */ }
  announce();
}

addEventListener("storage", (e) => { if (e.key === KEY) { state = load(); announce(); } });
