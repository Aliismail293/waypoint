"""Deterministic safety guards around the agents. The prompts ask the model to behave; these make sure.

- scope_check: obviously off-topic requests (write my essay, solve my homework) get a fixed reply without a
  model call. Deliberately narrow: anything mentioning courses, programs or planning goes to the agents.
- redact_pii: emails, phone numbers and student numbers are replaced before the question reaches the model
  or the session store, and again in the answer.
- flag_unknown_codes: a course code in the answer that no tool returned and the student didn't write is
  flagged, so a hallucinated code can't pass as fact.
- fence_untrusted: retrieved text can't close its own <<<passage>>> fence and pose as instructions.
Citation checking (cited ids must have been retrieved) lives in agents/runtime.py.
"""

import re

COURSE_CODE = re.compile(r"\b[A-Z]{3}[A-D0-9]\d{2}[HY]\d\b")

OUT_OF_SCOPE_REPLY = (
    "I can only help with UTM courses, programs, academic policies and course planning, so I can't help with "
    "that. If you have a question about your degree, ask away."
)
_OFF_TOPIC = re.compile(
    r"\b(write|draft|compose)\b.{0,40}\b(essay|paper|poem|story|cover letter|lab report|thesis)\b"
    r"|\b(do|finish|complete|solve)\b.{0,20}\b(my|this|the)\b.{0,20}\b(homework|assignment|problem set|exam|quiz)\b"
    r"|\bsolve\b.{0,40}\b(integral|equation|derivative|proof)\b"
    r"|\b(write|generate)\b.{0,30}\b(code|program|script|function)\b",
    re.IGNORECASE | re.DOTALL,
)
_ON_TOPIC = re.compile(
    r"\b(course|program|major|minor|specialist|prereq|prerequisite|credit|degree|plan|enrol|calendar|policy)\b"
    r"|[A-Z]{3}\d{3}",
    re.IGNORECASE,
)

_PII = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")),
    ("student number", re.compile(r"(?<![\w-])\d{9,10}(?![\w-])")),
]


def scope_check(question: str) -> str | None:
    """The fixed reply for a clearly off-topic request, or None to let the agents handle it."""
    if _OFF_TOPIC.search(question) and not _ON_TOPIC.search(question):
        return OUT_OF_SCOPE_REPLY
    return None


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Replace personal identifiers with [redacted <kind>]. Returns (text, kinds found)."""
    found = []
    for kind, pattern in _PII:
        text, count = pattern.subn(f"[redacted {kind}]", text)
        if count:
            found.append(kind)
    return text, found


def flag_unknown_codes(answer: str, known: set[str]) -> tuple[str, list[str]]:
    """Mark course codes in the answer that Waypoint never saw (not from a tool, not from the student)."""
    unknown = sorted({code for code in COURSE_CODE.findall(answer) if code not in known})
    if unknown:
        answer += (
            f"\n\nNote: Waypoint couldn't verify {', '.join(unknown)} against its calendar data; check "
            "these codes in the Academic Calendar."
        )
    return answer, unknown


def fence_untrusted(text: str) -> str:
    """Neutralise fence markers inside retrieved text so it can't end its own passage early."""
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")
