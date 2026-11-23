// The Live Logs terminal's pure logic (no DOM; tests/test_webLog.py runs it under node): the filter, and how a log line is split
// into the pieces a terminal would colour.

/** "spotify error -mqtt" -> {include: ["spotify", "error"], exclude: ["mqtt"]}: every included word must be in the line, no excluded
 *  word may be. Case-insensitive; a lone "-" is a plain character, not an exclusion. */
export function parseFilter(text) {
  const include = [], exclude = [];
  for (const word of String(text || "").toLowerCase().split(/\s+/).filter(Boolean)) {
    if (word.length > 1 && word.startsWith("-")) exclude.push(word.slice(1)); else include.push(word);
  }
  return { include, exclude };
}

export function matchLine(line, filter) {
  const low = line.toLowerCase();
  return filter.include.every((w) => low.includes(w)) && !filter.exclude.some((w) => low.includes(w));
}

/** [[text, matched]] segments of `text` with every occurrence of an included word marked (for highlighting). */
export function highlight(text, include) {
  const words = [...new Set(include.filter(Boolean))];
  if (!words.length) return [[text, false]];
  const low = text.toLowerCase(), hit = new Array(text.length).fill(false);
  for (const w of words) for (let i = low.indexOf(w); i !== -1; i = low.indexOf(w, i + w.length)) for (let k = i; k < i + w.length; k++) hit[k] = true;
  const out = [];
  for (let i = 0; i < text.length;) {
    let j = i; while (j < text.length && hit[j] === hit[i]) j++;
    out.push([text.slice(i, j), hit[i]]); i = j;
  }
  return out;
}

const ERR = /traceback|error|exception|critical|failed|fatal/i, WARN = /warn/i;
/** {stamp, tag, rest, kind}: "[04:51:15] [SPOTIFY] Command received" -> stamp "[04:51:15]", tag "SPOTIFY", rest "Command received";
 *  kind is "err" / "warn" / "" from the words in the line. Doubled timestamps (service + supervisor) are kept as one stamp. */
export function splitLine(line) {
  const m = /^((?:\[\d\d:\d\d:\d\d\]\s?)+)(.*)$/s.exec(line), stamp = m ? m[1].trim() : "", after = m ? m[2] : line;
  const t = /^\[([A-Za-z0-9 _-]{1,24})\]\s?(.*)$/s.exec(after);
  return { stamp, tag: t ? t[1] : "", rest: t ? t[2] : after, kind: ERR.test(line) ? "err" : WARN.test(line) ? "warn" : "" };
}

/** A stable palette slot 0..5 for a service tag, so [SPOTIFY] is always the same colour. */
export function tagSlot(tag) {
  let h = 0;
  for (const ch of tag) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 6;
}
