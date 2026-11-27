// The startup cover's rules, apart from the page (tests/test_webBoot.py runs this under node). The supervisor publishes where the boot is
// (jarvis/sys/boot_state, retained: { ready, total, online, started, modules: [{ name, ready, at }] }); the cover is drawn from it.

export const HOLD_MS = 2000;                // "System online" stays up this long (the spoken announcement) before the windows come in
export const DEFAULT_TIMEOUT_MS = 60000;    // a module that never reports in must not lock the dashboard away
export const SLOW_MS = 25000;               // past this a module that is still loading is named as the one being waited for

/** The cover's phase, given the previous one, the latest boot state (null while none has arrived) and the time.
 *  idle -> loading (a boot is under way) -> online (everything is in: held for the announcement) -> done. A page that opens when the system is
 *  already online never shows the cover; a new boot (another `started`) covers again; a boot that stalls lifts after `timeoutMs`. The same call, with only the clock moved, decides the timeout and the hold. */
export function nextPhase(prev, state, now, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const p = prev || { phase: "idle", since: 0, onlineAt: 0, started: null };
  if (!state || typeof state.started !== "number") return p;          // nothing yet, or a cleared / foreign message
  const next = { ...p };
  if (state.started !== p.started) {                                   // the first state this page sees, or a new boot
    next.started = state.started;
    if (!state.online) return { phase: "loading", since: now, onlineAt: 0, started: state.started };
    return { phase: "done", since: now, onlineAt: 0, started: state.started };
  }
  if (p.phase === "loading") {
    if (state.online) { next.phase = "online"; next.onlineAt = now; }
    else if (now - p.since >= timeoutMs) next.phase = "done";
  } else if (p.phase === "online" && now - p.onlineAt >= HOLD_MS) next.phase = "done";
  return next;
}

/** Whether the cover is showing for this phase. */
export const covering = (phase) => phase === "loading" || phase === "online";

/** What the cover says: the title, the count, the line under it, the progress (0-1) and one row per module. */
export function describe(state, phase, sinceMs) {
  const rows = (state && state.modules ? state.modules : []).map((m) => {
    const stuck = !m.ready && phase === "loading" && sinceMs >= SLOW_MS;
    return { name: m.name, state: m.ready ? "done" : stuck ? "stuck" : "wait", time: m.ready && m.at != null ? `${Number(m.at).toFixed(1)} s` : stuck ? "slow" : "loading" };
  });
  const total = state ? state.total : 0, ready = state ? state.ready : 0;
  const waiting = rows.filter((r) => r.state !== "done").map((r) => r.name);
  const online = phase === "online";
  let sub = "Loading speech, voice and lights.", warn = false;
  if (online) sub = "Everything reported in.";
  else if (waiting.length && sinceMs >= SLOW_MS) { sub = `Waiting on ${waiting.slice(0, 3).join(", ")}${waiting.length > 3 ? " and more" : ""}.`; warn = true; }
  return {
    title: online ? "System online" : "Starting JARVIS",
    count: `${String(ready).padStart(2, "0")} of ${total} modules`,
    progress: total ? ready / total : 0,
    sub, warn, rows,
  };
}
