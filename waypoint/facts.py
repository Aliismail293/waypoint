"""The fact route: questions about prerequisites, exclusions, credits, what a course unlocks, equivalents at other
campuses and program requirements are answered from structured data in code, with no retrieval and no agent loop.

    parse_fact_question(text, store)  -> FactQuery | None   (None: not a fact question, use the other routes)
    answer_fact(query, store, ...)    -> FactAnswer          (deterministic text, with [chunk_id] citations)

The text is already a complete, plain answer. In live mode one short model call may rephrase it more naturally, but
only from these facts, and the result is thrown away if it adds a course code or citation that wasn't in them
(see `rephrase_ok`). Offline it is used as written. Unknown means "check the calendar" with a link: never a guess.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from waypoint.datastore import CAMPUS_NAMES, CODE_IN_TEXT, DataStore, stem
from waypoint.prereq_eval import evaluate

MAX_LISTED = 12

_UNLOCKS = re.compile(
    r"\bunlock|lead(?:s)? to|\bopens? up\b|\bwhat (?:can|could|should) i take (?:after|next)\b|"
    r"\b(?:list|lists|listing|require|requires|required|need|needs) .{0,40}\bas a (?:pre-?req\w*|requirement)|"
    r"\bwhich courses? (?:need|require|depend)|\bdepends? on\b|\bwhat (?:courses? )?(?:needs?|requires?) \b",
    re.I,
)
_EXCLUSION = re.compile(r"\bexclusions?\b|\bexclude[sd]?\b|can'?t (?:take|count) both|same credit", re.I)
_EQUIVALENT = re.compile(
    r"\bequivalent|\bsame as\b|\bother campus|\bat (?:utsc|utm|utsg|scarborough|mississauga|st\.? george)\b|"
    r"\bcross-?campus|\bat u of t (?:scarborough|mississauga|st)",
    re.I,
)
_CREDITS = re.compile(r"how many credits|credit value|how much credit|\bworth\b.*credit|credit weight", re.I)
_PREREQ = re.compile(
    r"pre-?req|need before|required before|before (?:i|we) (?:can )?take|\bcan i (?:take|enrol|enroll|register)\b|"
    r"\bam i eligible|\bdo i (?:need|have)\b.*\b(?:before|first)\b|what (?:do|should) i need",
    re.I,
)
_OVERVIEW = re.compile(r"^\s*(?:what is|what's|tell me about|describe|about)\s+[A-Z]{3}", re.I)
_PROGRAM_WORDS = re.compile(r"\b(?:major|minor|specialist|program|requirements?)\b", re.I)
_TYPE_WORDS = {"major": "major", "minor": "minor", "specialist": "specialist", "focus": "focus"}


@dataclass
class FactQuery:
    # prerequisites | can_take | unlocks | exclusions | is_exclusion | equivalents | credits | overview | program
    kind: str
    codes: list[str] = field(default_factory=list)  # resolved course codes in the data
    unknown: list[str] = field(default_factory=list)  # codes mentioned but not in the data
    programs: list[str] = field(default_factory=list)  # program codes
    text: str = ""


@dataclass
class FactAnswer:
    text: str
    chunk_ids: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    kind: str = ""


def parse_fact_question(text: str, store: DataStore) -> FactQuery | None:
    codes = store.resolve(text)
    unknown = store.mentioned_unknown(text)
    mentioned = bool(CODE_IN_TEXT.search(text.upper()))
    if mentioned:
        if _EQUIVALENT.search(text):
            kind = "equivalents"
        elif _EXCLUSION.search(text):
            kind = "is_exclusion" if len({stem(c) for c in codes + unknown}) >= 2 else "exclusions"
        elif _UNLOCKS.search(text):
            kind = "unlocks"
        elif _CREDITS.search(text):
            kind = "credits"
        elif re.search(r"\bcan i (?:take|enrol|enroll|register)|\bam i eligible|\bdo i meet", text, re.I):
            kind = "can_take"
        elif _PREREQ.search(text):
            kind = "prerequisites"
        elif _OVERVIEW.search(text):
            kind = "overview"
        else:
            return None
        return FactQuery(kind, codes, unknown, text=text)
    if _PROGRAM_WORDS.search(text):
        programs = match_programs(text, store)
        if programs:
            return FactQuery("program", programs=[p["code"] for p in programs], text=text)
    return None


def match_programs(text: str, store: DataStore) -> list[dict[str, Any]]:
    """Programs whose name words and type (major/minor/...) all appear in the question. Never fuzzier than that."""
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    wanted_types = {t for w, t in _TYPE_WORDS.items() if w in words}
    hits = []
    for p in store.programs.values():
        name = re.sub(r"\(.*?\)", " ", p["name"]).lower()
        name_words = [w for w in re.findall(r"[a-z0-9]+", name) if w not in _TYPE_WORDS and w != "program"]
        if not name_words or not all(w in words for w in name_words):
            continue
        if wanted_types and p["type"] not in wanted_types:
            continue
        hits.append(p)
    return hits


def calendar_link(code: str) -> str:
    host = {"H5": "utm", "Y5": "utm", "H1": "artsci", "Y1": "artsci", "H3": "utsc", "Y3": "utsc"}.get(code[-2:])
    return f"https://{host}.calendar.utoronto.ca/course/{code}" if host else ""


def _cite(course: dict[str, Any], ids: list[str]) -> str:
    chunk = course.get("chunk_id")
    if not chunk:
        return ""
    if chunk not in ids:
        ids.append(chunk)
    return f" [{chunk}]"


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _campus(code: str) -> str:
    from waypoint.ingest.v3 import campus_of

    return CAMPUS_NAMES.get(campus_of(code) or "", "another campus")


def not_in_data(unknown: list[str]) -> str:
    links = "; ".join(f"{c} ({calendar_link(c)})" if calendar_link(c) else c for c in unknown)
    return (
        f"{_join(unknown)} isn't in Waypoint's data yet, so I can't say anything about it. "
        f"Check the official calendar: {links}."
    )


def answer_fact(
    query: FactQuery, store: DataStore, have: set[str] | None = None, highschool: bool = False
) -> FactAnswer:
    ids: list[str] = []
    if query.kind == "program":
        return _program(query, store, ids)
    if not query.codes:
        return FactAnswer(not_in_data(query.unknown), kind=query.kind) if query.unknown else FactAnswer("", kind="")
    if query.kind == "is_exclusion":
        return _is_exclusion(query, store, ids)
    parts: list[str] = []
    codes: list[str] = []
    for code in query.codes[:3]:
        course = store.courses[code]
        codes.append(code)
        handler = {
            "prerequisites": _prerequisites,
            "can_take": _can_take,
            "unlocks": _unlocks,
            "exclusions": _exclusions,
            "equivalents": _equivalents,
            "credits": _credits,
            "overview": _overview,
        }[query.kind]
        parts.append(handler(course, store, ids, have or set(), highschool))
    if query.unknown:
        parts.append(not_in_data(query.unknown))
    return FactAnswer("\n\n".join(parts), ids, codes, query.kind)


def _name(course: dict[str, Any]) -> str:
    return f"{course['code']} ({course['title']})"


def _prerequisites(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    from waypoint.ingest.prereq_parser import describe, is_fully_parsed

    tree = course["prereq_tree"]
    cite = _cite(course, ids)
    if tree is None:
        text = f"{_name(course)} lists no prerequisites{cite}."
    else:
        text = f"To take {_name(course)} you need: {describe(tree)}{cite}."
        if not is_fully_parsed(tree):
            text += " Part of that wording isn't something Waypoint can check, so read it on the calendar."
    if course.get("recommended_preparation"):
        text += f" Recommended preparation: {course['recommended_preparation']}"
    if course.get("coreq_tree"):
        text += f" Corequisite: {describe(course['coreq_tree'])}."
    if course.get("enrolment_notes"):
        text += f"\n\nEnrolment note: {course['enrolment_notes']}"
    return text


def _can_take(course: dict[str, Any], store: DataStore, ids: list[str], have: set[str], highschool: bool) -> str:
    result = evaluate(course["prereq_tree"], have, highschool)
    cite = _cite(course, ids)
    if not have:
        return (
            _prerequisites(course, store, ids)
            + "\n\nAdd the courses you've taken to your profile and I can check them."
        )
    if result.status in ("met", "none"):
        base = f"Yes, the courses in your profile meet the course prerequisites for {_name(course)}{cite}."
    elif result.status == "unmet":
        base = f"Not yet: for {_name(course)} you're still missing {_join(result.missing)}{cite}."
    else:
        base = (
            f"Nothing in your profile rules out {_name(course)}{cite}, but part of its requirement "
            f"can't be checked here: {_join(result.unchecked)}."
        )
    return base + " This is only the course prerequisite; the program may have its own admission requirements."


def _unlocks(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    code = course["code"]
    required = store.courses_requiring(code)
    cite = _cite(course, ids)
    if not required:
        return (
            f"No course in Waypoint's data lists {code} in its prerequisites{cite}. "
            "Waypoint only knows the campuses and subjects it has loaded, so check the calendar for others."
        )
    shown = required[:MAX_LISTED]
    for other in shown:
        _cite(store.courses[other], ids)
    more = f", and {len(required) - MAX_LISTED} more" if len(required) > MAX_LISTED else ""
    return f"These courses list {code} in their prerequisites: {', '.join(shown)}{more}{cite}."


def _exclusions(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    cite = _cite(course, ids)
    excluded = course["exclusion_codes"]
    if not excluded:
        return f"{_name(course)} lists no exclusions{cite}."
    return f"{_name(course)} can't be taken for credit with: {_join(excluded)}{cite}."


def _is_exclusion(query: FactQuery, store: DataStore, ids: list[str]) -> FactAnswer:
    asked = list(dict.fromkeys(query.codes + query.unknown))
    first, second = asked[0], asked[1]
    for a, b in ((first, second), (second, first)):
        course = store.get(a)
        if course and b in course["exclusion_codes"]:
            return FactAnswer(
                f"Yes: {b} is listed as an exclusion of {a}, so you can't take both for credit{_cite(course, ids)}.",
                ids,
                [a],
                "is_exclusion",
            )
    known = [c for c in (store.get(first), store.get(second)) if c is not None]
    if known:
        course = known[0]
        return FactAnswer(
            f"No: the calendar entry for {course['code']} doesn't list "
            f"{second if course['code'] == first else first} as an exclusion{_cite(course, ids)}.",
            ids,
            [course["code"]],
            "is_exclusion",
        )
    return FactAnswer(not_in_data(query.unknown), kind="is_exclusion")


def _equivalents(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    code = course["code"]
    linked = [c for c in store.equivalents(code) if c != code]
    cite = _cite(course, ids)
    if not linked:
        return f"{code}'s calendar entry names no course at another campus{cite}."
    by_campus = [f"{c} ({_campus(c)})" for c in linked]
    return (
        f"The calendar lists these courses as exclusions of {code}, which usually means they overlap with it: "
        f"{_join(by_campus)}{cite}. Whether you can enrol in another campus's course is decided by that campus "
        "and your own, so I can't promise eligibility: check the cross-campus enrolment rules on the official calendar."
    )


def _credits(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    plural = "" if course["credits"] == 1 else "s"
    return f"{_name(course)} is worth {course['credits']:g} credit{plural}{_cite(course, ids)}."


def _overview(course: dict[str, Any], store: DataStore, ids: list[str], *_: Any) -> str:
    first = re.split(r"(?<=[.!?])\s", (course.get("description") or "").strip(), maxsplit=1)[0]
    return (
        f"{_name(course)} is a {course['credits']:g}-credit course{_cite(course, ids)}. {first}\n\n"
        + _prerequisites(course, store, ids)
    )


def _program(query: FactQuery, store: DataStore, ids: list[str]) -> FactAnswer:
    parts: list[str] = []
    for code in query.programs[:2]:
        program = store.programs[code]
        need = program["requirements_tree"]["credits_required"]
        if need:
            amount = f"{need['min']:g}" + (f"-{need['max']:g}" if need["max"] != need["min"] else "")
            required = f"{amount} credits are required."
        else:
            required = "see the calendar for its credit total."
        parts.append(
            f"{program['name']} ({code}): {required} "
            "The checklist, with what you've done and what's missing, is in the Programs tab."
        )
        if program["admission_text"]:
            first = program["admission_text"].strip().splitlines()[0][:300]
            link = program["calendar_url"]
            parts.append(f"To get into the program (separate from course prerequisites): {first} Full text: {link}")
    return FactAnswer("\n\n".join(parts), ids, [], "program")


def rephrase_ok(original: str, rephrased: str) -> bool:
    """The model may reword the facts, never add to them: same course codes, no new bracket citations."""
    codes = set(CODE_IN_TEXT.findall(original.upper()))
    cites = set(re.findall(r"\[[^\]]+\]", original))
    return (
        bool(rephrased.strip())
        and set(CODE_IN_TEXT.findall(rephrased.upper())) <= codes
        and set(re.findall(r"\[[^\]]+\]", rephrased)) <= cites
    )
