// Live Logs, drawn as a terminal: a dark screen showing `$ tail -f logs/latest_run.log`, colour-coded lines (dim timestamp, a stable
// colour per service tag, red errors, amber warnings), a blinking block cursor after the last line, and a "follow" button when you
// scroll away from the bottom. There is no typing into it: the bar at the bottom is a FILTER (a prompt with a match counter): every
// word must be in a line, `-word` hides lines that have it, matches are highlighted, Esc clears.
//
// Data: a tail of logs/latest_run.log, condensed by default (heartbeat topics and protocol chatter dropped, long lines cut, bearer
// tokens redacted: utils/clLogTail.py, shared with the Qt widget and the terminal it can open). The page keeps no copy of the file: it
// asks GET /api/log?pos=<offset> for the whole lines written since the offset it has reached (the first call, with no offset, is the
// tail), so opening it never loads the multi-MB file, and only the last MAX_LINES stay in the page. The poll stops while the window is
// closed or the tab is hidden and catches up when it comes back.
import { h } from "./ui.js";
import { parseFilter, matchLine, highlight, splitLine, tagSlot } from "./logfilter.js";

const POLL_MS = 500, MAX_LINES = 3000, FILTER_DEBOUNCE_MS = 90;

export const logsWindow = {
  id: "widget_logs", title: "Live Logs", w: 680, h: 440,
  build(body) {
    let pos = null, condensed = true, busy = false, timer = null, pinned = true, filter = parseFilter("");
    const all = [];                                                    // {text, parts, el|null}: every line kept (the filter only decides what is shown)

    // ---- the terminal screen ----
    const prompt = h("div", { class: "t-prompt" }, h("span", { class: "t-ps" }, "$"), " tail -f logs/latest_run.log");
    const lines = h("div", { class: "t-lines" });
    const cursor = h("span", { class: "t-cursor", "aria-hidden": "true" });
    const empty = h("div", { class: "t-empty", hidden: true }, "No lines match.");
    const screen = h("div", { class: "t-screen", role: "log", "aria-live": "off", tabindex: 0 }, prompt, lines, empty, h("div", { class: "t-endline" }, cursor));
    const follow = h("button", { class: "t-follow", hidden: true, onclick: () => { pinned = true; screen.scrollTop = screen.scrollHeight; follow.hidden = true; } }, "↓ follow");

    // ---- header: view mode and the separate terminal ----
    const cond = h("button", { class: "btn log-tool", "aria-pressed": "true", title: "Hide heartbeat noise and shorten long lines. Off shows the log as written.",
      onclick: () => { condensed = !condensed; cond.setAttribute("aria-pressed", String(condensed)); reload(); } }, "Condensed");
    const note = h("span", { class: "dim log-note" }, "");
    const term = h("button", { class: "btn log-tool", title: "Follow this log in a separate terminal window", onclick: openTerminal }, "Open terminal");

    // ---- the filter prompt ----
    const input = h("input", { class: "t-filter", type: "text", spellcheck: "false", autocomplete: "off", "aria-label": "Filter the log",
      placeholder: "filter: all words must match, -word hides" });
    const count = h("span", { class: "t-count dim" }, "");
    const clear = h("button", { class: "bare t-clear", hidden: true, "aria-label": "Clear the filter", title: "Clear (Esc)", onclick: () => setFilter("") }, "×");
    const bar = h("label", { class: "t-bar" }, h("span", { class: "t-ps" }, "›"), input, count, clear);

    body.append(h("div", { class: "logs" }, h("div", { class: "log-head" }, cond, note, term), h("div", { class: "t-wrap" }, screen, follow), bar));

    // ---- lines ----
    function lineEl(entry) {
      const { stamp, tag, rest, kind } = entry.parts;
      const mark = (text) => highlight(text, filter.include).map(([s, hit]) => (hit ? h("mark", {}, s) : s));
      const el = h("div", { class: "t-line" + (kind ? " " + kind : "") });
      if (stamp) el.append(h("span", { class: "t-stamp" }, mark(stamp)), " ");
      if (tag) el.append(h("span", { class: "t-tag c" + tagSlot(tag) }, mark(`[${tag}]`)), " ");
      const topic = tag === "MQTT" ? /^(\S+)(\s.*)?$/s.exec(rest) : null;            // "[MQTT] jarvis/sys/x {payload}": the topic is its own colour
      if (topic) el.append(h("span", { class: "t-topic" }, mark(topic[1])), mark(topic[2] || ""));
      else el.append(...mark(rest));
      return el;
    }
    function show() {                                                  // re-render every kept line that passes the filter
      const frag = document.createDocumentFragment();
      let shown = 0;
      for (const e of all) { if (matchLine(e.text, filter)) { e.el = lineEl(e); frag.append(e.el); shown++; } else e.el = null; }
      lines.replaceChildren(frag);
      status(shown);
      if (pinned) screen.scrollTop = screen.scrollHeight;
    }
    function status(shown) {
      const active = filter.include.length || filter.exclude.length;
      count.textContent = active ? `${shown} / ${all.length}` : "";
      clear.hidden = !active; empty.hidden = !(active && all.length && !shown);
    }
    function append(text) {
      if (!text) return;
      const frag = document.createDocumentFragment();
      for (const raw of text.split("\n")) {
        if (!raw) continue;
        const entry = { text: raw, parts: splitLine(raw), el: null };
        all.push(entry);
        if (matchLine(raw, filter)) { entry.el = lineEl(entry); frag.append(entry.el); }
      }
      lines.append(frag);
      while (all.length > MAX_LINES) all.shift().el?.remove();
      status(lines.childElementCount);
      if (pinned) screen.scrollTop = screen.scrollHeight;
    }

    // ---- filter ----
    let debounce = null;
    function setFilter(text) { input.value = text; applyFilter(); input.focus(); }
    function applyFilter() { filter = parseFilter(input.value); pinned = true; follow.hidden = true; show(); }
    input.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(applyFilter, FILTER_DEBOUNCE_MS); });
    input.addEventListener("keydown", (e) => { if (e.key === "Escape" && input.value) { e.stopPropagation(); setFilter(""); } });

    // ---- following the bottom ----
    screen.addEventListener("scroll", () => {
      pinned = screen.scrollTop + screen.clientHeight >= screen.scrollHeight - 8;
      follow.hidden = pinned;
    });

    // ---- polling ----
    async function openTerminal() {
      try {
        const r = await fetch("/api/log/terminal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ raw: !condensed }) });
        if (!(await r.json()).ok) throw new Error("no terminal");
      } catch { term.textContent = "No terminal found"; setTimeout(() => (term.textContent = "Open terminal"), 2500); }
    }
    async function poll() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const r = await fetch(`/api/log?${new URLSearchParams({ ...(pos == null ? {} : { pos }), raw: condensed ? "0" : "1" })}`);
        const d = await r.json();
        if (d.restarted) { all.length = 0; lines.replaceChildren(); }       // a new run rewrote the log: start over
        pos = d.pos; note.textContent = "";
        append(d.text);
      } catch { note.textContent = "dashboard server not reachable"; }
      busy = false;
    }
    function reload() { all.length = 0; lines.replaceChildren(); pos = null; pinned = true; prompt.lastChild.textContent = ` tail -f logs/latest_run.log${condensed ? "" : "  # raw"}`; status(0); poll(); }

    poll();
    timer = setInterval(poll, POLL_MS);
    const onVisible = () => { if (!document.hidden) poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return { onClose: () => { clearInterval(timer); clearTimeout(debounce); document.removeEventListener("visibilitychange", onVisible); } };
  },
};
