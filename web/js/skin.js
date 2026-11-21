// Colour skins. The source palette is JARVIS orange; "ultron" recolours it (see src/clSkin.py for the full rules).
// CSS gets its colours from variables on :root; the canvas painters read this palette.
const JARVIS = {
  bloom: (t, a) => `rgba(255,${Math.round(200 - 130 * t)},0,${a})`,
  ring1: a => `rgba(255,180,0,${a})`, ring2: a => `rgba(255,120,0,${a})`,
  // one orange hue at three lightnesses (the pale line drifted toward yellow before), glow strongest on the loudest wave
  waves: [[210,90,0,230],[255,150,0,230],[255,195,70,230]], waveGlow: [10,6,3],
  particle: a => `rgba(255,200,50,${a})`,
};
const skins = { jarvis: JARVIS };
let active = "jarvis";
export const palette = () => skins[active] || JARVIS;
export function setSkin(name) { active = skins[name] ? name : "jarvis"; document.documentElement.dataset.skin = active; }
