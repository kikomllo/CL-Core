// The JARVIS orb: bloom, two rotating dashed rings, three sine waves, particles. A port of
// clUI.JarvisVisualizer. The original stepped once per 60 Hz (idle: 8 Hz) timer tick with per-tick easing
// factors; here every factor is applied per elapsed time so it looks the same at any frame rate.
import { brain } from "./state.js";
import { palette } from "./skin.js";

const BLOOM_RADIUS = 170, BLOOM_PEAK_ALPHA = 100;
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

  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  new ResizeObserver(resize).observe(canvas); resize();

  function setState(state) {
    const c = STATES[state] || STATES.IDLE;
    o.state = state;
    o.tOpacity = c.op ?? 0.8; o.tAmp = c.amp; o.freq = c.freq; o.speed = c.speed; o.tScale = c.scale ?? 0.8;
  }
  brain.listeners.add(setState); setState(brain.state);

  function applyVolume() {
    const maxAmp = H > 100 ? H / 4 : 150, v = brain.vol;
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
    g.save();
    g.translate(cx, cy); g.scale(o.scale, o.scale); g.translate(-cx, -cy);
    g.globalAlpha = o.opacity;

    // bloom: gaussian falloff that reaches exactly zero at the rim, so no visible disc edge
    const R = BLOOM_RADIUS * u, rim = Math.exp(-3.5);
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
  }
  requestAnimationFrame(frame);
}
