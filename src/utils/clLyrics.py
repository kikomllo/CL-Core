"""Pure helpers for finding lyrics: cleaning titles, picking the best lrclib
record, estimating line times for lyrics that have none, and a small on-disk
cache. The network calls themselves live in clSpotify.py's SpotifyManager."""
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# Spotify titles carry credits and edition tags the lyric databases usually
# don't: "Song (feat. X)", "Song (with Y)", "Song - Remastered 2011".
_BRACKETED_CREDIT = re.compile(r"\s*[\(\[][^)\]]*\b(feat\.?|ft\.?|featuring|with|remaster(ed)?|version|edit|live|radio)\b[^)\]]*[\)\]]", re.I)
_TRAILING_EDITION = re.compile(r"\s*-\s*(remaster(ed)?( \d{4})?|\d{4} remaster(ed)?|single version|radio edit|live\b.*)$", re.I)

DURATION_TOLERANCE_SECONDS = 4
MISS_TTL_SECONDS = 7 * 24 * 3600


def clean_title(title: str) -> str:
    cleaned = _TRAILING_EDITION.sub("", _BRACKETED_CREDIT.sub("", title or "")).strip()
    return cleaned or (title or "").strip()


def title_variants(title: str) -> List[str]:
    """The title as given, then without credits/edition tags (if different)."""
    return list(dict.fromkeys([title, clean_title(title)]))


def first_artist(artist: str) -> str:
    """The local media session usually reports just the primary artist, but
    a joined list ("A, B" / "A & B") can come through too."""
    return re.split(r"\s*(?:,|&|;| feat\.? | x )\s*", artist or "", maxsplit=1, flags=re.I)[0].strip() or (artist or "")


def pick_search_result(results: List[Dict[str, Any]], duration: float) -> Dict[str, Any]:
    """From lrclib /api/search results, the closest-duration record within
    tolerance, preferring one with synced lyrics. {} when nothing fits."""
    fits = [r for r in results or [] if abs((r.get("duration") or 0) - duration) <= DURATION_TOLERANCE_SECONDS]
    fits.sort(key=lambda r: (not r.get("syncedLyrics"), not r.get("plainLyrics"), abs((r.get("duration") or 0) - duration)))
    return fits[0] if fits else {}


def estimate_line_times(plain: str, duration: float) -> List[Dict[str, Any]]:
    """Lines with no timing from the source get estimated ones: spread over
    the track after a short intro and before a short outro, each line's share
    proportional to its length. Approximate by nature (an instrumental break
    shifts everything after it) -- better than no lyrics, not a substitute for
    real synced ones."""
    lines = [l.strip() for l in (plain or "").splitlines()]
    lines = [l for l in lines if l and not re.fullmatch(r"[\[\(][^)\]]*[\]\)]", l)]  # drop blanks and [Hook]/[Verse] markers
    if not lines or duration <= 0:
        return []
    intro = min(8.0, duration * 0.06)
    outro = min(6.0, duration * 0.04)
    span = max(1.0, duration - intro - outro)
    weights = [max(1, len(l)) for l in lines]
    total = float(sum(weights))
    result, elapsed = [], 0.0
    for text, weight in zip(lines, weights):
        result.append({"time": round(intro + span * elapsed / total, 2), "text": text})
        elapsed += weight
    return result


def cache_key(title: str, artist: str, duration: float) -> str:
    raw = f"{(title or '').casefold().strip()}|{first_artist(artist).casefold()}|{round(duration)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class LyricsCache:
    """One small JSON file per track under data/lyrics_cache/. Entries with
    synced lyrics never expire; plain-only and misses are re-checked after
    MISS_TTL_SECONDS, or immediately when the set of providers changed (a
    newly enabled one deserves a look at everything that had no synced lyrics)."""

    def __init__(self, directory: str, miss_ttl: float = MISS_TTL_SECONDS):
        self.directory = directory
        self.miss_ttl = miss_ttl

    def _path(self, key: str) -> str:
        return os.path.join(self.directory, f"{key}.json")

    def get(self, key: str, providers: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        try:
            with open(self._path(key), "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict):
            return None
        if not entry.get("synced"):
            if time.time() - entry.get("fetched_at", 0) > self.miss_ttl:
                return None
            if providers is not None and entry.get("providers") != providers:
                return None
        return entry

    def put(self, key: str, synced: Optional[str], plain: Optional[str], source: str, providers: Optional[List[str]] = None) -> Dict[str, Any]:
        entry = {"synced": synced, "plain": plain, "source": source, "providers": providers, "fetched_at": time.time()}
        try:
            os.makedirs(self.directory, exist_ok=True)
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False)
            os.replace(tmp, self._path(key))
        except OSError:
            pass  # a cache that can't be written is just a cache miss next time
        return entry
