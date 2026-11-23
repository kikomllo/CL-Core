// The calendar drawer on the right edge, "U3" design (Calendar Drawer Designs artifact) with the Qt drawer's behaviour:
// month / week / day / year views, the selected day's sheet with its events and the next busy days, and the new/edit/delete
// dialog. Data: jarvis/sys/calendar/status {events:[...]} (asked for with calendar.read); a new or edited event is published to
// jarvis/sys/calendar/create and a delete is calendar.delete, exactly what the Qt dialog sends. Below the agenda sits the Qt
// drawer's carousel (a round icon dock over To-do / Reminders / Media / Lights, each the same widget as its window, built the first
// time it is picked) and the now-playing pill.
import { bridge } from "./bridge.js";
import { h, icon } from "./ui.js";
import * as M from "./calendar-model.js";
import { todoWindow } from "./todo.js";
import { remindersWindow } from "./reminders.js";
import { mediaWindow } from "./media.js";
import { lightsWindow } from "./lights.js";

const PAGES = [{ id: "todo", name: "To-do", ic: "todos.svg", def: todoWindow }, { id: "reminders", name: "Reminders", ic: "reminders.svg", def: remindersWindow },
  { id: "media", name: "Media", ic: "music.svg", def: mediaWindow }, { id: "lights", name: "Lights", ic: "lights.svg", def: lightsWindow }];

const MODES = [["day", "DAY"], ["week", "WEEK"], ["month", "MONTH"]];

