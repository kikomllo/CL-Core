"""Lyrics from YouTube Music, with no login and no cookie: ytmusicapi's
unauthenticated search -> the song's watch page -> its lyrics tab, which
carries timed lines sourced from Musixmatch / LyricFind (measured live: it
had synced lyrics for several Portuguese rap tracks lrclib lacks).

Unofficial (Google's private web API, wrapped by a maintained library), so
every failure degrades to "no lyrics from this provider" and trips a
cool-down. A result is only accepted if its length and title/artist match the
track playing, so a similarly named song's lyrics are never shown."""
import logging
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from utils.clLyrics import clean_title, first_artist

DURATION_TOLERANCE_SECONDS = 5
SEARCH_LIMIT = 5
COOLDOWN_AFTER_ERROR_SECONDS = 60
COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS = 1800
REPEATED_ERRORS = 3


def _fold(text: Optional[str]) -> str:
    """Lower-case, accent-less, punctuation-less, single-spaced."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def pick_candidate(results: List[Dict[str, Any]], title: str, artist: str, duration: float) -> Optional[Dict[str, Any]]:
    """The search result that is the track playing, or None. All of: a song
    (not a video/episode), a length within tolerance, the playing title
    contained in the result's (or the reverse -- "É Tuga Ou Nada (feat. X)"),
    and the primary artist among the result's artists."""
    wanted_title = _fold(clean_title(title))
    wanted_artist = _fold(first_artist(artist))
    best, best_gap = None, None
    for result in results or []:
        if not result.get("videoId"):
            continue
        length = result.get("duration_seconds")
        if duration and length is not None and abs(length - duration) > DURATION_TOLERANCE_SECONDS:
            continue
        candidate_title = _fold(clean_title(result.get("title")))
        if not wanted_title or not candidate_title or not (wanted_title in candidate_title or candidate_title in wanted_title):
            continue
        artists = [_fold(a.get("name")) for a in result.get("artists") or []]
        if wanted_artist and not any(wanted_artist in a or a in wanted_artist for a in artists if a):
            continue
        gap = abs((length or duration) - duration) if duration else 0
        if best is None or gap < best_gap:
            best, best_gap = result, gap
    return best


def _lrc_stamp(milliseconds: int) -> str:
    centis = max(0, int(milliseconds)) // 10
    return f"[{centis // 6000:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}]"


def lyrics_to_texts(lyrics: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """(synced LRC, plain) from ytmusicapi's get_lyrics(..., timestamps=True):
    a list of timed lines becomes LRC (what the rest of the pipeline parses);
    a plain string stays plain."""
    body = (lyrics or {}).get("lyrics")
    if isinstance(body, str):
        return None, body.strip() or None
    if not isinstance(body, list):
        return None, None
    timed = [(getattr(line, "start_time", None), (getattr(line, "text", "") or "").strip()) for line in body]
    timed = [(start, text) for start, text in timed if start is not None and text]
    if not timed:
        return None, None
    synced = "\n".join(f"{_lrc_stamp(start)}{text}" for start, text in timed)
    plain = "\n".join(text for _, text in timed if text != "♪") or None
    return synced, plain


class YtMusicLyrics:
    """Blocking (ytmusicapi is synchronous): call find() from a worker thread."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory
        self._client = None
        self._lock = threading.Lock()
        self._errors_in_a_row = 0
        self._blocked_until = 0.0

    @property
    def blocked(self) -> bool:
        return time.time() < self._blocked_until

    def _get_client(self):
        with self._lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    from ytmusicapi import YTMusic  # imported lazily: an absent library must not break the service
                    self._client = YTMusic()
            return self._client

    def _failed(self, reason: str) -> None:
        self._errors_in_a_row += 1
        seconds = COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS if self._errors_in_a_row >= REPEATED_ERRORS else COOLDOWN_AFTER_ERROR_SECONDS
        self._blocked_until = time.time() + seconds
        logging.warning(f"YouTube Music lyrics paused for {seconds / 60:.0f} min: {reason}")

    def find(self, title: str, artist: str, duration: float) -> Tuple[Optional[str], Optional[str], bool]:
        """(synced LRC | None, plain | None, errored). errored = couldn't ask
        or it failed (don't cache as a miss); a clean "no lyrics" is
        (None, None, False)."""
        if self.blocked:
            return None, None, True
        try:
            client = self._get_client()
            query = f"{clean_title(title)} {first_artist(artist)}".strip()
            result = pick_candidate(client.search(query, filter="songs", limit=SEARCH_LIMIT), title, artist, duration)
            if result is None:
                self._errors_in_a_row = 0
                return None, None, False
            lyrics_id = (client.get_watch_playlist(videoId=result["videoId"]) or {}).get("lyrics")
            if not lyrics_id:
                self._errors_in_a_row = 0
                return None, None, False
            synced, plain = lyrics_to_texts(client.get_lyrics(lyrics_id, timestamps=True))
            self._errors_in_a_row = 0
            return synced, plain, False
        except Exception as e:
            self._failed(type(e).__name__)  # the type only: never echo request details
            return None, None, True
