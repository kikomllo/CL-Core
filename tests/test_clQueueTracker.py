import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clQueueTracker import QueueTracker, SAME, ADVANCED, DIVERGED, UNLOADED


def _track(name, artists=("Artist",), duration_ms=200_000, uri=None):
    return {"name": name, "artists": [{"name": a} for a in artists], "duration_ms": duration_ms, "uri": uri or f"spotify:track:{name}"}


def _loaded(*upcoming, current="Now"):
    tracker = QueueTracker()
    tracker.load({"currently_playing": _track(current), "queue": [_track(n) if isinstance(n, str) else n for n in upcoming]})
    return tracker


class TestLoad:
    def test_normalizes_the_api_shape(self):
        tracker = QueueTracker()
        tracker.load({"currently_playing": _track("Now"), "queue": [_track("Duo", artists=("A", "B"), duration_ms=1234, uri="spotify:track:x")]})

        assert tracker.snapshot() == [{"uri": "spotify:track:x", "title": "Duo", "artist": "A, B", "duration_ms": 1234}]
        assert tracker.remaining == 1

    def test_nothing_playing_is_fine(self):
        tracker = QueueTracker()
        tracker.load({"currently_playing": None, "queue": []})

        assert tracker.loaded and tracker.remaining == 0

    def test_snapshot_is_limited_and_a_copy(self):
        tracker = _loaded("a", "b", "c")

        snap = tracker.snapshot(2)
        snap[0]["title"] = "mutated"

        assert [i["title"] for i in tracker.snapshot(2)] == ["a", "b"]


class TestObserve:
    def test_nothing_is_tracked_before_a_queue_was_fetched(self):
        assert QueueTracker().observe("Anything") == (UNLOADED, 0)

    def test_the_current_track_again_is_the_same(self):
        assert _loaded("a", "b").observe("Now") == (SAME, 0)

    def test_the_expected_next_track_advances_the_queue(self):
        tracker = _loaded("a", "b", "c")

        assert tracker.observe("a") == (ADVANCED, 0)
        assert [i["title"] for i in tracker.snapshot()] == ["b", "c"]
        assert tracker.observe("a") == (SAME, 0)  # now the current one

    def test_skipping_several_tracks_is_found_without_a_refetch(self):
        tracker = _loaded("a", "b", "c", "d")

        assert tracker.observe("c") == (ADVANCED, 2)
        assert [i["title"] for i in tracker.snapshot()] == ["d"]

    def test_a_track_outside_the_queue_is_a_divergence(self):
        assert _loaded("a", "b").observe("Something else") == (DIVERGED, 0)

    def test_matching_ignores_case_and_spacing(self):
        assert _loaded("FIM DO  NADA (feat. T-Rex)").observe("fim do nada (feat. t-rex)") == (ADVANCED, 0)

    def test_an_empty_title_is_not_a_change(self):
        assert _loaded("a").observe("") == (SAME, 0)

    def test_same_titled_tracks_are_told_apart_by_duration(self):
        tracker = _loaded(_track("Intro", duration_ms=100_000), _track("Intro", duration_ms=250_000))

        assert tracker.observe("Intro", 250_400) == (ADVANCED, 1)

    def test_a_stale_duration_does_not_break_a_unique_title(self):
        """Right after a track change the local duration can still be the old track's."""
        assert _loaded(_track("Unique", duration_ms=180_000)).observe("Unique", 999_000) == (ADVANCED, 0)

    def test_ties_with_no_matching_duration_take_the_first(self):
        tracker = _loaded(_track("Intro", duration_ms=100_000), _track("Intro", duration_ms=250_000))

        assert tracker.observe("Intro", 777_000) == (ADVANCED, 0)

    def test_reloading_replaces_everything(self):
        tracker = _loaded("a", "b")
        tracker.observe("a")

        tracker.load({"currently_playing": _track("z"), "queue": [_track("y")]})

        assert tracker.observe("z") == (SAME, 0)
        assert [i["title"] for i in tracker.snapshot()] == ["y"]


class TestJumpTo:
    """A click on a queued track: find it, then drop everything up to it."""

    def test_find_returns_the_index_of_a_uri(self):
        tracker = _loaded("a", "b", "c")

        assert tracker.find("spotify:track:c") == 2
        assert tracker.find("spotify:track:nope") is None
        assert tracker.find(None) is None

    def test_find_prefers_the_hinted_index_when_a_track_is_queued_twice(self):
        tracker = _loaded("a", "b", "a")

        assert tracker.find("spotify:track:a") == 0
        assert tracker.find("spotify:track:a", hint=2) == 2
        assert tracker.find("spotify:track:a", hint=1) == 0      # a wrong hint is ignored

    def test_skip_to_makes_it_current_and_drops_what_came_before(self):
        tracker = _loaded("a", "b", "c", "d")

        assert tracker.skip_to("spotify:track:c") is True

        assert tracker.current["title"] == "c"
        assert [t["title"] for t in tracker.snapshot()] == ["d"]

    def test_skip_to_is_idempotent_once_the_local_session_got_there_first(self):
        tracker = _loaded("a", "b", "c")
        tracker.observe("b")                                     # playback already advanced to b

        assert tracker.skip_to("spotify:track:b") is True

        assert [t["title"] for t in tracker.snapshot()] == ["c"]

    def test_skip_to_an_unknown_uri_changes_nothing(self):
        tracker = _loaded("a", "b")

        assert tracker.skip_to("spotify:track:zzz") is False

        assert tracker.remaining == 2 and tracker.current["title"] == "Now"
