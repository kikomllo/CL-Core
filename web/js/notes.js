// Quick Notes, "Notes module" artifact: a list of title + first-line previews, + button; click opens the editor.
// Data: jarvis/sys/note/status {notes:[{id,title,text}]}; actions note.list/create/update/delete.
import { bridge } from "./bridge.js";
import { h, icon } from "./ui.js";

export const notesWindow = {
  id: "widget_notes", title: "Quick Notes", w: 380, h: 440,
  build(body) {
    let notes = [], openId = null, openNext = false;
    const root = h("div", { class: "mod" });
    body.append(root);
    const preview = (t) => (t || "").split("\n", 1)[0];

    function save() {
      if (openId == null) return;
      const title = root.querySelector(".note-title")?.value.trim() || "Unnamed", text = root.querySelector(".note-text")?.value ?? "";
      bridge.act("note.update", { id: openId, title, text });
      const n = notes.find(x => x.id === openId); if (n) { n.title = title; n.text = text; }
    }
    function back() {
      const title = root.querySelector(".note-title")?.value.trim(), text = root.querySelector(".note-text")?.value.trim();
      if (openId != null && !title && !text) del(openId); else save();
      openId = null; render();
    }
    function del(id) { bridge.act("note.delete", { id }); notes = notes.filter(n => n.id !== id); if (openId === id) openId = null; render(); }

    function render() {
      if (openId != null) {
        const n = notes.find(x => x.id === openId) || {};
        const title = h("input", { class: "note-title", placeholder: "Title", value: n.title || "" });
        const text = h("textarea", { class: "note-text", placeholder: "Type a note…" }, n.text || "");
        title.addEventListener("blur", save); text.addEventListener("blur", save);
        root.replaceChildren(
          h("div", { class: "mod-head" }, h("button", { class: "round sm", title: "Back to notes", "aria-label": "Back to notes", onclick: back }, icon("chevron_left.svg", { size: 14 })), title,
            h("button", { class: "bare danger", title: "Delete note", "aria-label": "Delete note", onclick: () => del(openId) }, icon("close.svg", { size: 12 }))),
          text);
        queueMicrotask(() => text.focus());
        return;
      }
      const rows = notes.map(n => h("div", { class: "note-row", onclick: (e) => { if (!e.target.closest("button")) { openId = n.id; render(); } } },
        h("div", { class: "note-top" }, h("span", { class: "mod-name" + (n.title ? "" : " dim") }, n.title || "New note"),
          h("button", { class: "bare", title: "Delete note", "aria-label": "Delete note", onclick: () => del(n.id) }, icon("close.svg", { size: 12 }))),
        preview(n.text) ? h("div", { class: "note-body" }, preview(n.text)) : null));
      root.replaceChildren(
        h("div", { class: "mod-head" }, h("div", { class: "mod-title" }, "Notes", h("span", { class: "mod-count" }, notes.length ? String(notes.length) : "")),
          h("button", { class: "round sm add", title: "Add note", "aria-label": "Add note", onclick: () => { openNext = true; bridge.act("note.create", { text: "" }); } }, icon("add.svg", { size: 14 }))),
        h("div", { class: "mod-list" }, ...(rows.length ? rows : [h("div", { class: "dim mod-empty" }, "No notes yet.")])));
    }

    const off = bridge.on("jarvis/sys/note/status", (d) => {
      if (!d || !Array.isArray(d.notes)) return;
      const before = new Set(notes.map(n => n.id));
      notes = d.notes;
      const fresh = notes.find(n => !before.has(n.id));
      if (openNext && fresh) { openNext = false; openId = fresh.id; }
      // never clobber text being typed: only re-render the list page; the editor keeps its own fields
      if (openId == null || !root.querySelector(".note-text")) render();
    });
    bridge.act("note.list");
    render();
    return { onClose() { save(); off(); } };
  },
};