export function startCalendar() {
  const st = { open: false, mode: "month", date: M.startOfDay(new Date()), events: [], byDay: new Map(), dialog: null };
  const el = {};

  // ---- header: title (click = year picker), prev / next, Today ----
  el.main = h("span", { class: "cal-title-main" }); el.sub = h("span", { class: "cal-title-sub" });
  el.titleBtn = h("button", { class: "cal-title", title: "Pick a month", onclick: () => { st.mode = st.mode === "year" ? "month" : "year"; render(); } }, el.main, el.sub);
  const nav = (dir, label, ic) => h("button", { class: "round xs cal-nav", "aria-label": label, onclick: () => { st.date = M.step(st.date, st.mode, dir); render(); } }, icon(ic, { size: 12 }));
  el.head = h("div", { class: "cal-head" }, el.titleBtn,
    h("div", { class: "cal-actions" }, nav(-1, "Previous", "chevron_left.svg"), nav(1, "Next", "chevron_right.svg"),
      h("button", { class: "cal-today", onclick: () => { st.date = M.startOfDay(new Date()); if (st.mode === "year") st.mode = "month"; render(); } }, "Today")));

  // ---- DAY / WEEK / MONTH pill ----
  el.pill = h("div", { class: "cal-pill", role: "tablist" }, MODES.map(([id, name]) =>
    h("button", { role: "tab", "data-mode": id, onclick: () => { st.mode = id; render(); } }, name)));

  el.dows = h("div", { class: "cal-dows" }, M.DOW_LETTERS.map((l) => h("div", {}, l)));
  el.grid = h("div", { class: "cal-grid" });
  el.calendar = h("div", { class: "cal-calendar" }, el.dows, el.grid);

  // ---- the sheet: the selected day ----
  el.dayTitle = h("span", { class: "cal-day-title" }); el.dayCount = h("span", { class: "cal-day-count" });
  el.add = h("button", { class: "round add", "aria-label": "Add event", title: "Add event", onclick: () => openDialog(null) }, icon("add.svg", { size: 16 }));
  el.events = h("div", { class: "cal-events" });
  el.next = h("div", { class: "cal-next" });
  // ---- the carousel: round icon dock over the widgets, then the now-playing pill ----
  const pages = new Map();                                      // id -> {btn, host, built}
  el.split = h("div", { class: "cal-split", role: "separator", "aria-orientation": "horizontal", tabindex: 0, "aria-label": "Resize the widget area",
    title: "Drag to resize, double-click to reset" }, h("i"));
  el.dockRow = h("div", { class: "cal-dock", role: "tablist" });
  el.stack = h("div", { class: "cal-stack" });
  for (const pg of PAGES) {
    const badge = h("span", { class: "cal-badge", hidden: true });
    const btn = h("button", { class: "cal-dock-btn", role: "tab", "aria-label": pg.name, title: pg.name, onclick: () => showPage(pg.id) }, icon(pg.ic, { size: 20 }), badge);
    const host = h("div", { class: "cal-page", hidden: true });
    el.dockRow.append(btn); el.stack.append(host);
    pages.set(pg.id, { btn, host, built: false, badge, def: pg.def });
  }
  el.dockRow.append(h("span", { class: "cal-dock-name" }));
  // The widget area's height (design px) is the user's: drag the bar above the dock, arrows nudge it, double-click resets. The
  // agenda above takes whatever is left (never under MIN_EVENTS), and the stack's flex-shrink keeps a saved height that is too tall
  // for a smaller window inside it.
  const HEIGHT_KEY = "jarvis.calendar.stackH", DEFAULT_H = 240, MIN_H = 80, MIN_EVENTS = 84;
  const unitPx = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;
  const setStack = (v, max = Infinity) => el.stack.style.setProperty("--stack-h", String(Math.round(Math.min(max, Math.max(MIN_H, v)))));
  const saveStack = () => { try { localStorage.setItem(HEIGHT_KEY, el.stack.style.getPropertyValue("--stack-h")); } catch { /* private mode */ } };
  try { const saved = parseFloat(localStorage.getItem(HEIGHT_KEY)); setStack(Number.isFinite(saved) ? saved : DEFAULT_H); } catch { setStack(DEFAULT_H); }
  // the most the stack can take now: what it shows plus what the agenda can give up
  const room = () => (el.stack.getBoundingClientRect().height + el.events.getBoundingClientRect().height - MIN_EVENTS * unitPx()) / unitPx();
  el.split.addEventListener("pointerdown", (e) => {
    e.preventDefault(); try { el.split.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ } el.split.classList.add("drag");
    const u = unitPx(), y0 = e.clientY, h0 = el.stack.getBoundingClientRect().height / u, max = room();
    const move = (ev) => setStack(h0 + (y0 - ev.clientY) / u, max);          // dragging up makes the area taller
    const up = () => { el.split.classList.remove("drag"); el.split.removeEventListener("pointermove", move); el.split.removeEventListener("pointerup", up); saveStack(); };
    el.split.addEventListener("pointermove", move); el.split.addEventListener("pointerup", up);
  });
  el.split.addEventListener("dblclick", () => { setStack(DEFAULT_H); saveStack(); });
  el.split.addEventListener("keydown", (e) => {
    const d = { ArrowUp: 24, ArrowDown: -24 }[e.key];
    if (d === undefined) return;
    e.preventDefault(); setStack(el.stack.getBoundingClientRect().height / unitPx() + d, room()); saveStack();
  });
  function showPage(id) {
    for (const [pid, pg] of pages) {
      const on = pid === id;
      pg.btn.setAttribute("aria-pressed", String(on)); pg.host.hidden = !on;
      if (on && !pg.built) { pg.built = true; pg.def.build(pg.host); }       // the widget's own window body, built on first use
    }
    el.dockRow.lastChild.textContent = PAGES.find((p) => p.id === id).name.toUpperCase();
  }
  bridge.on("jarvis/sys/todo/status", (d) => {
    const open = d && Array.isArray(d.todos) ? d.todos.filter((t) => !t.completed).length : 0, badge = pages.get("todo").badge;
    badge.textContent = open > 99 ? "99+" : String(open); badge.hidden = open === 0;
  });

  const ctl = (action) => bridge.act("spotify.control", { silent: true, action });
  el.mTitle = h("div", { class: "cal-m-title" }, "Nothing playing"); el.mArtist = h("div", { class: "cal-m-artist" });
  el.mPlay = h("button", { class: "round lg", "aria-label": "Play or pause", onclick: () => { playing = !playing; el.mPlay.firstChild.setIcon(playing ? "pause.svg" : "play.svg"); ctl("toggle"); } }, icon("play.svg", { size: 16 }));
  let playing = false;
  el.mpill = h("div", { class: "cal-mpill" }, h("div", { class: "cal-cover" }), h("div", { class: "cal-m-info" }, el.mTitle, el.mArtist),
    h("button", { class: "round sm ghost", "aria-label": "Previous track", onclick: () => ctl("prev") }, icon("skip_back.svg", { size: 14 })), el.mPlay);
  bridge.on("jarvis/sys/media_status", (d) => {
    if (!d || typeof d !== "object") return;
    el.mTitle.textContent = d.title || "Nothing playing"; el.mArtist.textContent = d.artist || "";
    playing = d.status === "Playing"; el.mPlay.firstChild.setIcon(playing ? "pause.svg" : "play.svg");
  });

  // The sheet's grab handle is a splitter between the calendar and the sheet: dragging it down makes the calendar's day cells taller
  // (the calendar grows, the sheet gives up space), dragging it up shrinks them. The shape is the cell's height / width (1 = square,
  // the U3 look), saved in localStorage; double-click resets, the arrow keys nudge it.
  const CELL_KEY = "jarvis.calendar.cellK", MIN_K = 0.5, MAX_K = 2.2;
  const setK = (k) => el.drawer.style.setProperty("--cell-k", String(Math.round(Math.min(MAX_K, Math.max(MIN_K, k)) * 100) / 100));
  const saveK = () => { try { localStorage.setItem(CELL_KEY, el.drawer.style.getPropertyValue("--cell-k")); } catch { /* private mode */ } };
  try { const k = parseFloat(localStorage.getItem(CELL_KEY)); if (Number.isFinite(k)) setK(k); } catch { /* private mode */ }
  el.grab = h("div", { class: "cal-grab", role: "separator", "aria-orientation": "horizontal", tabindex: 0, "aria-label": "Resize the calendar",
    title: "Drag to resize the calendar, double-click to reset" }, h("div", { class: "cal-handle" }));
  const cellBox = () => { const c = el.grid.querySelector(".cal-cell"); return c ? c.getBoundingClientRect() : null; };
  const rows = () => Math.max(1, Math.round(el.grid.querySelectorAll(".cal-cell, .cal-blank").length / 7));
  // how many px the sheet can give the calendar: what its agenda and widget area can shrink by
  const giveable = () => (el.events.getBoundingClientRect().height - MIN_EVENTS * unitPx()) + (el.stack.getBoundingClientRect().height - MIN_H * unitPx());
  function growBy(dy) {                                                      // dy px of total grid height, positive = taller
    const c = cellBox(); if (!c) return;
    const n = rows(), max = Math.max(0, giveable());
    setK((c.height + Math.min(dy, max) / n) / c.width);
  }
  el.grab.addEventListener("pointerdown", (e) => {
    const c0 = cellBox(); if (!c0) return;
    e.preventDefault(); try { el.grab.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer */ } el.grab.classList.add("drag");
    const y0 = e.clientY, n = rows(), h0 = c0.height, w = c0.width, max = Math.max(0, giveable());
    const move = (ev) => setK((h0 + Math.min(ev.clientY - y0, max) / n) / w);
    const up = () => { el.grab.classList.remove("drag"); el.grab.removeEventListener("pointermove", move); el.grab.removeEventListener("pointerup", up); saveK(); };
    el.grab.addEventListener("pointermove", move); el.grab.addEventListener("pointerup", up);
  });
  el.grab.addEventListener("dblclick", () => { setK(1); saveK(); });
  el.grab.addEventListener("keydown", (e) => {
    const d = { ArrowDown: 24, ArrowUp: -24 }[e.key];
    if (d === undefined) return;
    e.preventDefault(); growBy(d); saveK();
  });
  el.sheet = h("div", { class: "cal-sheet" }, el.grab,
    h("div", { class: "cal-day-head" }, h("div", { class: "cal-day-line" }, el.dayTitle, el.dayCount), el.add), el.events, el.next,
    el.split, el.dockRow, el.stack, el.mpill);

  el.dialog = h("div", { class: "cal-dialog-wrap", hidden: true });
  el.drawer = h("aside", { class: "cal-drawer", "aria-label": "Calendar", inert: true }, el.head, el.pill, el.calendar, el.sheet, el.dialog);
  el.tab = h("button", { class: "cal-tab", "aria-label": "Open calendar", title: "Calendar", onclick: () => toggle() }, icon("chevron_left.svg", { size: 14 }));
  document.body.append(el.drawer, el.tab);

  // ---- rendering ----
  function render() {
    const t = M.titleFor(st.mode, st.date);
    el.main.textContent = t.main; el.sub.textContent = t.sub;
    el.titleBtn.classList.toggle("small", st.mode === "week" || st.mode === "day");
    for (const b of el.pill.children) b.setAttribute("aria-pressed", String(b.dataset.mode === st.mode));
    el.calendar.hidden = st.mode === "day";
    el.dows.hidden = st.mode === "year";
    el.pill.classList.toggle("none", st.mode === "year");
    st.byDay = M.groupByDay(st.events);
    renderGrid(); renderSheet();
  }

  function renderGrid() {
    const today = new Date(), picked = st.date;
    if (st.mode === "year") {                                      // a month picker: the 12 months of the selected year
      el.grid.className = "cal-grid year";
      el.grid.replaceChildren(...M.MONTHS.map((name, i) => h("button", { class: "cal-month" + (i === picked.getMonth() ? " on" : ""),
        onclick: () => { st.date = new Date(picked.getFullYear(), i, Math.min(picked.getDate(), M.daysInMonth(picked.getFullYear(), i))); st.mode = "month"; render(); } }, name.slice(0, 3))));
      return;
    }
    el.grid.className = "cal-grid";
    const cells = st.mode === "week" ? M.weekCells(picked) : M.monthCells(picked.getFullYear(), picked.getMonth());
    el.grid.replaceChildren(...cells.map((d) => {
      if (!d) return h("div", { class: "cal-blank" });
      const evs = st.byDay.get(M.dayKey(d)) || [], sel = M.sameDay(d, picked), isToday = M.sameDay(d, today);
      const bars = evs.slice(0, 3).map((ev) => h("i", { class: M.eventSource(ev) === M.SOURCE_GOOGLE ? "hollow" : "" }));
      return h("button", { class: "cal-cell" + (sel ? " sel" : "") + (isToday ? " today" : ""), "aria-label": `${d.getDate()} ${M.MONTHS[d.getMonth()]} ${d.getFullYear()}, ${evs.length} events`,
        "aria-pressed": String(sel), onclick: () => { st.date = d; render(); } }, h("span", { class: "n" }, d.getDate()), h("span", { class: "bars" }, bars));
    }));
  }

  function renderSheet() {
    const day = st.date, list = st.byDay.get(M.dayKey(day)) || [];
    el.dayTitle.textContent = M.dayTitle(day); el.dayCount.textContent = M.countLabel(list.length);
    el.events.replaceChildren(...(list.length ? list.map(eventRow) : [h("div", { class: "cal-empty" }, "Nothing planned")]));
    el.next.replaceChildren(...M.nextBusyDays(st.events, day).map(({ date, count }) =>
      h("button", { class: "cal-next-row", onclick: () => { st.date = date; if (st.mode === "year") st.mode = "month"; render(); } },
        h("span", {}, `${M.DAYS[M.weekdayIndex(date)].slice(0, 3)} ${date.getDate()} · ${count} event${count === 1 ? "" : "s"}`), h("span", {}, "→"))));
  }

  function eventRow(ev) {
    const google = M.eventSource(ev) === M.SOURCE_GOOGLE, editable = M.isEditable(ev);
    return h(editable ? "button" : "div", { class: "cal-event" + (editable ? " editable" : ""), onclick: editable ? () => openDialog(ev) : null },
      h("span", { class: "cal-time" }, M.timeLabel(ev)), h("span", { class: "cal-dot" + (google ? " hollow" : "") }),
      h("span", { class: "cal-ev-title" }, ev.summary || ""), google ? h("span", { class: "cal-tag" }, "GOOGLE") : null);
  }

  // ---- new / edit / delete ----
  function openDialog(ev) {
    const start = ev ? M.eventStart(ev) : null, now = new Date();
    const title = h("input", { class: "mod-input", placeholder: "Event title", value: ev ? ev.summary || "" : "", "aria-label": "Event title", maxlength: 200 });
    const date = h("input", { class: "mod-input", type: "date", value: M.inputDate(start || st.date), "aria-label": "Date" });   // a new event lands on the selected day
    const time = h("input", { class: "mod-input", type: "time", value: M.inputTime(start || now), "aria-label": "Time" });
    const save = () => {
      const payload = M.createPayload(title.value, date.value, time.value, ev && ev.id);
      if (!payload) { title.focus(); return; }
      bridge.pub("jarvis/sys/calendar/create", payload);
      closeDialog();
    };
    const del = ev ? h("button", { class: "btn danger", onclick: () => { bridge.act("calendar.delete", { id: String(ev.id) }); closeDialog(); } }, "Delete") : null;
    title.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    el.dialog.replaceChildren(h("div", { class: "cal-dialog panel", role: "dialog", "aria-label": ev ? "Edit calendar event" : "New calendar event" },
      h("div", { class: "pk-title" }, ev ? "EDIT CALENDAR EVENT" : "NEW CALENDAR EVENT"), title,
      h("div", { class: "cal-dt" }, date, time),
      h("div", { class: "cal-dlg-btns" }, del, h("button", { class: "btn", onclick: closeDialog }, "Cancel"), h("button", { class: "btn cal-save", onclick: save }, "Save"))));
    el.dialog.hidden = false; st.dialog = true;
    title.focus();
  }
  function closeDialog() { el.dialog.hidden = true; el.dialog.replaceChildren(); st.dialog = false; }
  el.dialog.addEventListener("pointerdown", (e) => { if (e.target === el.dialog) closeDialog(); });
  addEventListener("keydown", (e) => { if (e.key === "Escape") { if (st.dialog) closeDialog(); else if (st.open) toggle(false); } });

  // ---- open / close, width ----
  function toggle(force) {
    st.open = force === undefined ? !st.open : force;
    el.drawer.classList.toggle("open", st.open); el.tab.classList.toggle("open", st.open);
    el.drawer.inert = !st.open;
    el.tab.setAttribute("aria-label", st.open ? "Close calendar" : "Open calendar");
    if (st.open) { bridge.act("calendar.read"); render(); }
  }

  bridge.on("jarvis/sys/calendar/status", (p) => { if (p && Array.isArray(p.events)) { st.events = p.events; render(); } });
  bridge.act("calendar.read");
  showPage("todo");
  render();
  return { toggle };
}
