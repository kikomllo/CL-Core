// Colour skins. The source palette is JARVIS orange; "ultron" recolours it with the same deterministic mapping as src/clSkin.py
// (tests/test_webSkin.py checks the two agree). CSS is recoloured on the server (clWebUI serves /style.css through
// clSkin.recolor_css), so only colours that are built in JS pass through here: the orb, the lyrics, the clock glow.
// Colours that must not follow the skin (a bulb's real colour in the Lights window) simply never call it.
const ACCENT_ALPHA_BOOST = 1.8;          // clSkin.ACCENT_ALPHA_BOOST: red holds about half of amber's luminance

export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  const s = mx === 0 ? 0 : d / mx;
  let h = 0;
  if (d !== 0) {
    if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h = ((h / 6) % 1 + 1) % 1;
  }
  return [h, s, mx];
}
export function hsvToRgb(h, s, v) {
  h = ((h % 1) + 1) % 1;
  const i = Math.floor(h * 6), f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  return [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
}

export function isAccent(r, g, b) {
  const [h, s, v] = rgbToHsv(r, g, b), hue = h * 360;
  return s >= 0.06 && v >= 0.28 && hue >= 10 && hue <= 60 && s >= 0.59;
}

export function ultronRgb(r, g, b) {
  const [h, s, v] = rgbToHsv(r, g, b), hue = h * 360;
  let out;
  if (s < 0.06) return [r, g, b];                                              // white, grey, black
  if (v < 0.28 && (hue <= 60 || hue >= 345)) out = hsvToRgb(235 / 360, 0.18, 0.03 + 0.6 * v);   // dark brown -> cold near-black
  else if (hue >= 10 && hue <= 60 && s >= 0.59) {                              // orange / amber accent -> crimson, hotter as it was yellower
    const t = (hue - 10) / 50;
    out = hsvToRgb(((355 + 13 * t) % 360) / 360, 1 - 0.28 * t, v);
  } else if (hue >= 10 && hue <= 60) out = hsvToRgb(215 / 360, Math.min(0.10, s * 0.5), v * 0.92);   // cream / warm grey -> steel
  else return [r, g, b];
  return out.map((c) => Math.round(c * 255));
}

let active = "jarvis";
const mapRgb = (r, g, b) => (active === "ultron" ? ultronRgb(r, g, b) : [r, g, b]);
const boost = (a, max) => Math.min(max, a * ACCENT_ALPHA_BOOST);

/** rgba() for a source colour (0-255 channels) and a 0-1 alpha, recoloured by the active skin (accents get the opaque boost). */
export function skinRgba(r, g, b, a = 1) {
  if (active !== "ultron") return `rgba(${r},${g},${b},${a})`;
  const [R, G, B] = ultronRgb(r, g, b);
  return `rgba(${R},${G},${B},${isAccent(r, g, b) ? Math.round(boost(a, 1) * 1000) / 1000 : a})`;
}

/** Recolours every rgb()/rgba()/#rrggbb in a CSS colour string (clSkin.recolor_css, for strings built in JS). */
export function skinCss(css) {
  if (active !== "ultron" || !css) return css;
  return css
    .replace(/(?<![\w])#([0-9a-fA-F]{6})\b/g, (_, hex) => {
      const [r, g, b] = ultronRgb(parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16));
      return "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");
    })
    .replace(/rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+%?)\s*)?\)/g, (_, r0, g0, b0, alpha) => {
      const [r, g, b] = ultronRgb(Math.min(255, +r0), Math.min(255, +g0), Math.min(255, +b0));
      if (alpha === undefined) return `rgb(${r}, ${g}, ${b})`;
      if (isAccent(Math.min(255, +r0), Math.min(255, +g0), Math.min(255, +b0))) {
        if (alpha.endsWith("%")) alpha = `${Math.round(boost(parseFloat(alpha), 100))}%`;
        else if (alpha.includes(".")) alpha = boost(parseFloat(alpha), 1).toFixed(2);
        else alpha = String(Math.round(boost(parseFloat(alpha), 255)));
      }
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    });
}

// The orb's painter palette, written in JARVIS orange; every colour goes through the skin when it is asked for.
const WAVES = [[210, 90, 0, 230], [255, 150, 0, 230], [255, 195, 70, 230]];       // one orange hue at three lightnesses
export const palette = () => ({
  bloom: (t, a) => skinRgba(255, Math.round(200 - 130 * t), 0, a),
  ring1: (a) => skinRgba(255, 180, 0, a), ring2: (a) => skinRgba(255, 120, 0, a),
  waves: WAVES.map(([r, g, b, a]) => { const [R, G, B] = mapRgb(r, g, b); return [R, G, B, active === "ultron" && isAccent(r, g, b) ? Math.min(255, a * ACCENT_ALPHA_BOOST) : a]; }),
  waveGlow: [10, 6, 3],                                                          // glow strongest on the loudest wave
  particle: (a) => skinRgba(255, 200, 50, a),
});

const SKINS = ["jarvis", "ultron"];
export const activeSkin = () => active;
export function setSkin(name) {
  active = SKINS.includes(name) ? name : "jarvis";
  document.documentElement.dataset.skin = active;
  window.dispatchEvent(new CustomEvent("skin-changed", { detail: active }));
}
