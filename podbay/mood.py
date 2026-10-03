"""How the last prompts of a session read: calm, or heated. A lexicon, not
a model, so it costs no tokens and runs on every scan: swearing (English and
Finnish), shouting, question-and-exclamation clusters, and the words of a
third attempt ("still", "again", "why is this"). Newer prompts weigh more.
Crude by design; it catches the moment a conversation turns into a fight,
which is what HAL needs to know. See hal.py for what he says about it.
"""

from __future__ import annotations

import re

PROMPT_WINDOW = 6  # prompts the gauge looks at, newest last
HOT_AT = 1.0  # score at or above this is heated

_SWEAR_RE = re.compile(
    r"\b(fuck\w*|shit\w*|wtf|ffs|damn\w*|bloody|crap|bullshit|goddamn|"
    r"vittu\w*|perkele\w*|saatana\w*|helvetti\w*|paska\w*|jumalauta)\b",
    re.IGNORECASE,
)
_SHOUT_RE = re.compile(r"\b[A-ZÄÖÅ]{4,}\b")  # a word in capitals, four letters or more
_CLUSTER_RE = re.compile(r"(!\?|\?!|!!+|\?\?+)")
_RETRY_RE = re.compile(
    r"\b(still|again|why (?:is|does|did|the|on earth)|not working|doesn'?t work|broken|wrong again|stop)\b",
    re.IGNORECASE,
)

# What each hit is worth; a single swear word already makes a prompt heated
# on its own, the softer signals need company.
_WEIGHTS = (
    (_SWEAR_RE, 1.0),
    (_CLUSTER_RE, 0.5),
    (_SHOUT_RE, 0.4),
    (_RETRY_RE, 0.3),
)
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_URL_RE = re.compile(r"\S+://\S+")


def prompt_score(text: str) -> float:
    """Heat of one prompt. Code spans and URLs are left out: a command line
    shouts in capitals and a log says "broken" without anyone being upset."""
    text = _URL_RE.sub(" ", _CODE_RE.sub(" ", text))
    score = 0.0
    for regex, weight in _WEIGHTS:
        score += weight * len(regex.findall(text))
    return score


def score(prompts: list[str]) -> float:
    """Heat of the last PROMPT_WINDOW prompts, the newest counting in full
    and each older one half as much as the one after it."""
    recent = [p for p in prompts if p][-PROMPT_WINDOW:]
    total = 0.0
    weight = 1.0
    for text in reversed(recent):
        total += weight * prompt_score(text)
        weight /= 2
    return total


def is_hot(prompts: list[str]) -> bool:
    return score(prompts) >= HOT_AT
