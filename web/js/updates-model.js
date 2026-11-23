// The Updates window's pure logic, as the Qt UpdateWidget words it (tests/test_webUpdates.py runs it under node). The updater service
// (src/clUpdater.py) publishes jarvis/sys/updates/status: {status: "checking" | "updating" | "ready", details: [{type, name, id}]} and,
// while it installs, {action: "log", text}. The service only installs ONE named update at a time (it refuses bulk actions on purpose),
// so there is no "update all" here either.

/** "[APP] Mozilla Firefox" for one update, with the fallbacks the Qt widget uses. */
export const itemLabel = (u) => `[${String((u && u.type) || "Unknown").toUpperCase()}] ${(u && u.name) || "Unknown"}`;

/** Only well-formed updates (an object with a text id) can be installed; anything else in a payload is dropped. */
export const cleanItems = (details) => (Array.isArray(details) ? details.filter((u) => u && typeof u === "object" && typeof u.id === "string" && u.id) : []);

/** What a status message means for the window: {kind: "log", text} to append a line, or {kind: "view", label, busy, mode, items, clearLog}
 *  (mode "list" shows the updates, "log" shows the install output); null for a message it does not know. */
export function interpret(msg) {
  if (!msg || typeof msg !== "object") return null;
  if (msg.action === "log") return { kind: "log", text: String(msg.text ?? "") };
  if (msg.status === "checking") return { kind: "view", label: "Checking for updates... Please wait.", busy: true, mode: "list", items: [], clearLog: false };
  if (msg.status === "updating") return { kind: "view", label: "Installing updates... Please wait.", busy: true, mode: "log", items: [], clearLog: true };
  if (msg.status === "ready") {
    const items = cleanItems(msg.details);
    return { kind: "view", label: `Found ${items.length} update${items.length === 1 ? "" : "s"}.`, busy: false, mode: "list", items, clearLog: false };
  }
  return null;
}
