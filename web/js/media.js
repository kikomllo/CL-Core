// Media widget, laid out like the "Media Widget Redesign" artifact: SPOTIFY PLAYER badge + shuffle/repeat (bare icons with an
// on-dot), title/artist, prev/play/next, seek bar, then three circles (Up Next, lyrics, App Volume) that open drawers.
// Spotify's own volume is the hover fader from the "Media Widget Concept". Every send is the same spotify.control /
// terminal.app_volume action the Qt widget used (ui/clMediaWidget.py).
import { bridge } from "./bridge.js";
import { h, icon, loadUiState, saveUiState } from "./ui.js";
import { savedChoice } from "./session-model.js";
import { lyrics } from "./lyrics.js";

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const REPEAT_CYCLE = { off: "context", context: "track", track: "off" };
const BRAND = { spotify: "spotify.svg", firefox: "firefox.svg", discord: "discord.svg", obs: "obs.svg", chrome: "chrome.svg",
  brave: "brave.svg", opera: "opera.svg", vivaldi: "vivaldi.svg", razer: "razer.svg" };
const brandFor = (name) => { name = (name || "").toLowerCase(); for (const k in BRAND) if (name.includes(k)) return BRAND[k]; return null; };
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const setFill = (el) => el.style.setProperty("--fill", `${(100 * (el.value - el.min)) / (el.max - el.min)}%`);
const ctl = (o) => bridge.act("spotify.control", { silent: true, ...o });

