// The JARVIS orb: bloom, two rotating dashed rings, three sine waves, particles. A port of
// clUI.JarvisVisualizer. The original stepped once per 60 Hz (idle: 8 Hz) timer tick with per-tick easing
// factors; here every factor is applied per elapsed time so it looks the same at any frame rate.
import { brain } from "./state.js";
import { palette, activeSkin } from "./skin.js";
import { loadHeadModel, HeadPainter, Glitch, pulseScale } from "./ultronhead.js";
import { restingLook, bloomRadius, headUnit, overlayVisible } from "./orb-model.js";

const BLOOM_PEAK_ALPHA = 100;
const STATES = {            // opacity, amplitude, frequency, speed, scale
  IDLE:       { op: null, amp: 10, freq: 0.015, speed: 0.02, scale: null },
  SPEAKING:   { op: 1.0,  amp: 10, freq: 0.03,  speed: 0.3,  scale: 1.0 },
  LISTENING:  { op: 1.0,  amp: 15, freq: 0.015, speed: 0.05, scale: 1.0 },
  ATTENTION:  { op: 0.3,  amp: 15, freq: 0.015, speed: 0.05, scale: 0.5 },
  RECORDING:  { op: 0.8,  amp: 20, freq: 0.02,  speed: 0.1,  scale: 1.0 },
  PROCESSING: { op: 1.0,  amp: 25, freq: 0.08,  speed: 0.15, scale: 0.5 },
};

