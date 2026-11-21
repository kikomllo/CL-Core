// The page's only link to the system: a WebSocket to clWebUI.py, which relays MQTT. Same rule as every other
// service: topics in, topics out (or an actions.json action), no other coupling.
const handlers = new Map();      // topic -> Set<fn(payload)>
const stateListeners = new Set();
let ws = null, open = false, delay = 500;
const wanted = new Set();

function parse(text) {
  if (text === "") return {};
  try { return JSON.parse(text); } catch { return text; }
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    open = true; delay = 500;
    if (wanted.size) ws.send(JSON.stringify({ sub: [...wanted] }));
    outbox.splice(0).forEach(o => ws.send(JSON.stringify(o)));      // requests made before the socket was up (a window opened at start)
    stateListeners.forEach(f => f(true));
  };
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    const set = handlers.get(m.t);
    if (set) { const p = parse(m.p); set.forEach(fn => { try { fn(p, m.t); } catch (e) { console.error(m.t, e); } }); }
    for (const [flt, fns] of wildcards) if (match(flt, m.t)) { const p = parse(m.p); fns.forEach(fn => fn(p, m.t)); }
  };
  ws.onclose = () => {
    open = false; stateListeners.forEach(f => f(false));
    setTimeout(connect, delay); delay = Math.min(delay * 2, 8000);
  };
  ws.onerror = () => ws.close();
}

const wildcards = new Map();
function match(flt, topic) {
  const a = flt.split("/"), b = topic.split("/");
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "#") return true;
    if (a[i] !== "+" && a[i] !== b[i]) return false;
  }
  return a.length === b.length;
}

const outbox = [];
function send(obj) { if (open) ws.send(JSON.stringify(obj)); }
function queue(obj) { if (open) send(obj); else if (outbox.length < 50) outbox.push(obj); }

export const bridge = {
  /** Subscribe: fn(payload, topic) for every message (the bridge replays the last one it knows). */
  on(topic, fn) {
    const store = (topic.includes("#") || topic.includes("+")) ? wildcards : handlers;
    if (!store.has(topic)) store.set(topic, new Set());
    store.get(topic).add(fn);
    if (!wanted.has(topic)) { wanted.add(topic); send({ sub: [topic] }); }
    return () => store.get(topic).delete(fn);
  },
  /** Dispatch an actions.json action, e.g. act("spotify.control", {action: "toggle"}). */
  act(action, kwargs = {}) { queue({ act: action, k: kwargs }); },
  pub(topic, payload, retain = false) { queue({ pub: topic, p: payload, retain }); },
  onConnection(fn) { stateListeners.add(fn); fn(open); },
};
connect();
