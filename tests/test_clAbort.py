import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from utils.clAbort import is_abort_utterance

KEYWORDS = ["abort", "cancel", "nevermind", "stop"]


class TestAbortUtterance:
    @pytest.mark.parametrize("text", [
        "abort", "Abort.", "cancel", "cancel that", "Hey Jarvis, abort.", "hey jarvis stop", "stop it", "stop, please",
        "never mind", "Never mind.", "nevermind", "abort sequence", "cancel everything", "stop talking",
        "okay jarvis cancel that please", "ABORT ABORT",
    ])
    def test_a_bare_abort_is_an_abort(self, text):
        assert is_abort_utterance(text, KEYWORDS) is True

    @pytest.mark.parametrize("text", [
        "Hey Jarvis, cancel reminder and remind me in 10 minutes.",     # the live log line that did nothing
        "cancel reminder", "cancel all alarms", "cancel my alarm", "stop the music", "stop the timer",
        "cancel the 7 am alarm", "abort the timer and set an alarm for six", "I don't want to cancel the lights",
    ])
    def test_a_command_that_contains_an_abort_word_is_not_an_abort(self, text):
        assert is_abort_utterance(text, KEYWORDS) is False

    @pytest.mark.parametrize("text", ["stopwatch", "forecast", "remind me in 5 minutes", "", "   ", "...", "hey jarvis"])
    def test_no_abort_word_means_no_abort(self, text):
        assert is_abort_utterance(text, KEYWORDS) is False

    def test_a_keyword_inside_another_word_does_not_count(self):
        assert is_abort_utterance("stopwatch", KEYWORDS) is False       # the old substring match said yes
        assert is_abort_utterance("scancel", KEYWORDS) is False

    def test_it_follows_the_configured_keywords(self):
        assert is_abort_utterance("halt", ["halt"]) is True
        assert is_abort_utterance("abort", ["halt"]) is False
        assert is_abort_utterance("cancel", []) is False
