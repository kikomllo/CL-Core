// Reminders: a half-arc countdown per upcoming reminder (the Qt widget's look), Delete under each.
// Data: jarvis/web/reminders {reminders:[{id,text,time,time_created}]}, published by clWebUI from data/reminders.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";

const R = 54, C = Math.PI * R;                          // half circle: arc length
const pad = (n) => String(n).padStart(2, "0");
function clock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const hh = Math.floor(sec / 3600), mm = Math.floor(sec / 60) % 60, ss = sec % 60;
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}

export const remindersWindow = {
  id: "widget_reminders", title: "Reminders", w: 380, h: 330,
  build(body) {
    let items = [];
    const grid = h("div", { class: "rem-grid" });
    const count = h("span", { class: "mod-count" }, "");
    body.append(h("div", { class: "mod" }, h("div", { class: "mod-head" }, h("div", { class: "mod-title" }, "Reminders", count)), grid));
    const cells = new Map();                             // id -> {arc, time}

    function progress(r, now) {
      const t = Date.parse(r.time), c = Date.parse(r.time_created || "") || t - 60000;
      return { left: (t - now) / 1000, p: t > c ? Math.max(0, Math.min(1, (now - c) / (t - c))) : 1 };
    }
    function tick() {
      const now = Date.now();
      for (const r of items) {
        const el = cells.get(r.id); if (!el) continue;
        const { left, p } = progress(r, now);
        el.arc.setAttribute("stroke-dasharray", `${p * C} ${C * 2}`);
        el.time.textContent = clock(left);
      }
    }
    function render() {
      const now = Date.now();
      items = items.filter(r => Date.parse(r.time) > now).sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
      count.textContent = items.length ? String(items.length) : "";
      cells.clear();
      grid.replaceChildren(...(items.length ? items.map(r => {
        const arc = document.createElementNS("http://www.w3.org/2000/svg", "path");
        const d = `M ${70 - R} 70 A ${R} ${R} 0 0 1 ${70 + R} 70`;
        arc.setAttribute("d", d); arc.setAttribute("class", "rem-arc");
        const track = document.createElementNS("http://www.w3.org/2000/svg", "path");
        track.setAttribute("d", d); track.setAttribute("class", "rem-track");
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 140 80"); svg.append(track, arc);
        const time = h("div", { class: "rem-time" });
        cells.set(r.id, { arc, time });
        return h("div", { class: "rem-cell" }, h("div", { class: "rem-dial" }, svg, time), r.text ? h("div", { class: "rem-text dim" }, r.text) : null,
          h("button", { class: "btn danger", onclick: () => { bridge.act("reminder.delete", { id: r.id }); items = items.filter(x => x !== r); render(); } }, "Delete"));
      }) : [h("div", { class: "dim mod-empty" }, "No active reminders")]));
      tick();
    }
    const off = bridge.on("jarvis/web/reminders", (d) => { if (d && Array.isArray(d.reminders)) { items = d.reminders; render(); } });
    const timer = setInterval(tick, 1000);
    render();
    return { onClose() { off(); clearInterval(timer); } };
  },
};
