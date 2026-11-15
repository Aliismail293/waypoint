"""Evaluates a prerequisite tree (see ingest/prereq_parser.py) against what a student has.

Three-valued on purpose: a part we can't check (a grade, CGPA, permission, program enrolment, credits at a level, or
text we couldn't parse) is `unknown`, never guessed true or false. The result's `status`:

    met      every part is satisfied by the courses given
    unmet    a part is definitely not satisfied (the student lacks every option)
    unclear  nothing is definitely missing, but some part can't be checked here (we say which)
    none     the course lists no prerequisite
"""

from dataclasses import dataclass, field

from waypoint.ingest.prereq_parser import Node, describe

HIGH_SCHOOL_MATH = {"MHF4U", "MCV4U"}


@dataclass
class Evaluation:
    status: str  # met | unmet | unclear | none
    missing: list[str] = field(default_factory=list)  # courses the student lacks (from unsatisfied parts)
    unchecked: list[str] = field(default_factory=list)  # plain-language parts we can't check
    text: str = ""


def evaluate(tree: Node | None, have: set[str], highschool_done: bool = False) -> Evaluation:
    if tree is None:
        return Evaluation("none", text=describe(None))
    missing: list[str] = []
    unchecked: list[str] = []
    value = _eval(tree, have, highschool_done, missing, unchecked)
    status = "met" if value is True else "unmet" if value is False else "unclear"
    return Evaluation(status, [] if value is True else _dedupe(missing), _dedupe(unchecked), describe(tree))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _eval(tree: Node, have: set[str], hs_done: bool, missing: list[str], unchecked: list[str]) -> bool | None:
    if "all_of" in tree:
        results = [_eval(c, have, hs_done, missing, unchecked) for c in tree["all_of"]]
        if False in results:
            return False
        return None if None in results else True
    if "one_of" in tree:
        sub_missing: list[str] = []
        results = [_eval(c, have, hs_done, sub_missing, unchecked) for c in tree["one_of"]]
        if True in results:
            return True
        missing += sub_missing
        return None if None in results else False
    if "course" in tree:
        code = tree["course"]
        if code in have:
            if "min_grade" in tree:
                unchecked.append(f"a minimum grade of {tree['min_grade']}% in {code}")
                return None
            return True
        missing.append(code)
        return False
    if "highschool" in tree:
        if hs_done and tree["highschool"] in HIGH_SCHOOL_MATH:
            return True
        unchecked.append(
            f"Grade 12 {tree['highschool']}" + (f" (minimum {tree['min_grade']}%)" if "min_grade" in tree else "")
        )
        return None
    unchecked.append(describe(tree))
    return None
