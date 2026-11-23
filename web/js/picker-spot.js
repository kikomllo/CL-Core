// Where a popover goes next to the control that opened it (tests/test_webPicker.py runs this under node): its left edge lines up with the
// control's, and it is kept inside the viewport; below the control when it fits, otherwise above it.

/** `anchor` = {left, top, right, bottom} of the control, `size` = {width, height} of the popover, `view` = {width, height} of the viewport. */
export function pickerSpot(anchor, size, view, margin = 8, gap = 6) {
  const left = Math.max(margin, Math.min(anchor.left, view.width - size.width - margin));
  const fitsBelow = anchor.bottom + size.height + gap * 2 <= view.height;
  const top = fitsBelow ? anchor.bottom + gap : Math.max(margin, anchor.top - size.height - gap);
  return { left, top };
}
