// The colour wheel and the customisable colour swatches of the light colour picker: the maths, with no DOM (tests/test_webSwatches.py
// runs this under node). The wheel gives a hue and a saturation at full value: brightness is the picker's own slider, because a bulb is
// told its brightness separately (a darker rgb would also dim a WiZ bulb twice).

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** [r, g, b] (0-255 whole numbers) for a hue 0-360 and a saturation 0-1 at full value. */
export function hsvToRgb(h, s, v = 1) {
  h = ((h % 360) + 360) % 360; s = clamp(s, 0, 1); v = clamp(v, 0, 1);
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [r, g, b].map((t) => Math.round((t + m) * 255));
}

/** {h: 0-360, s: 0-1, v: 0-1} of an [r, g, b]. */
export function rgbToHsv([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: ((h * 60) + 360) % 360, s: max ? d / max : 0, v: max };
}

/** The colour under a point of the wheel: `dx`, `dy` are from the wheel's centre (y down), `radius` its radius. Hue goes round from the
 *  right (red) clockwise, saturation grows from the centre to the rim; a point beyond the rim is the rim's colour. */
export function wheelColour(dx, dy, radius) {
  const dist = Math.hypot(dx, dy), s = clamp(radius ? dist / radius : 0, 0, 1);
  const h = dist === 0 ? 0 : ((Math.atan2(dy, dx) * 180 / Math.PI) + 360) % 360;
  return { h, s, rgb: hsvToRgb(h, s) };
}

/** Where the knob sits for a hue and a saturation: offsets from the wheel's centre. */
export function knobOffset(h, s, radius) {
  const a = h * Math.PI / 180;
  return { x: Math.cos(a) * s * radius, y: Math.sin(a) * s * radius };
}

/** The swatches to show: the defaults [[name, rgb], ...] with the user's own colours (`saved` = {name: [r, g, b]}) put over them. */
export function mergeSwatches(defaults, saved) {
  const mine = saved && typeof saved === "object" ? saved : {};
  return defaults.map(([name, rgb]) => {
    const c = mine[name];
    const ok = Array.isArray(c) && c.length === 3 && c.every((v) => Number.isInteger(v) && v >= 0 && v <= 255);
    return { name, rgb: ok ? c : rgb, custom: ok };
  });
}

/** What a click on a swatch sends to the light: the voice colour's name while the swatch is the default, the rgb once it is the user's. */
export function swatchCommand(swatch, lum) {
  return swatch.custom ? { rgb: swatch.rgb, lum } : { color: swatch.name, lum };
}

/** Limits a live drag to one message per `ms`, the last colour always getting through: send(f) is called right away when it is
 *  free, otherwise the newest value waits for the end of the window. Returns {push(v), flush()}. */
export function liveSender(send, ms, now = () => Date.now(), later = (fn, d) => setTimeout(fn, d), cancel = (t) => clearTimeout(t)) {
  let last = -Infinity, waiting = null, timer = null;
  const fire = () => { timer = null; if (waiting !== null) { const v = waiting; waiting = null; last = now(); send(v); } };
  return {
    push(v) {
      waiting = v;
      const wait = last + ms - now();
      if (wait <= 0) { if (timer) { cancel(timer); timer = null; } fire(); } else if (!timer) timer = later(fire, wait);
    },
    flush() { if (timer) { cancel(timer); timer = null; } fire(); },
  };
}

// ---- the white temperature slider: it only means something while the bulb is white ----
// A bulb is in colour mode (hue and saturation) or in white mode (a temperature in kelvin); sending a temperature leaves colour mode. So the
// temperature slider is locked while the bulb is in colour mode, and a white swatch is what unlocks it.
export const WHITE_NAMES = ["warm white", "daylight", "coolwhite"];

/** Whether a light reported by the status is in white mode (it has a temperature and no colour). */
export const isWhiteLight = (light) => !!light && !light.rgb && !!light.kelvin;

/** The mode a light.set command leaves the bulb in: "white", "colour", or null when it does not change it (a brightness alone). */
export function modeAfter(fields) {
  if (!fields) return null;
  if (fields.temp !== undefined && fields.temp !== null) return "white";
  if (Array.isArray(fields.rgb)) return "colour";
  if (typeof fields.color === "string") return WHITE_NAMES.includes(fields.color) ? "white" : "colour";
  return null;
}

/** The same rules as src/utils/clColour.py. The brightness slider is the overall brightness; a colour's own value (its brightest channel as a fraction
 *  of 255, to the power gamma: the light a screen would give) scales it, because the darkness of a mix such as (0, 100, 0) is not part of a bulb's hue. */
export const valueFactor = (rgb, gamma = 1) => Math.pow(Math.max(...rgb) / 255, gamma);

/** The brightness (1-100) to send for an overall brightness `master` and a colour's `factor`. */
export const composeBrightness = (master, factor) => (factor >= 0.995 ? master : Math.max(1, Math.min(100, Math.round(master * factor))));
