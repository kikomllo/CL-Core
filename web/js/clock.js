// Date and time above the orb. Reads the system clock only; the glow is CSS text-shadow.
const NAMES = {
  en: { days: ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
        months: ["January","February","March","April","May","June","July","August","September","October","November","December"] },
  pt: { days: ["Segunda-feira","Terça-feira","Quarta-feira","Quinta-feira","Sexta-feira","Sábado","Domingo"],
        months: ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"] },
};
const DEFAULTS = { enabled: true, hour24: true, seconds: true, language: "en", glow: 1.0 };

export function startClock(userSettings = {}) {
  const s = { ...DEFAULTS };
  for (const k of Object.keys(DEFAULTS)) {
    const v = userSettings[k];
    if (k === "language") s[k] = v in NAMES ? v : DEFAULTS[k];
    else if (k === "glow") s[k] = typeof v === "number" ? Math.min(3, Math.max(0, v)) : DEFAULTS[k];
    else s[k] = typeof v === "boolean" ? v : DEFAULTS[k];
  }
  const root = document.getElementById("clock");
  root.hidden = !s.enabled;
  if (!s.enabled) return;
  root.style.setProperty("--glow", s.glow);
  root.querySelector(".time").style.textShadow = s.glow <= 0 ? "none"
    : `0 0 calc(${10 * s.glow} * var(--u)) rgba(255,140,0,${Math.min(1, .55 * s.glow)}), 0 0 calc(3 * var(--u)) rgba(255,170,0,${Math.min(1, .4 * s.glow)})`;
  const h = document.getElementById("clk-h"), m = document.getElementById("clk-m"),
        sec = document.getElementById("clk-s"), date = document.getElementById("clk-date");
  sec.hidden = !s.seconds;
  const n = NAMES[s.language];
  let timer;
  function tick() {
    const d = new Date();
    const hour = s.hour24 ? d.getHours() : (d.getHours() % 12 || 12);
    h.textContent = String(hour).padStart(2, "0");
    m.textContent = String(d.getMinutes()).padStart(2, "0");
    sec.textContent = String(d.getSeconds()).padStart(2, "0");
    const dow = (d.getDay() + 6) % 7;
    date.textContent = `${n.days[dow]} · ${d.getDate()} ${n.months[d.getMonth()]} ${d.getFullYear()}`.toUpperCase();
    // aim at the next change, a few ms past the boundary so the old value is never redrawn
    const into = d.getMilliseconds();
    const wait = s.seconds ? 1000 - into + 5 : (59 - d.getSeconds()) * 1000 + (1000 - into) + 5;
    timer = setTimeout(tick, wait);
  }
  tick();
  document.addEventListener("visibilitychange", () => { clearTimeout(timer); if (!document.hidden) tick(); });
}
