// System Updates: check for updates and install them one at a time (the Qt UpdateWidget). Data: jarvis/sys/updates/status from
// clUpdater.py; actions updates.check and updates.individual {id}. The service keeps no "last result" for a window that opens later and
// does not check by itself, so the window starts at "Ready" and the Check button asks. Installing software changes the machine, so an
// install asks for a second click on that one item. There is no "update all": the service refuses bulk updates on purpose.
import { bridge } from "./bridge.js";
import { h } from "./ui.js";
import { interpret, itemLabel } from "./updates-model.js";

const MAX_LOG_LINES = 2000;

export const updatesWindow = {
  id: "widget_updates", title: "System Updates", w: 480, h: 420,
  build(body) {
    let busy = false, armed = null, armTimer = null;
    const status = h("div", { class: "dim up-status", role: "status", "aria-live": "polite" }, "Ready");
    const bar = h("div", { class: "up-bar", hidden: true, role: "progressbar", "aria-label": "Working" }, h("i"));
    const list = h("div", { class: "mod-list up-list" });
    const log = h("div", { class: "t-screen up-log", hidden: true, role: "log", tabindex: 0 });
    const check = h("button", { class: "btn mod-wide", onclick: () => { setView({ label: "Checking for updates...", busy: true, mode: "list", items: [] }); bridge.act("updates.check"); } }, "Check for Updates");
    body.append(h("div", { class: "mod" }, h("div", { class: "mod-head" }, h("div", { class: "mod-title" }, "Updates")), status, bar, list, log, check));

    function row(u) {
      const install = h("button", { class: "btn up-install", onclick: () => {
        if (armed !== u.id) {                                           // the first click arms this item, the second installs it
          armed = u.id; render(items); clearTimeout(armTimer); armTimer = setTimeout(() => { armed = null; render(items); }, 3500); return;
        }
        armed = null; clearTimeout(armTimer);
        setView({ label: `Installing ${u.name || u.id}...`, busy: true, mode: "log", items: [], clearLog: true });
        bridge.act("updates.individual", { id: u.id });
      } }, armed === u.id ? "Click again to install" : "Install");
      return h("div", { class: "mod-row up-row" }, h("div", { class: "up-name", title: u.id }, itemLabel(u)), install);
    }
    let items = [];
    function render(next) {
      items = next;
      list.replaceChildren(...(items.length ? items.map(row) : [h("div", { class: "dim mod-empty" }, "No updates available.")]));
    }
    function setView({ label, busy: isBusy, mode, items: next, clearLog }) {
      status.textContent = label; busy = isBusy;
      bar.hidden = !isBusy; check.disabled = isBusy;
      list.hidden = mode === "log"; log.hidden = mode !== "log";
      if (clearLog) log.replaceChildren();
      if (mode === "list") { armed = null; if (isBusy) { items = []; list.replaceChildren(); } else render(next); }      // nothing to list while it is still looking
    }
    function appendLog(text) {
      const near = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
      log.append(h("div", { class: "t-line" }, text));
      while (log.childElementCount > MAX_LOG_LINES) log.firstElementChild.remove();
      if (near) log.scrollTop = log.scrollHeight;
    }

    list.replaceChildren(h("div", { class: "dim mod-empty" }, "Press “Check for Updates” to look for updates."));       // nothing is known until it has looked
    const off = bridge.on("jarvis/sys/updates/status", (msg) => {
      const view = interpret(msg);
      if (!view) return;
      if (view.kind === "log") appendLog(view.text); else setView(view);
    });
    return { onClose: () => { off(); clearTimeout(armTimer); } };
  },
};