export function startOrb(canvas, getUnit) {
  const g = canvas.getContext("2d");
  const o = { opacity: 0.8, tOpacity: 0.8, scale: 0.8, tScale: 0.8, amp: 10, tAmp: 10, freq: 0.015, speed: 0.02,
              time: 0, particles: [], state: "IDLE" };
  let W = 0, H = 0, dpr = 1;
  // Under the Ultron skin the head IS the visualizer (no bloom, rings, waves or particles), driven by the same voice level and state.
  const ultron = { painter: null, glitch: null, requested: false, talk: 0, rows: null, flicker: 1 };
  function wantHead() {
    if (activeSkin() !== "ultron") return false;
    if (!ultron.requested) {
      ultron.requested = true;
      Promise.all([loadHeadModel(), fetch("/api/config").then((r) => r.json()).catch(() => ({}))]).then(([model, settings]) => {
        ultron.painter = new HeadPainter(model); ultron.glitch = new Glitch(settings.ultron_glitch);
      }).catch(() => { ultron.requested = false; });
    }
    return !!ultron.painter;                                         // until it has loaded the (recoloured) orb is shown
  }

  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  new ResizeObserver(resize).observe(canvas); resize();

  // In the desktop shell's overlay window the orb shows only while the assistant is active, and the shell hides the window the rest of the time
  // (no frames, no compositing). It is told as soon as something is to show (an event, not the frame loop, which stops when the window is hidden).
  const overlay = () => document.documentElement.dataset.mode === "overlay";
  const shell = window.jarvisShell || null;
  let shown = null;
  function reportVisible() {
    const v = !overlay() || overlayVisible(o.opacity, o.tOpacity);
    if (shell && v !== shown) { shown = v; shell.setVisible(v); }
  }
  function setState(state) {
    const c = STATES[state] || STATES.IDLE, rest = restingLook(overlay());
    o.state = state;
    o.tOpacity = c.op ?? rest.op; o.tAmp = c.amp; o.freq = c.freq; o.speed = c.speed; o.tScale = c.scale ?? rest.scale;
    reportVisible();
  }
  brain.listeners.add(setState); setState(brain.state);
  window.addEventListener("jarvis-mode", () => {                      // the shell switched between overlay and dashboard
    if (overlay() && o.state === "IDLE") { o.opacity = 0; o.scale = 0.5; g.clearRect(0, 0, canvas.width, canvas.height); }       // no fade-out: the window should be gone at once
    setState(o.state);
  });

  const maxAmpFor = () => (H > 100 ? Math.min(W, H) / 4 / getUnit() : 150);
  function applyVolume() {
    // The loudest wave, in design px (drawing multiplies by the unit). The Qt orb used height / 4, which is 270 on a 1080-tall
    // screen but 480 on a 1920-tall portrait one (and doubled on a 4K screen); the short edge gives the same size either way up.
    const maxAmp = maxAmpFor(), v = brain.vol;
    if (o.state === "SPEAKING") o.tAmp = Math.max(10, Math.min(1, v / 100) * 0.7 * maxAmp);
    else if (o.state === "RECORDING" || o.state === "LISTENING" || o.state === "ATTENTION") {
      const n = Math.min(1, v / 3000) * 0.8;
      o.tAmp = Math.max(10, n * maxAmp);
      if (o.state === "RECORDING") o.tOpacity = Math.min(1, Math.max(0.6, n + 0.6));
    }
  }

  const ease = (cur, target, rate, k) => cur + (target - cur) * (1 - Math.pow(1 - rate, k));

  function step(k) {                   // k = elapsed time in 60 Hz ticks
    applyVolume();
    o.opacity = ease(o.opacity, o.tOpacity, 0.1, k);
    o.amp = ease(o.amp, o.tAmp, 0.4, k);
    o.scale = ease(o.scale, o.tScale, 0.15, k);
    o.time += o.speed * k;
    if (ultron.painter) {                                            // clUI.JarvisVisualizer: quick attack, slower release
      const maxAmp = maxAmpFor();
      const raw = o.state === "SPEAKING" ? (o.amp - 10) / (maxAmp * 0.7) : (o.state === "RECORDING" || o.state === "LISTENING") ? 0.5 * (o.amp - 10) / maxAmp : 0;
      const target = Math.min(1, Math.max(0, raw));
      ultron.talk += (target - ultron.talk) * (1 - Math.pow(1 - (target > ultron.talk ? 0.6 : 0.25), k));
      [ultron.rows, ultron.flicker] = ultron.glitch.update(o.state, ultron.painter.h);
    }
    if (o.amp > 5 && Math.random() < Math.min(1, (o.amp / 30) * k)) {
      o.particles.push({ x: Math.random() * W, dy: (Math.random() * 2 - 1) * o.amp, vx: Math.random() * 4 - 2,
                         vy: Math.random() * 2 - 1, life: 1, size: 1 + Math.floor(Math.random() * 3) });
    }
    for (const p of o.particles) { p.x += p.vx * k; p.dy += p.vy * k; p.life -= 0.03 * k; }
    o.particles = o.particles.filter(p => p.life > 0);
  }

  function draw() {
    const u = getUnit();
    const P = palette();
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2;
    if (wantHead()) {
      const speaking = o.state === "SPEAKING", sc = o.scale * pulseScale(ultron.talk, speaking, o.time);     // it gets bigger and smaller with the voice
      g.save(); g.translate(cx, cy); g.scale(sc, sc); g.translate(-cx, -cy);
      g.imageSmoothingEnabled = true; g.globalAlpha = o.opacity;
      ultron.painter.paint(g, cx, cy, Math.round(300 * headUnit(u, W, overlay()) / ultron.painter.h), ultron.talk, o.time, ultron.rows, ultron.flicker, dpr);
      g.restore();
      return;
    }
    g.save();
    g.translate(cx, cy); g.scale(o.scale, o.scale); g.translate(-cx, -cy);
    g.globalAlpha = o.opacity;

    // bloom: gaussian falloff that reaches exactly zero at the rim, so no visible disc edge
    const R = bloomRadius(u, W, H, o.scale, overlay()), rim = Math.exp(-3.5);
    const grad = g.createRadialGradient(cx, cy, 0, cx, cy, R);
    for (let i = 0; i <= 16; i++) {
      const t = i / 16, fall = (Math.exp(-3.5 * t * t) - rim) / (1 - rim);
      grad.addColorStop(t, P.bloom(t, BLOOM_PEAK_ALPHA * fall / 255));
    }
    g.fillStyle = grad; g.fillRect(cx - 500 * u, cy - 500 * u, 1000 * u, 1000 * u);

    // rings
    g.save(); g.translate(cx, cy);
    g.save(); g.rotate(o.time * 15 * Math.PI / 180); g.lineWidth = 2 * u;
    g.setLineDash([6 * u, 4 * u]); g.strokeStyle = P.ring1(60 / 255 * o.opacity);
    g.beginPath(); g.arc(0, 0, 80 * u, 0, 7); g.stroke(); g.restore();
    g.save(); g.rotate(-o.time * 25 * Math.PI / 180); g.lineWidth = 1 * u;
    g.setLineDash([1 * u, 3 * u]); g.strokeStyle = P.ring2(90 / 255 * o.opacity);
    g.beginPath(); g.arc(0, 0, 50 * u, 0, 7); g.stroke(); g.restore();
    g.restore();

    // waves
    const phases = [0, 2, 4], amps = [o.amp, o.amp * 0.6, o.amp * 0.3], step = Math.max(1, 6 * u);
    g.setLineDash([]);
    for (let i = 0; i < 3; i++) {
      const [r, gr, b, a] = P.waves[i];
      const lg = g.createLinearGradient(0, 0, W, 0);
      lg.addColorStop(0, `rgba(${r},${gr},${b},0)`); lg.addColorStop(0.2, `rgba(${r},${gr},${b},${a / 255})`);
      lg.addColorStop(0.8, `rgba(${r},${gr},${b},${a / 255})`); lg.addColorStop(1, `rgba(${r},${gr},${b},0)`);
      g.strokeStyle = lg; g.lineWidth = i < 2 ? 2 : 1;
      g.shadowBlur = P.waveGlow[i] * u; g.shadowColor = `rgba(${r},${gr},${b},.9)`;   // the glow rides on the line itself (Visualizer Glow Concept)
      g.beginPath(); g.moveTo(0, cy);
      for (let x = 0; x <= W + step; x += step) {
        const env = Math.pow(Math.sin(Math.PI * x / W), 3);
        g.lineTo(x, cy + Math.sin(x * o.freq + o.time + phases[i]) * amps[i] * u * env);
      }
      g.stroke();
    }
    g.shadowBlur = 0;

    // particles
    for (const p of o.particles) {
      const env = Math.sin(Math.PI * p.x / W);
      const y = cy + Math.sin(p.x * o.freq + o.time) * o.amp * env + p.dy;
      const a = p.life * o.opacity * env;
      if (a > 0.004) { g.fillStyle = P.particle(a); g.fillRect(p.x, y, p.size, p.size); }
    }
    g.restore();
  }

  let last = performance.now(), acc = 0;
  function frame(now) {
    requestAnimationFrame(frame);
    const dt = now - last;
    const idle = o.state === "IDLE" && Math.abs(o.opacity - o.tOpacity) < 0.01 && Math.abs(o.scale - o.tScale) < 0.01;
    const minGap = idle ? 1000 / 8 : 0;          // the original dropped to 8 Hz at rest
    if (dt < minGap) return;
    last = now;
    step(Math.min(dt, 100) / (1000 / 60));
    draw();
    if (shell) reportVisible();                                       // the fade-out is over: let the shell hide the window
  }
  requestAnimationFrame(frame);
}
