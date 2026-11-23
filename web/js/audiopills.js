// Microphone and speaker quick-switch pills (AudioQuickSwitchPill): an icon that grows on hover to show the current device,
// a click opens a menu of every device. Picking one saves it (audio_settings.*_device in core.json) and tells the live service,
// exactly like the Qt pills. The list comes from GET /api/audio-devices (the first call can take seconds).
import { bridge } from "./bridge.js";
import { h, icon } from "./ui.js";
import { bindPillLabel } from "./pillmarquee.js";

const LABEL_CAP = 170;           // design px: the widest the label opens (a longer device name scrolls)
const unit = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--u")) || 1;

const KINDS = [
  { kind: "input", icon: "mic.svg", word: "Mic", key: "audio_settings.input_device",
    send: (name) => bridge.act("mic.state", { action: "set_input_device", device_name: name }) },
  { kind: "output", icon: "speaker.svg", word: "Speaker", key: "audio_settings.output_device",
    send: (name) => bridge.act("tts.control", { action: "set_output_device", device_name: name }) },
];

/** {current, options:[{value,label}]} for one kind (the first call can take seconds: Windows lists its devices slowly). */
export const loadDevices = async (kind) => (await fetch(`/api/audio-devices?kind=${kind}`)).json();

/** Saves the device (audio_settings in core.json) and, if that worked, tells the live service. Returns whether it was saved. */
export async function setDevice(kind, value) {
  const k = KINDS.find((x) => x.kind === kind);
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: k.key, value }) });
    if (r.ok) k.send(value);
    return r.ok;
  } catch { return false; }
}

let openMenu = null;
const closeMenu = () => { openMenu?.remove(); openMenu = null; };

export function startAudioPills(container) {
  for (const k of KINDS) {
    const text = h("span", { class: "mq" }, "…"), label = h("span", { class: "label" }, text);
    const pill = h("button", { class: "pill", "aria-label": k.word, "aria-haspopup": "menu" },      // no title: the label opening on hover shows the device
      h("span", { class: "icon-slot" }, icon(k.icon, { size: 20 })), label);
    container.append(pill);
    const marquee = bindPillLabel(pill, label, text, () => LABEL_CAP * unit());

    const show = (current, options) => {
      text.textContent = options?.find((o) => o.value === current)?.label || current;
      pill.setAttribute("aria-label", `${k.word}: ${text.textContent}`);
      marquee.refresh();
    };
    // the label comes from the cheap settings call; the device list (slow the first time) is only fetched when the menu opens
    fetch("/api/settings").then((r) => r.json()).then((s) => show(s.audio_settings?.[`${k.kind}_device`] || "System Default"))
      .catch(() => {});

    pill.addEventListener("click", async (e) => {
      e.stopPropagation(); closeMenu();
      let data;
      try { data = await loadDevices(k.kind); } catch { return; }
      show(data.current, data.options);
      const menu = h("div", { class: "menu device-menu", role: "menu" }, data.options.map((o) =>
        h("button", { role: "menuitemradio", "aria-checked": String(o.value === data.current), class: o.value === data.current ? "on" : "",
          onclick: () => { closeMenu(); show(o.value, data.options); setDevice(k.kind, o.value); /* the label shows the choice; the next open shows what is really saved */ }
           }, o.label)));
      document.body.append(menu); openMenu = menu;
      const r = pill.getBoundingClientRect(), mw = menu.offsetWidth, mh = menu.offsetHeight;
      menu.style.left = Math.max(8, Math.min(r.left, innerWidth - mw - 8)) + "px";
      menu.style.top = (r.top - mh - 8 >= 0 ? r.top - mh - 8 : r.bottom + 8) + "px";      // the dock is at the bottom: open upwards
      setTimeout(() => addEventListener("pointerdown", (ev) => { if (!menu.contains(ev.target)) closeMenu(); }, { once: true }), 0);
    });
  }
}
