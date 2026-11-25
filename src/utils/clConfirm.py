"""Is a spoken answer a clear "yes"? For the questions that guard something that cannot be undone (powering the computer off).

Strict on purpose, like clAbort: the answer must be made of an affirmative word (or phrase) and filler, and must hold no negative word at all. A stray
"okay" or "sure" from a television, or "yes I think the weather is nice", never confirms; "yes", "yes please", "confirm", "yes shut it down", "do it" do."""
import re

_WORD = re.compile(r"[a-z']+")

AFFIRM_WORDS = {"yes", "yeah", "yep", "yup", "confirm", "confirmed", "affirmative", "proceed"}
AFFIRM_PHRASES = ("do it", "go ahead", "go for it")
NEGATIVE_WORDS = {"no", "nope", "nah", "not", "never", "dont", "don't", "cancel", "stop", "abort", "wait", "hold", "nevermind", "negative", "undo", "actually"}
# Words that may surround the answer without changing it ("yes, shut it down now, please").
FILLER = {
    "please", "jarvis", "hey", "sir", "now", "thanks", "thank", "you", "shut", "down", "it", "the", "computer", "pc", "that", "just", "do", "go", "ahead", "for",
    "power", "off", "turn", "really", "absolutely", "definitely", "certainly", "i", "am", "sure", "want", "to",
}


def is_affirmative(text: str) -> bool:
    words = _WORD.findall((text or "").lower().replace("never mind", "nevermind"))
    if not words or any(w in NEGATIVE_WORDS for w in words):
        return False
    phrase = " ".join(words)
    has_affirm = any(w in AFFIRM_WORDS for w in words) or any(p in phrase for p in AFFIRM_PHRASES)
    if not has_affirm:
        return False
    return all(w in AFFIRM_WORDS or w in FILLER for w in words)


# "Shut down the computer": an EXACT phrase, never fuzzy matching and never the SLM. The fast-path matcher is a fuzzy one built to be forgiving, and for a power-off
# that is the wrong way round: "shut down the heater" and "turn off the computer screen" both matched a shutdown intent there. So the daemon checks this before any
# matching, and only a whole utterance that is one of these forms counts; anything with an extra word ("the computer screen", "the computer light") is not one.
_THE = r"(?:my |the |this )?"
_PC = r"(?:computer|pc)"
_SHUTDOWN = re.compile(
    r"^(?:(?:hey |ok |okay )?jarvis[ ,]+)?(?:please |can you |could you )?"
    r"(?:(?:shut ?down|power off|power down|switch off|turn off) " + _THE + _PC
    + r"|shut " + _THE + _PC + r" down|power " + _THE + _PC + r" (?:off|down)|switch " + _THE + _PC + r" off|turn " + _THE + _PC + r" off)"
    r"(?: please| now)?$"
)


def is_shutdown_request(text: str) -> bool:
    return bool(_SHUTDOWN.match(re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()))
