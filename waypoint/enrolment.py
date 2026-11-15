"""Enrolment check: what to look at before enrolling in next term's planned courses. Replaces the sample timetable.

Per planned course: the calendar's enrolment notes and restrictions, exclusions against what the student has taken or
planned, courses at the other campuses that the calendar lists as exclusions ("equivalent" is never claimed), whether
the course is current or discontinued, and the program rules that touch it. "Offered this year" is always unknown:
no public source with usable terms is connected, and Waypoint says so rather than guess. Enrolling itself happens on
ACORN; Waypoint only produces a text list to search there and never connects to it.
"""

import re
from datetime import date
from typing import Any

from waypoint.datastore import CAMPUS_NAMES, DataStore
from waypoint.ingest.v3 import campus_of
from waypoint.plans import Plan, term_key
from waypoint.profile import Profile

OFFERED_UNKNOWN = "Not checked: Waypoint has no permitted public source for what is offered this year. Check ACORN."


def current_term(today: date | None = None) -> str:
    """The term a student is enrolling for now: the next Fall (Jun-Oct), Winter (Nov-Feb) or Summer (Mar-May)."""
    today = today or date.today()
    if 3 <= today.month <= 5:
        return f"{today.year} Summer"
    if 6 <= today.month <= 10:
        return f"{today.year} Fall"
    return f"{today.year + 1 if today.month >= 11 else today.year} Winter"


def pick_term(plans: list[Plan], term: str | None, today: date | None = None) -> str | None:
    """The requested term, else the earliest planned term at or after the current one, else the first planned term."""
    if term:
        return term
    terms = sorted({t.term for p in plans for t in p.terms if t.courses}, key=term_key)
    if not terms:
        return None
    wanted = term_key(current_term(today))
    return next((t for t in terms if term_key(t) >= wanted), terms[0])


def enrolment_check(store: DataStore, profile: Profile, plans: list[Plan], term: str | None) -> dict[str, Any]:
    term = pick_term(plans, term)
    if term is None:
        return {"term": None, "courses": [], "acorn": "", "message": "Plan some courses first, then check them here."}
    plan = plans[0]
    planned_this_term = next((t.courses for t in plan.terms if t.term == term), [])
    in_hand = set(profile.codes())
    planned_all = {c for t in plan.terms for c in t.courses}
    chosen_programs = set(profile.programs) | ({profile.program} if profile.program else set())
    rows: list[dict[str, Any]] = []
    for code in planned_this_term:
        course = store.get(code)
        if course is None:
            rows.append(
                {
                    "code": code,
                    "title": "",
                    "known": False,
                    "notes": [],
                    "conflicts": [],
                    "other_campuses": [],
                    "status": "unknown",
                    "offered": OFFERED_UNKNOWN,
                    "program_rules": [],
                    "calendar_url": "",
                }
            )
            continue
        conflicts = [
            f"{code} excludes {other}, which you {'have taken or are taking' if other in in_hand else 'have planned'}."
            for other in course["exclusion_codes"]
            if other != code and (other in in_hand or other in planned_all)
        ]
        elsewhere = [
            {"code": o, "campus": CAMPUS_NAMES.get(campus_of(o) or "", ""), "label": "listed as exclusion"}
            for o in store.equivalents(code)
            if campus_of(o) != course["campus"]
        ]
        program_rules = [
            r["source_text"]
            for p in sorted(chosen_programs)
            for r in store.rules.get(p, [])
            if r["rule_type"] == "manual_check" and code in r["source_text"]
        ]
        rows.append(
            {
                "code": code,
                "title": course["title"],
                "known": True,
                "notes": [course["enrolment_notes"]] if course["enrolment_notes"] else [],
                "conflicts": conflicts,
                "other_campuses": elsewhere,
                "status": course["status"],
                "offered": OFFERED_UNKNOWN,
                "program_rules": program_rules,
                "calendar_url": course["calendar_url"],
            }
        )
    return {
        "term": term,
        "courses": rows,
        "acorn": acorn_list(term, [r["code"] for r in rows if r["status"] != "discontinued"]),
        "cross_campus_note": (
            "Taking a course at another campus has its own rules; see the cross-campus enrolment information on the "
            "official calendar. Waypoint doesn't promise eligibility."
        ),
    }


def acorn_list(term: str, codes: list[str]) -> str:
    """Plain text, one course per line, to search for on ACORN. Waypoint never connects to ACORN."""
    stems = [re.sub(r"[HY]\d$", "", c) for c in codes]
    lines: list[str] = [f"Courses to search for on ACORN, {term}:"]
    lines += [f"- {code}  (search “{stem}”)" for code, stem in zip(codes, stems, strict=True)]
    lines.append("Check each section's enrolment controls on ACORN before enrolling.")
    return "\n".join(lines)
