import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils import clYtMusicLyrics as ym
from utils.clYtMusicLyrics import YtMusicLyrics, lyrics_to_texts, pick_candidate


def _song(title, artist, seconds, video_id="vid"):
    return {"videoId": video_id, "title": title, "artists": [{"name": artist}], "duration_seconds": seconds}


def _timed(*pairs):
    return {"lyrics": [SimpleNamespace(start_time=start, text=text) for start, text in pairs], "hasTimestamps": True}


class TestPickCandidate:
    def test_accepts_the_matching_song(self):
        assert pick_candidate([_song("GEIZA", "ProfJam", 136)], "GEIZA", "ProfJam", 136.2)["videoId"] == "vid"

    def test_a_different_length_is_rejected(self):
        """A live or extended version has different timings."""
        assert pick_candidate([_song("GEIZA", "ProfJam", 200)], "GEIZA", "ProfJam", 136.0) is None

    def test_a_different_title_is_rejected(self):
        assert pick_candidate([_song("Something Else", "ProfJam", 136)], "GEIZA", "ProfJam", 136.0) is None

    def test_a_different_artist_is_rejected(self):
        assert pick_candidate([_song("GEIZA", "Somebody Else", 136)], "GEIZA", "ProfJam", 136.0) is None

    def test_credits_in_either_title_do_not_get_in_the_way(self):
        assert pick_candidate([_song("É Tuga Ou Nada (feat. Sam The Kid)", "Sir Scratch", 209)], "Tuga Ou Nada", "Sir Scratch", 209.0)
        assert pick_candidate([_song("Back on the Road", "Cordae", 200)], "Back on the Road (feat. Lil Wayne)", "Cordae", 200.0)

    def test_accents_and_case_are_ignored(self):
        assert pick_candidate([_song("ação", "ProfJam", 100)], "ACAO", "profjam", 100.0)

    def test_the_primary_artist_only_has_to_be_among_the_credited(self):
        song = {"videoId": "v", "title": "Song", "artists": [{"name": "Other"}, {"name": "Wet Bed Gang"}], "duration_seconds": 100}
        assert pick_candidate([song], "Song", "Wet Bed Gang, Zara G", 100.0)

    def test_the_closest_length_wins_among_matches(self):
        results = [_song("Song", "A", 103, "far"), _song("Song", "A", 100, "near")]
        assert pick_candidate(results, "Song", "A", 100.0)["videoId"] == "near"

    def test_results_without_an_id_or_nothing_at_all_give_none(self):
        assert pick_candidate([{"title": "Song", "artists": [{"name": "A"}], "duration_seconds": 100}], "Song", "A", 100.0) is None
        assert pick_candidate([], "Song", "A", 100.0) is None
        assert pick_candidate(None, "Song", "A", 100.0) is None


class TestLyricsToTexts:
    def test_timed_lines_become_lrc_and_plain_text(self):
        synced, plain = lyrics_to_texts(_timed((0, "♪"), (9470, "Thank you, Jesus"), (125430, "Second")))

        assert synced == "[00:00.00]♪\n[00:09.47]Thank you, Jesus\n[02:05.43]Second"
        assert plain == "Thank you, Jesus\nSecond"  # the instrumental marker isn't a lyric

    def test_a_plain_string_stays_plain(self):
        assert lyrics_to_texts({"lyrics": "  Line one\nLine two  "}) == (None, "Line one\nLine two")

    def test_empty_text_and_missing_times_are_skipped(self):
        synced, _ = lyrics_to_texts(_timed((1000, ""), (None, "No time"), (2000, "Kept")))
        assert synced == "[00:02.00]Kept"

    @pytest.mark.parametrize("data", [None, {}, {"lyrics": None}, {"lyrics": []}, {"lyrics": 5}])
    def test_garbage_gives_nothing(self, data):
        assert lyrics_to_texts(data) == (None, None)


class _FakeYt:
    def __init__(self, results=None, lyrics_id="LYR", lyrics=None):
        self.results = [_song("GEIZA", "ProfJam", 136)] if results is None else results
        self.lyrics_id = lyrics_id
        self.lyrics = lyrics if lyrics is not None else _timed((9000, "Resurrect"))
        self.calls = []

    def search(self, query, filter=None, limit=None):
        self.calls.append(("search", query, filter))
        return self.results

    def get_watch_playlist(self, videoId=None):
        self.calls.append(("watch", videoId))
        return {"lyrics": self.lyrics_id}

    def get_lyrics(self, browse_id, timestamps=False):
        self.calls.append(("lyrics", browse_id, timestamps))
        return self.lyrics


