"""Keeps a local copy of Spotify's up-next queue after one Web API fetch and
follows playback through it using only the free local media session -- so the
API is called again only when playback leaves the queue we expected."""
import threading
from typing import Any, Dict, List, Optional, Tuple

SAME = "same"
ADVANCED = "advanced"
DIVERGED = "diverged"
UNLOADED = "unloaded"


def _normalize(text: Optional[str]) -> str:
    return " ".join((text or "").casefold().split())


def _to_item(track: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "uri": track.get("uri"),
        "title": track.get("name") or "",
        "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
        "duration_ms": track.get("duration_ms") or 0,
    }


class QueueTracker:
    """Thread-safe: loaded from API-fetch worker threads, observed from the
    event loop."""

    # The local media session's duration can still be the previous track's
    # for a moment after a track change, so the title is the primary key and
    # the duration only breaks ties between same-titled tracks.
    DURATION_TOLERANCE_MS = 1500

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.loaded = False
        self.current: Optional[Dict[str, Any]] = None
        self.items: List[Dict[str, Any]] = []

    def load(self, queue_response: Dict[str, Any]) -> None:
        """queue_response: the Web API's GET /me/player/queue body."""
        with self._lock:
            now_playing = queue_response.get("currently_playing")
            self.current = _to_item(now_playing) if now_playing else None
            self.items = [_to_item(t) for t in queue_response.get("queue", []) if t]
            self.loaded = True

    @property
    def remaining(self) -> int:
        with self._lock:
            return len(self.items)

    def observe(self, title: Optional[str], duration_ms: Optional[int] = None) -> Tuple[str, int]:
        """Reports what the local media session says is playing. Returns
        (outcome, skipped):
          SAME      -- still the track we had (repeat, pause, a heartbeat)
          ADVANCED  -- it's the item at index `skipped` of our queue (0 = the
                       expected next track); everything up to it is dropped
          DIVERGED  -- not in our queue; the caller should refetch it
          UNLOADED  -- nothing fetched yet, nothing to track"""
        with self._lock:
            if not self.loaded:
                return UNLOADED, 0
            key = _normalize(title)
            if not key or (self.current and _normalize(self.current["title"]) == key):
                return SAME, 0

            candidates = [i for i, item in enumerate(self.items) if _normalize(item["title"]) == key]
            if not candidates:
                return DIVERGED, 0
            index = candidates[0]
            if duration_ms and len(candidates) > 1:
                index = next((i for i in candidates if abs(self.items[i]["duration_ms"] - duration_ms) <= self.DURATION_TOLERANCE_MS), index)

            self.current = self.items[index]
            self.items = self.items[index + 1:]
            return ADVANCED, index

    def snapshot(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.items[:limit]]
