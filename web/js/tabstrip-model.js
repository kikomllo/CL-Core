// A row of tab pills that is wider than its window (tests/test_webTabStrip.py runs this under node). It always scrolled sideways, but with
// the bar hidden and a mouse wheel that only scrolls vertically there was no way to reach the tabs past the edge, and no sign they existed.
// m = {scrollLeft, scrollWidth, clientWidth} of the strip.

/** The scrollLeft a wheel turn should set, or null to leave the event alone: not for a sideways wheel/trackpad (the browser already scrolls
 *  that), not when everything fits, and not at an end (so the page can still have the turn). A vertical wheel moves the strip sideways. */
export function wheelScroll({ deltaX, deltaY }, m) {
  if (m.scrollWidth <= m.clientWidth + 1 || Math.abs(deltaX) >= Math.abs(deltaY) || !deltaY) return null;
  const next = Math.max(0, Math.min(m.scrollLeft + deltaY, m.scrollWidth - m.clientWidth));
  return next === m.scrollLeft ? null : next;
}

/** Which edges have tabs hidden past them: where a fade is drawn to say "there is more". */
export const edges = (m) => ({ left: m.scrollLeft > 1, right: m.scrollLeft + m.clientWidth < m.scrollWidth - 1 });

/** The scrollLeft that brings an item into view (nearest edge, `pad` px of margin), or the current one if it already is.
 *  itemLeft is measured from the strip's visible left edge, as getBoundingClientRect differences give it. */
export function revealOffset(m, itemLeft, itemWidth, pad = 0) {
  const hidden = m.scrollWidth > m.clientWidth + 1;
  if (!hidden) return m.scrollLeft;
  let next = m.scrollLeft;
  if (itemLeft < pad) next += itemLeft - pad;
  else if (itemLeft + itemWidth > m.clientWidth - pad) next += itemLeft + itemWidth - (m.clientWidth - pad);
  return Math.max(0, Math.min(next, m.scrollWidth - m.clientWidth));
}
