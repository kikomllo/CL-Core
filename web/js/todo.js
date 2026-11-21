// To-Do, "To-Do module" artifact: list tabs in a pill, ring rows, + Add task. Left click ticks; right click deletes a done task.
// Data: jarvis/sys/todo/status {todos:[{id,task,completed,list_name}]}; actions todo.list/create/complete/delete.
import { bridge } from "./bridge.js";
import { h, icon } from "./ui.js";

export const todoWindow = {
  id: "widget_todo", title: "To-Do List", w: 380, h: 440, minW: 320, minH: 300,
  build(body) {
    let todos = [], current = "My To-Do List", adding = false;
    const open = h("span", { class: "mod-count" }, "");
    const tabs = h("div", { class: "tabs-pill" });
    const list = h("div", { class: "mod-list" });
    const foot = h("div", { class: "mod-foot" });
    const newList = h("button", { class: "round sm", title: "New list", "aria-label": "New list", onclick: () => promptList() }, icon("add.svg", { size: 14 }));
    body.append(h("div", { class: "mod" }, h("div", { class: "mod-head" }, h("div", { class: "mod-title" }, "To-Do", open), newList), tabs, list, foot));

    const names = () => [...new Set(todos.map(t => t.list_name || "My To-Do List"))];
    function promptList() {
      const input = h("input", { class: "mod-input", placeholder: "List name, e.g. Groceries" });
      const done = (save) => {
        const v = input.value.trim(); row.remove();
        if (save && v) { current = v; extra.add(v); render(); }
      };
      const row = h("div", { class: "mod-compose" }, input, h("button", { class: "btn", onclick: () => done(true) }, "Create"), h("button", { class: "bare", "aria-label": "Cancel", onclick: () => done(false) }, icon("close.svg", { size: 12 })));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
      tabs.after(row); input.focus();
    }
    const extra = new Set();                         // lists created here that have no task yet (the backend only knows lists through tasks)

    function render() {
      const all = [...new Set([...names(), ...extra])];
      if (!all.includes(current)) current = all[0] || "My To-Do List";
      open.textContent = `${todos.filter(t => !t.completed).length} OPEN`;
      tabs.replaceChildren(...all.map(n => h("button", { class: "tab-pill", "aria-pressed": String(n === current),
        onclick: () => { current = n; adding = false; render(); } }, n)));
      tabs.hidden = all.length < 1;
      const mine = todos.filter(t => (t.list_name || "My To-Do List") === current);
      const rows = mine.map(t => {
        const r = h("div", { class: "todo-row" + (t.completed ? " done" : ""),
          onclick: (e) => { if (e.target.closest(".todo-text") && getSelection().toString()) return; bridge.act("todo.complete", { id: t.id, completed: !t.completed }); t.completed = !t.completed; render(); },
          oncontextmenu: (e) => { e.preventDefault(); if (t.completed) { bridge.act("todo.delete", { id: t.id }); todos = todos.filter(x => x !== t); render(); } } },
          h("span", { class: "ring" }), h("span", { class: "todo-text" }, t.task));
        return r;
      });
      let add;
      if (adding) {
        const input = h("input", { class: "mod-input", placeholder: "New task…" });
        const submit = () => { const v = input.value.trim(); adding = false; if (v) { bridge.act("todo.create", { task: v, list_name: current, silent: true }); extra.delete(current); } render(); };
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") { adding = false; render(); } });
        input.addEventListener("blur", () => setTimeout(() => { if (adding) { adding = false; render(); } }, 120));
        add = h("div", { class: "todo-row add" }, h("span", { class: "plus" }, "+"), input);
        queueMicrotask(() => input.focus());
      } else add = h("button", { class: "todo-row add", onclick: () => { adding = true; render(); } }, h("span", { class: "plus" }, "+"), "Add task");
      list.replaceChildren(...rows, add);
      const doneCount = mine.filter(t => t.completed).length;
      foot.replaceChildren(h("span", {}, `${doneCount} completed`),
        h("span", {}, doneCount ? "Right-click a completed task to delete" : ""),
        h("button", { class: "bare danger", title: "Delete this list", "aria-label": "Delete this list", hidden: !all.length, onclick: delList }, icon("close.svg", { size: 12 })));
    }
    function delList() {
      mine().forEach(t => bridge.act("todo.delete", { id: t.id }));
      todos = todos.filter(t => (t.list_name || "My To-Do List") !== current); extra.delete(current); render();
    }
    const mine = () => todos.filter(t => (t.list_name || "My To-Do List") === current);

    const off = bridge.on("jarvis/sys/todo/status", (d) => { if (d && Array.isArray(d.todos)) { todos = d.todos; for (const n of names()) extra.delete(n); render(); } });
    bridge.act("todo.list");
    render();
    return { onClose: off };
  },
};
