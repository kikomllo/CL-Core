import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clLyrics import (
    LyricsCache, cache_key, clean_title, estimate_line_times, first_artist, pick_search_result, title_variants,
)


class TestCleanTitle:
    @pytest.mark.parametrize("raw, expected", [
        ("Ric Flair Drip (with Metro Boomin)", "Ric Flair Drip"),
        ("Back on the Road (feat. Lil Wayne)", "Back on the Road"),
        ("Song [Remastered 2011]", "Song"),
        ("Song - Remastered 2011", "Song"),
        ("Song - 2009 Remaster", "Song"),
        ("Song - Radio Edit", "Song"),
        ("Plain Title", "Plain Title"),
        ("FIM DO NADA (feat. T-Rex & Zara G)", "FIM DO NADA"),
    ])
    def test_strips_credits_and_edition_tags(self, raw, expected):
        assert clean_title(raw) == expected

    def test_keeps_a_title_that_is_only_a_bracket(self):
        assert clean_title("(feat. X)") == "(feat. X)"

    def test_keeps_brackets_that_are_part_of_the_name(self):
        assert clean_title("Song (Intro)") == "Song (Intro)"

    def test_variants_are_the_original_then_the_cleaned_one(self):
        assert title_variants("Song (feat. X)") == ["Song (feat. X)", "Song"]
        assert title_variants("Song") == ["Song"]


class TestFirstArtist:
    @pytest.mark.parametrize("raw, expected", [
        ("Mizzy Miles", "Mizzy Miles"),
        ("LON3R JOHNY, Plutonio", "LON3R JOHNY"),
        ("A & B", "A"),
        ("A feat. B", "A"),
        ("", ""),
    ])
    def test_keeps_only_the_primary_artist(self, raw, expected):
        assert first_artist(raw) == expected


class TestPickSearchResult:
    def test_prefers_synced_over_plain_among_matching_durations(self):
        results = [{"duration": 180, "plainLyrics": "p"}, {"duration": 181, "syncedLyrics": "[00:01.00]s"}]
        assert pick_search_result(results, 180.0)["syncedLyrics"]

    def test_ignores_records_with_a_very_different_duration(self):
        assert pick_search_result([{"duration": 400, "syncedLyrics": "[00:01.00]x"}], 180.0) == {}

    def test_takes_the_closest_duration_among_equals(self):
        results = [{"duration": 183, "syncedLyrics": "a"}, {"duration": 180, "syncedLyrics": "b"}]
        assert pick_search_result(results, 180.0)["syncedLyrics"] == "b"

    def test_nothing_in_nothing_out(self):
        assert pick_search_result([], 100.0) == {}
        assert pick_search_result(None, 100.0) == {}


class TestEstimateLineTimes:
    PLAIN = "Short\nA much longer line than the first one\n\n[Chorus]\nShort"

    def test_blank_lines_and_section_markers_are_dropped(self):
        assert [l["text"] for l in estimate_line_times(self.PLAIN, 200.0)] == ["Short", "A much longer line than the first one", "Short"]

    def test_times_increase_and_stay_inside_the_track(self):
        times = [l["time"] for l in estimate_line_times(self.PLAIN, 200.0)]
        assert times == sorted(times)
        assert times[0] > 0 and times[-1] < 200

    def test_the_first_line_waits_for_an_intro(self):
        assert estimate_line_times("One\nTwo", 200.0)[0]["time"] == pytest.approx(8.0)

    def test_the_intro_scales_down_for_a_short_track(self):
        assert estimate_line_times("One\nTwo", 50.0)[0]["time"] == pytest.approx(3.0)

    def test_longer_lines_get_more_time(self):
        lines = estimate_line_times("a\nbbbbbbbbbbbbbbbbbbbb\nc", 200.0)
        gap_after_short = lines[1]["time"] - lines[0]["time"]
        gap_after_long = lines[2]["time"] - lines[1]["time"]
        assert gap_after_long > gap_after_short

    def test_no_lyrics_or_no_duration_gives_nothing(self):
        assert estimate_line_times("", 200.0) == []
        assert estimate_line_times("One", 0) == []
        assert estimate_line_times("[Intro]\n\n", 200.0) == []


class TestLyricsCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return LyricsCache(str(tmp_path / "cache"), miss_ttl=3600)

    def test_a_stored_entry_comes_back(self, cache):
        cache.put("k", "[00:01.00]hi", "hi", "lrclib", ["lrclib"])
        entry = cache.get("k", ["lrclib"])
        assert entry["synced"] == "[00:01.00]hi" and entry["source"] == "lrclib"

    def test_an_unknown_key_is_a_miss(self, cache):
        assert cache.get("nope") is None

    def test_synced_entries_never_expire(self, cache, mocker):
        cache.put("k", "[00:01.00]hi", None, "lrclib", ["lrclib"])
        mocker.patch("utils.clLyrics.time.time", return_value=time.time() + 10 * 365 * 24 * 3600)
        assert cache.get("k", ["lrclib"]) is not None

    def test_plain_only_and_empty_entries_expire(self, cache, mocker):
        cache.put("plain", None, "text", "lrclib", ["lrclib"])
        cache.put("empty", None, None, "none", ["lrclib"])
        mocker.patch("utils.clLyrics.time.time", return_value=time.time() + 7200)
        assert cache.get("plain", ["lrclib"]) is None
        assert cache.get("empty", ["lrclib"]) is None

    def test_a_changed_provider_set_reopens_entries_without_synced_lyrics(self, cache):
        cache.put("k", None, "text", "lrclib", ["lrclib"])
        assert cache.get("k", ["lrclib"]) is not None
        assert cache.get("k", ["lrclib", "other"]) is None

    def test_a_changed_provider_set_does_not_reopen_synced_entries(self, cache):
        cache.put("k", "[00:01.00]hi", None, "lrclib", ["lrclib"])
        assert cache.get("k", ["lrclib", "other"]) is not None

    def test_a_corrupt_file_is_just_a_miss(self, cache):
        cache.put("k", "[00:01.00]hi", None, "lrclib", ["lrclib"])
        with open(os.path.join(cache.directory, "k.json"), "w") as f:
            f.write("{not json")
        assert cache.get("k") is None

    def test_an_unwritable_directory_does_not_raise(self, tmp_path):
        blocked = tmp_path / "file"
        blocked.write_text("x")
        cache = LyricsCache(str(blocked / "sub"))  # a file where the directory should be
        assert cache.put("k", None, None, "none")["source"] == "none"

    def test_unicode_survives_the_round_trip(self, cache):
        cache.put("k", None, "Ó vida, ação", "lrclib", ["lrclib"])
        assert cache.get("k", ["lrclib"])["plain"] == "Ó vida, ação"


class TestCacheKey:
    def test_ignores_case_and_surrounding_space(self):
        assert cache_key(" Song ", "ARTIST", 180.2) == cache_key("song", "artist", 180.2)

    def test_the_primary_artist_is_what_counts(self):
        assert cache_key("Song", "A, B", 180) == cache_key("Song", "A", 180)

    def test_a_different_length_is_a_different_version(self):
        assert cache_key("Song", "A", 180) != cache_key("Song", "A", 240)
