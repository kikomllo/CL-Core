"""Tells an abort utterance ("abort", "cancel that", "hey jarvis stop") from a command that merely contains an abort word.

Matching `"cancel" in text` turned "cancel reminder and remind me in 10 minutes", "cancel all alarms" and "stop the
music" into aborts that did nothing, before any intent could see them. A phrase aborts only when everything in it is an
abort word or filler; any other content word means it is a command.
"""
import re
from typing import Iterable

_WORD = re.compile(r"[a-z0-9']+")

# Words that may surround an abort word without making it a command.
_FILLER = {
    "hey", "jarvis", "okay", "ok", "please", "just", "uh", "um", "the", "this", "that", "it", "now", "all", "everything",
    "sequence", "mission", "operation", "command", "task", "action", "process", "talking", "speaking", "listening",
    "there", "right", "thanks", "thank", "you", "for", "now",
}


def is_abort_utterance(text: str, keywords: Iterable[str]) -> bool:
    phrase = " ".join(_WORD.findall(text.lower())).replace("never mind", "nevermind")
    words = phrase.split()
    if not words:
        return False
    keyword_words = set()
    for kw in keywords:
        keyword_words.update(_WORD.findall(kw.lower().replace("never mind", "nevermind")))
    if not any(w in keyword_words for w in words):
        return False
    return all(w in keyword_words or w in _FILLER for w in words)