export const mediaWindow = {
  id: "widget_media_controls", title: "Media Controls", w: 430, h: 300,
  build(body) {
    const st = { status: "Paused", pos: 0, dur: 0, shuffle: false, repeat: "off", pending: {}, lastVol: 100, drawer: null, queueBusy: false };

    const mkToggle = (name) => h("button", { class: "dot-toggle" }, icon(name, { size: 16 }), h("i"));
    const shuffleBtn = mkToggle("shuffle.svg"), repeatBtn = mkToggle("repeat.svg");
    shuffleBtn.setAttribute("aria-label", "Shuffle"); repeatBtn.setAttribute("aria-label", "Repeat");
    const title = h("div", { class: "m-title" }, "Nothing playing"), artist = h("div", { class: "m-artist" }, "");
    const top = h("div", { class: "m-top" }, h("span", { class: "m-badge" }, "Spotify Player"), shuffleBtn, repeatBtn);

    const prev = h("button", { class: "round sm", "aria-label": "Previous track" }, icon("skip_back.svg", { size: 15 }));
    const play = h("button", { class: "round lg", "aria-label": "Play or pause" }, icon("play.svg", { size: 18 }));
    const next = h("button", { class: "round sm", "aria-label": "Next track" }, icon("skip_forward.svg", { size: 15 }));
    const transport = h("div", { class: "m-transport" }, prev, play, next);

    const seek = h("div", { class: "seek", role: "slider", tabindex: 0, "aria-label": "Seek" },
      h("div", { class: "seek-track" }, h("div", { class: "seek-fill" }), h("div", { class: "seek-knob" })));
    const tPos = h("span", { class: "dim" }, "0:00"), tDur = h("span", { class: "dim" }, "0:00");
    const seekRow = h("div", { class: "m-seek" }, tPos, seek, tDur);

    const vol = h("input", { type: "range", min: 0, max: 100, value: 100, class: "slider v", "aria-label": "Spotify volume" });
    const muteBtn = h("button", { class: "bare mute", title: "Mute Spotify", "aria-label": "Mute" }, icon("speaker.svg", { size: 16 }));
    const fader = h("div", { class: "fader" }, vol, muteBtn);

    const qBtn = h("button", { class: "round tab", title: "Up Next", "aria-label": "Up next queue" }, icon("list.svg", { size: 13 }), h("i"));
    const lyBtn = h("button", { class: "round tab mid", title: "Lyrics", "aria-label": "Toggle lyrics" }, icon("music.svg", { size: 15 }), h("i"));
    const avBtn = h("button", { class: "round tab", title: "App Volume", "aria-label": "App volume" }, icon("headphones.svg", { size: 13 }), h("i"));
    const tabs = h("div", { class: "m-tabs" }, qBtn, lyBtn, avBtn);

    const qCaption = h("span", { class: "dim" }, "Loading…"), qRows = h("div", { class: "q-rows" });
    const qRefresh = h("button", { class: "round xs", title: "Refresh from Spotify", "aria-label": "Refresh queue from Spotify" }, icon("refresh.svg", { size: 11 }));
    const queueDrawer = h("div", { class: "drawer", hidden: true }, h("div", { class: "m-capbar" }, qCaption, qRefresh), qRows);
    const avEmpty = h("div", { class: "dim" }, "No apps playing audio."), avRows = h("div", { class: "av-rows" });
    const appDrawer = h("div", { class: "drawer", hidden: true }, avRows, avEmpty);

    body.append(h("div", { class: "media" }, top, title, artist, transport, seekRow, tabs, queueDrawer, appDrawer, fader));

    // ----- status -----
    const paint = () => {
      tPos.textContent = fmt(st.pos); tDur.textContent = fmt(st.dur);
      seek.style.setProperty("--p", (st.dur > 0 ? Math.max(0, Math.min(1, st.pos / st.dur)) : 0) * 100 + "%");
    };
    const showModes = () => {
      shuffleBtn.classList.toggle("on", st.shuffle);
      repeatBtn.classList.toggle("on", st.repeat !== "off");
      repeatBtn.firstChild.setIcon(st.repeat === "track" ? "repeat_one.svg" : "repeat.svg");
      repeatBtn.title = `Repeat: ${st.repeat}`;
    };
    const showPlay = () => play.firstChild.setIcon(st.status === "Playing" ? "pause.svg" : "play.svg");
    const setMute = (v) => { muteBtn.firstChild.setIcon(v === 0 ? "speaker_mute.svg" : "speaker.svg"); muteBtn.classList.toggle("on", v === 0); };
    let pendingTimer;
    const expect = (key, value, btn) => {
      st.pending[key] = value; btn.classList.add("pending");
      clearTimeout(pendingTimer);
      pendingTimer = setTimeout(() => { st.pending = {}; shuffleBtn.classList.remove("pending"); repeatBtn.classList.remove("pending"); }, 2500);
    };

    const offStatus = bridge.on("jarvis/sys/media_status", (d) => {
      if (typeof d !== "object") return;
      title.textContent = d.title || "Unknown"; artist.textContent = d.artist || "Unknown";
      st.pos = d.position || 0; st.dur = d.duration || 0; st.status = d.status || "Paused";
      seek.classList.toggle("locked", d.can_seek === false);
      for (const [key, btn] of [["shuffle", shuffleBtn], ["repeat", repeatBtn]]) {
        const v = d[key]; if (v == null) continue;
        if (key in st.pending) { if (v !== st.pending[key]) continue; delete st.pending[key]; btn.classList.remove("pending"); }
        st[key] = v;
      }
      showModes(); showPlay(); paint();
      if (Number.isInteger(d.volume) && document.activeElement !== vol) {
        vol.value = d.volume; setFill(vol); setMute(d.volume);
        if (d.volume > 0) st.lastVol = d.volume;
      }
    });
    const tick = setInterval(() => { if (st.status === "Playing" && st.dur > 0) { st.pos = Math.min(st.dur, st.pos + 1); paint(); } }, 1000);
    setFill(vol);

    // ----- controls -----
    play.onclick = () => { st.status = st.status === "Playing" ? "Paused" : "Playing"; showPlay(); ctl({ action: "toggle" }); };
    prev.onclick = () => ctl({ action: "prev" });
    next.onclick = () => ctl({ action: "next" });
    shuffleBtn.onclick = () => { st.shuffle = !st.shuffle; showModes(); expect("shuffle", st.shuffle, shuffleBtn); ctl({ action: "shuffle", enabled: st.shuffle }); };
    repeatBtn.onclick = () => { st.repeat = REPEAT_CYCLE[st.repeat]; showModes(); expect("repeat", st.repeat, repeatBtn); ctl({ action: "repeat", mode: st.repeat }); };
    const sendVol = () => ctl({ action: "volume", volume: +vol.value });
    vol.oninput = () => { setFill(vol); setMute(+vol.value); if (+vol.value > 0) st.lastVol = +vol.value; };
    vol.onchange = sendVol;
    muteBtn.onclick = () => { vol.value = +vol.value > 0 ? 0 : st.lastVol || 100; setFill(vol); setMute(+vol.value); sendVol(); };
    seek.addEventListener("pointerdown", (e) => {
      if (st.dur <= 0 || seek.classList.contains("locked")) return;
      const r = seek.getBoundingClientRect(), f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      st.pos = f * st.dur; paint(); ctl({ action: "seek", position_ms: Math.round(st.pos * 1000) });
    });

    // ----- lyrics switch (the overlay itself lives in lyrics.js) -----
    const showLy = () => lyBtn.classList.toggle("on", lyrics.enabled);
    lyBtn.onclick = () => { lyrics.setEnabled(!lyrics.enabled); showLy(); };
    showLy();

    // ----- drawers (one open at a time) -----
    const setDrawer = (which) => {
      st.drawer = st.drawer === which ? null : which;
      saveUiState("media.drawer", st.drawer);
      queueDrawer.hidden = st.drawer !== "queue"; appDrawer.hidden = st.drawer !== "app";
      qBtn.classList.toggle("on", st.drawer === "queue"); avBtn.classList.toggle("on", st.drawer === "app");
      if (st.drawer === "queue") ctl({ action: "queue" });
      if (st.drawer === "app") { bridge.act("terminal.app_volume", { action: "list_app_volumes" }); bridge.act("terminal.app_volume", { action: "list_output_devices" }); }
      // the window grows with its drawer, and shrinks back, like the Qt widget
      requestAnimationFrame(() => fitHeight(body));
    };
    qBtn.onclick = () => setDrawer("queue"); avBtn.onclick = () => setDrawer("app");
    const savedDrawer = savedChoice(loadUiState(), "media.drawer", ["queue", "app"], null);      // the drawer you had open
    if (savedDrawer) setDrawer(savedDrawer);
    const fitHeight = (b) => {
      const win = b.closest(".win"); if (!win) return;
      const want = b.firstChild.scrollHeight + win.querySelector(".win-head").offsetHeight + 2;
      win.style.height = Math.max(parseFloat(win.style.minHeight), want) + "px";
    };

    let queueTotal = 0, queueTimer, queueLoaded = false;
    const queueBusy = (on, what = "refreshing from Spotify…") => {
      st.queueBusy = on; qRows.style.opacity = on ? 0.3 : 1;
      qCaption.textContent = on ? what : !queueLoaded ? "Loading…" : queueTotal ? `${queueTotal} tracks, followed locally` : "Nothing queued.";
    };
    qRefresh.onclick = () => { queueBusy(true); ctl({ action: "queue_refresh" }); clearTimeout(queueTimer); queueTimer = setTimeout(() => queueBusy(false), 4000); };
    const offQueue = bridge.on("jarvis/sys/spotify_queue", (d) => {
      const items = d.queue || []; queueLoaded = true; queueTotal = d.remaining ?? items.length;
      qRows.replaceChildren(...items.slice(0, 5).map((it, i) => {
        const row = h("button", { class: "q-row", disabled: !it.uri },
          h("span", { class: "dim q-n" }, String(i + 1)),
          h("span", { class: "q-info" }, h("span", { class: "q-t" }, it.title || "Unknown"), h("span", { class: "dim" }, it.artist || "")),
          i === 0 ? h("span", { class: "q-next" }, "NEXT") : null,
          h("span", { class: "dim" }, fmt((it.duration_ms || 0) / 1000)));
        row.onclick = () => {
          if (st.queueBusy || !it.uri) return;
          queueBusy(true, "switching…"); clearTimeout(queueTimer); queueTimer = setTimeout(() => queueBusy(false), 4000);
          ctl({ action: "play_queue_item", uri: it.uri, queue_index: i });
        };
        return row;
      }));
      clearTimeout(queueTimer); queueBusy(false);
      if (st.drawer === "queue") requestAnimationFrame(() => fitHeight(body));
    });

    let devices = [];
    const offDev = bridge.on("jarvis/sys/app_output_devices", (d) => { devices = d.devices || []; });
    const rows = new Map();
    const offApps = bridge.on("jarvis/sys/app_volumes", (d) => {
      const apps = d.apps || [], ids = new Set(apps.map(a => a.id).filter(Boolean));
      for (const [id, r] of rows) if (!ids.has(id)) { r.stop(); r.el.remove(); rows.delete(id); }
      for (const a of apps) {
        if (!a.id) continue;
        let r = rows.get(a.id);
        if (!r) { r = makeAppRow(a); rows.set(a.id, r); avRows.append(r.el); }
        r.update(a);
      }
      avEmpty.hidden = apps.length > 0;
      if (st.drawer === "app") requestAnimationFrame(() => fitHeight(body));
    });
    function makeAppRow(app) {
      const ic = brandFor(app.name) ? icon(brandFor(app.name), { size: 20, keep: true }) : h("span", { class: "app-ph" });
      const name = h("div", { class: "av-name" }), sub = h("div", { class: "dim av-sub" });
      const dev = h("button", { class: "bare", title: "Output device", "aria-label": "Output device" }, icon("headphones.svg", { size: 14 }));
      const pl = h("button", { class: "round xs", "aria-label": "Play or pause app" }, icon("pause.svg", { size: 11 }));
      const mu = h("button", { class: "bare", "aria-label": "Mute app" }, icon("speaker.svg", { size: 14 }));
      const sl = h("input", { type: "range", min: 0, max: 100, class: "slider av-vol", "aria-label": "App volume" });
      let cur = app;
      dev.onclick = () => {
        const pick = (id) => { menu.remove(); bridge.act("terminal.app_volume", { action: "set_app_output_device", target: cur.id, device_id: id, silent: true }); };
        const menu = h("div", { class: "menu" }, h("button", { onclick: () => pick("") }, "System Default"),
          ...devices.map(d => h("button", { onclick: () => pick(d.id || "") }, d.name || "Unknown device")));
        const r = dev.getBoundingClientRect();
        Object.assign(menu.style, { left: Math.min(r.left, innerWidth - 260) + "px", top: r.bottom + "px" });
        document.body.append(menu);
        setTimeout(() => addEventListener("pointerdown", () => menu.remove(), { once: true }), 0);
      };
      pl.onclick = () => cur.media_player && bridge.act("terminal.app_volume", { action: "toggle_app_playback", target: cur.media_player, silent: true });
      mu.onclick = () => bridge.act("terminal.app_volume", { action: "toggle_app_mute", target: cur.id, silent: true });
      sl.oninput = () => setFill(sl);
      sl.onchange = () => bridge.act("terminal.app_volume", { action: "set_app_volume", target: cur.id, level: +sl.value, silent: true });
      const info = h("div", { class: "av-info" }, name, sub);
      const el = h("div", { class: "av-row" }, ic, info, dev, pl, mu, sl);
      // the name and now-playing only show when there is room to read some of it (about 3 characters: a character is ~0.6 of the font size wide); narrower, they go and the buttons keep the row
      const ro = new ResizeObserver(() => el.classList.toggle("tight", info.clientWidth < 1.8 * parseFloat(getComputedStyle(name).fontSize)));
      ro.observe(info);
      return {
        el, stop: () => ro.disconnect(),
        update(a) {
          cur = a; name.textContent = cap(a.name || "Unknown"); sub.textContent = a.now_playing || ""; sub.hidden = !a.now_playing;
          pl.hidden = !a.media_player; pl.firstChild.setIcon(a.is_playing === false ? "play.svg" : "pause.svg");
          mu.firstChild.setIcon(a.muted ? "speaker_mute.svg" : "speaker.svg"); mu.style.color = a.muted ? "var(--title-accent)" : "";
          if (document.activeElement !== sl) sl.value = a.volume ?? sl.value;
          setFill(sl);
        },
      };
    }

    ctl({ action: "status" });
    setTimeout(() => fitHeight(body), 60);          // open at the height the content needs, not the default box
    return { onClose() { clearInterval(tick); clearTimeout(queueTimer); [offStatus, offQueue, offDev, offApps].forEach(f => f()); } };
  },
};
