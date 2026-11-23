// Calendar logic with no DOM: event accessors (a port of utils/clCalendarEvents.py), month/week grids (Monday first, like the
// Qt CalendarWidget), stepping, the "next busy days" list and the payload for a new/edited event. Tested with node by
// tests/test_webCalendarModel.py.
export const SOURCE_LOCAL = "local", SOURCE_GOOGLE = "google";
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const DOW_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
const pad = (n) => String(n).padStart(2, "0");

/** `{dateTime}` or `{date}` -> a local Date, or null. A bare date must be built from its parts: `new Date("2026-11-22")` is UTC midnight. */
export function whenToDate(when) {
  if (!when || typeof when !== "object") return null;
  if (when.dateTime) { const d = new Date(when.dateTime); return isNaN(d) ? null : d; }
  if (when.date) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(when.date);
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }
  return null;
}
export const eventStart = (ev) => whenToDate(ev && ev.start);
export const isAllDay = (ev) => !!(ev && ev.start && !ev.start.dateTime && ev.start.date);
export const eventSource = (ev) => (ev && ev.source) || SOURCE_LOCAL;
export const isEditable = (ev) => eventSource(ev) === SOURCE_LOCAL && !ev.readOnly;
export const sortKey = (ev) => { const d = eventStart(ev); return d ? d.getTime() : Infinity; };
export const timeLabel = (ev) => {
  const d = eventStart(ev);
  return !d ? "" : isAllDay(ev) ? "All day" : `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const sameDay = (a, b) => dayKey(a) === dayKey(b);
export const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
export const daysInMonth = (y, m) => new Date(y, m + 1, 0).getDate();
/** 0 = Monday ... 6 = Sunday */
export const weekdayIndex = (d) => (d.getDay() + 6) % 7;
export const startOfWeek = (d) => addDays(startOfDay(d), -weekdayIndex(d));

/** Events with a readable start, grouped by day (Map of dayKey -> events sorted by time). */
export function groupByDay(events) {
  const map = new Map();
  for (const ev of events || []) {
    const d = eventStart(ev);
    if (!d) continue;
    const k = dayKey(d);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(ev);
  }
  for (const list of map.values()) list.sort((a, b) => sortKey(a) - sortKey(b));
  return map;
}

/** The month grid as whole weeks: null for the blanks before the 1st and after the last day (4-6 rows). */
export function monthCells(year, month) {
  const lead = weekdayIndex(new Date(year, month, 1)), n = daysInMonth(year, month);
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let day = 1; day <= n; day++) cells.push(new Date(year, month, day));
  while (cells.length % 7) cells.push(null);
  return cells;
}
export const weekCells = (d) => { const s = startOfWeek(d); return Array.from({ length: 7 }, (_, i) => addDays(s, i)); };

/** Step the selected day by one unit of the mode; month and year steps clamp the day (31 Jan + 1 month = 28/29 Feb). */
export function step(date, mode, dir) {
  if (mode === "day") return addDays(date, dir);
  if (mode === "week") return addDays(date, 7 * dir);
  const months = mode === "year" ? 12 * dir : dir;
  const target = new Date(date.getFullYear(), date.getMonth() + months, 1);
  return new Date(target.getFullYear(), target.getMonth(), Math.min(date.getDate(), daysInMonth(target.getFullYear(), target.getMonth())));
}

/** The header title: `main` is big, `sub` the small year beside it. Week and day are long, so callers shrink them. */
export function titleFor(mode, date) {
  if (mode === "year") return { main: String(date.getFullYear()), sub: "" };
  if (mode === "day") return { main: DAYS[weekdayIndex(date)], sub: "" };
  if (mode === "week") {
    const w = weekCells(date), a = w[0], b = w[6], mon = (d) => MONTHS[d.getMonth()].slice(0, 3);
    return { main: a.getMonth() === b.getMonth() ? `${mon(a)} ${a.getDate()}–${b.getDate()}` : `${mon(a)} ${a.getDate()} – ${mon(b)} ${b.getDate()}`, sub: String(b.getFullYear()) };
  }
  return { main: MONTHS[date.getMonth()], sub: String(date.getFullYear()) };
}

export const dayTitle = (d) => `${DAYS[weekdayIndex(d)].slice(0, 3)} ${d.getDate()}`;
export const countLabel = (n) => (n === 0 ? "NO EVENTS" : `${n} EVENT${n === 1 ? "" : "S"}`);

/** The next `limit` days after `day` that have events: [{date, count}]. */
export function nextBusyDays(events, day, limit = 2) {
  const after = new Map();
  for (const ev of events || []) {
    const d = eventStart(ev);
    if (!d) continue;
    const start = startOfDay(d);
    if (start > startOfDay(day)) after.set(start.getTime(), (after.get(start.getTime()) || 0) + 1);
  }
  return [...after.keys()].sort((a, b) => a - b).slice(0, limit).map((t) => ({ date: new Date(t), count: after.get(t) }));
}

/** `jarvis/sys/calendar/create` payload, as the Qt dialog sends it ("yyyy-MM-dd HH:mm"); an edit carries the event's id. */
export function createPayload(title, dateStr, timeStr, id) {
  const event = (title || "").trim();
  if (!event || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr || "") || !/^\d{2}:\d{2}$/.test(timeStr || "")) return null;
  const payload = { event, time_str: `${dateStr} ${timeStr}` };
  if (id) payload.id = id;
  return payload;
}
export const inputDate = (d) => dayKey(d);
export const inputTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
