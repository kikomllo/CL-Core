// The pills' hover label, ported from the Qt IconPill + MarqueeLabel (src/clUI.py, src/ui/clMarqueeLabel.py): the label opens to fit its text,
// capped at a width; a text longer than the cap is shown cut off with an ellipsis, held still for 0.8 s after the hover starts, then scrolls
// left at 1 px per 40 ms and comes back in from the right edge until the pointer leaves. The opening itself is CSS (style.css: 80 ms hover delay,
// 260 ms ease-out, like the Qt pill); this module measures the text and runs the scroll. The pure parts are tested (tests/test_webPill.py).

export const MARQUEE_DELAY_MS = 800;
export const MS_PER_PX = 40;

/** The width the label opens to, in px: the text's own width (+2 so sub-pixel rounding never clips the last letter), at most `cap`. */
export const labelWidth = (natural, cap) => Math.min(Math.ceil(natural) + 2, cap);

/** Does the text overflow the label it opens to? Only then does it scroll. */
export const needsMarquee = (natural, cap) => natural > cap + 1;

/** The two passes of the scroll. First the text slides out to the left from where it rests; after that it enters from the right edge of
 *  the box (`boxW` px away) and crosses it again, forever. Distances in px, durations in ms. */
export function marqueeFrames(textW, boxW) {
  return {
    first: { from: 0, to: -textW, duration: textW * MS_PER_PX },
    loop: { from: boxW, to: -textW, duration: (boxW + textW) * MS_PER_PX },
  };
}

/** Wires one pill: `pill` (the button), `label` (the clipped box that opens), `text` (the span inside it), `capPx()` (the widest it may open, now).
 *  Returns {refresh}: call it after the text changes so a pill that is open measures again. */
export function bindPillLabel(pill, label, text, capPx) {
  let timer = null, anims = [];
  const stop = () => { clearTimeout(timer); anims.forEach((a) => a.cancel()); anims = []; label.classList.remove("scrolling"); };

  function measure() {
    const natural = text.getBoundingClientRect().width, cap = capPx();       // a span's rect is its whole text even while the label clips it
    pill.style.setProperty("--lw", labelWidth(natural, cap) + "px");
    return { natural, cap };
  }

  function open() {
    stop();
    const { natural, cap } = measure();
    if (!needsMarquee(natural, cap)) return;
    timer = setTimeout(() => {
      const boxW = labelWidth(natural, cap) - 2, f = marqueeFrames(natural, boxW);
      label.classList.add("scrolling");
      const slide = (a) => text.animate([{ transform: `translateX(${a.from}px)` }, { transform: `translateX(${a.to}px)` }], { duration: a.duration, easing: "linear" });
      const first = slide(f.first);
      anims = [first];
      first.onfinish = () => { const loop = text.animate([{ transform: `translateX(${f.loop.from}px)` }, { transform: `translateX(${f.loop.to}px)` }], { duration: f.loop.duration, easing: "linear", iterations: Infinity }); anims = [loop]; };
    }, MARQUEE_DELAY_MS);
  }

  pill.addEventListener("pointerenter", open);
  pill.addEventListener("focus", open);                                       // the keyboard opens the label too
  pill.addEventListener("pointerleave", stop);
  pill.addEventListener("blur", stop);
  return { refresh: () => { if (pill.matches(":hover, :focus-visible")) open(); } };
}
