"""Source reader helpers: find the line of a calendar chunk that backs a claim in an answer.

The highlight is computed, never invented: the chunk's own lines are scored by how many of the claim's meaningful
words (and course codes) they contain, and the best line is returned as a character span of the chunk text.
No overlap means no highlight, and the UI says so.
"""

import re
from dataclasses import dataclass

WORD = re.compile(r"[a-z0-9]+")
CODE = re.compile(r"\b[A-Z]{3}\d{3}[HY]\d\b")
STOPWORDS = frozenset(
    "the and for are you your with that this from into not any all has have had was were will can may must "
    "does did how what when where which who whose than then them they its it's out per via but also each".split()
)


@dataclass
class Passage:
    start: int
    end: int
    matched: list[str]


def _terms(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOPWORDS}


def locate_passage(text: str, claim: str) -> Passage | None:
    """Best-matching line of `text` for `claim`, as (start, end) offsets into `text`; None if nothing matches."""
    claim_terms = _terms(claim)
    claim_codes = {c.lower() for c in CODE.findall(claim)}
    if not claim_terms:
        return None
    best: tuple[float, int, int, list[str]] | None = None
    offset = 0
    for line in text.split("\n"):
        start, end = offset, offset + len(line)
        offset = end + 1
        if not line.strip():
            continue
        shared = sorted(claim_terms & _terms(line))
        codes = sum(1 for c in claim_codes if c in line.lower())
        score = len(shared) + 2 * codes
        if score and (best is None or score > best[0]):
            best = (score, start, end, shared)
    if best is None:
        return None
    score, start, end, shared = best
    # A single shared common word is not evidence of support.
    if len(shared) < 2 and not any(c in text[start:end].lower() for c in claim_codes):
        return None
    return Passage(start, end, shared[:6])
