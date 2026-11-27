// What the dashboard puts back after the page is reloaded, the shell restarts, or the mode changes (tests/test_webSession.py runs this under
// node). Every window that was open comes back at every start (a reload, a restart of the UI, a crash recovery, a cold start of the ecosystem).

/** A saved list of window ids, whatever was stored: only non-empty strings, no repeats. */
export function parseIds(raw) {
  let v = raw;
  if (typeof raw === "string") { try { v = JSON.parse(raw); } catch { return []; } }
  return Array.isArray(v) ? [...new Set(v.filter((x) => typeof x === "string" && x))] : [];
}

/** The window ids to open at start, in order. `open` = ids open when the page last changed; `restoreAll` = the session continues. */
export function windowsToRestore({ pinned = [], open = [], restoreAll = false }) {
  return [...new Set([...(restoreAll ? parseIds(open) : []), ...parseIds(pinned)])];
}

/** One remembered value of a window (its selected tab, its open drawer) out of the saved state object, or `fallback` if it is missing
 *  or not one of the `allowed` values (a tab that no longer exists must not leave the window blank). */
export function savedChoice(state, key, allowed, fallback) {
  const v = state && typeof state === "object" ? state[key] : undefined;
  return v !== undefined && (!allowed || allowed.includes(v)) ? v : fallback;
}