def _provider(fake):
    return YtMusicLyrics(client_factory=lambda: fake)


class TestFind:
    def test_the_whole_flow_returns_synced_lyrics(self):
        fake = _FakeYt()

        synced, plain, errored = _provider(fake).find("GEIZA", "ProfJam", 136.0)

        assert synced == "[00:09.00]Resurrect" and plain == "Resurrect" and errored is False
        assert [c[0] for c in fake.calls] == ["search", "watch", "lyrics"]
        assert fake.calls[0][2] == "songs" and fake.calls[2][2] is True  # songs only, timed lyrics requested

    def test_the_query_uses_the_cleaned_title_and_the_primary_artist(self):
        fake = _FakeYt()

        _provider(fake).find("GEIZA (feat. X)", "ProfJam, Other", 136.0)

        assert fake.calls[0][1] == "GEIZA ProfJam"

    def test_plain_only_lyrics_come_back_as_plain(self):
        fake = _FakeYt(lyrics={"lyrics": "Just text"})
        assert _provider(fake).find("GEIZA", "ProfJam", 136.0) == (None, "Just text", False)

    def test_no_matching_result_is_a_clean_miss_and_asks_nothing_more(self):
        fake = _FakeYt(results=[_song("Other", "Someone", 300)])

        assert _provider(fake).find("GEIZA", "ProfJam", 136.0) == (None, None, False)
        assert [c[0] for c in fake.calls] == ["search"]

    def test_a_song_without_a_lyrics_tab_is_a_clean_miss(self):
        fake = _FakeYt(lyrics_id=None)

        assert _provider(fake).find("GEIZA", "ProfJam", 136.0) == (None, None, False)
        assert [c[0] for c in fake.calls] == ["search", "watch"]

    def test_the_client_is_created_once_and_only_when_needed(self):
        made = []

        def factory():
            made.append(1)
            return _FakeYt()
        provider = YtMusicLyrics(client_factory=factory)
        assert made == []

        provider.find("GEIZA", "ProfJam", 136.0)
        provider.find("GEIZA", "ProfJam", 136.0)

        assert made == [1]


class TestFailuresPause:
    class _Broken(_FakeYt):
        def search(self, *args, **kwargs):
            raise ConnectionError("down: https://secret.example/?token=abc")

    def test_an_exception_is_an_error_not_a_crash(self):
        provider = _provider(self._Broken())

        assert provider.find("GEIZA", "ProfJam", 136.0) == (None, None, True)
        assert provider.blocked is True

    def test_while_paused_nothing_is_asked(self):
        fake = self._Broken()
        provider = _provider(fake)
        provider.find("GEIZA", "ProfJam", 136.0)
        fake.calls.clear()

        assert provider.find("OTHER", "Artist", 100.0) == (None, None, True)
        assert fake.calls == []

    def test_it_resumes_after_the_pause(self, mocker):
        provider = _provider(self._Broken())
        provider.find("GEIZA", "ProfJam", 136.0)
        mocker.patch("utils.clYtMusicLyrics.time.time", return_value=time.time() + ym.COOLDOWN_AFTER_ERROR_SECONDS + 5)

        assert provider.blocked is False

    def test_repeated_failures_pause_for_much_longer(self, mocker):
        provider = _provider(self._Broken())
        now = time.time()
        for i in range(ym.REPEATED_ERRORS):
            mocker.patch("utils.clYtMusicLyrics.time.time", return_value=now + i * 100)
            provider.find("GEIZA", "ProfJam", 136.0)

        assert provider._blocked_until - (now + (ym.REPEATED_ERRORS - 1) * 100) == ym.COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS

    def test_a_success_resets_the_failure_count(self):
        provider = _provider(_FakeYt())
        provider._errors_in_a_row = 2

        provider.find("GEIZA", "ProfJam", 136.0)

        assert provider._errors_in_a_row == 0

    def test_the_log_never_carries_request_details(self, caplog):
        _provider(self._Broken()).find("GEIZA", "ProfJam", 136.0)

        assert "secret.example" not in caplog.text and "token=abc" not in caplog.text
        assert "ConnectionError" in caplog.text
