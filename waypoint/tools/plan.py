"""Deterministic plan validation: the checks a plan must pass no matter what the model says.

A plan is written as text the model can produce reliably in a function call:
    "2026 Fall: MAT224H5, STA256H5; 2027 Winter: MAT301H5, MAT244H5"
Terms must be in chronological order. Checks:
- every course exists in the calendar data
- prerequisites are met by completed courses plus courses in earlier terms (not the same term)
- no course is planned twice or was already completed
- no exclusion conflicts (planning a course that excludes one taken or planned)
- at most 2.5 credits per term (UTM's full course load)
"""

import re
from collections.abc import Callable
from typing import Any

from waypoint.tools.catalog import Catalog, normalise_code
from waypoint.tools.prereqs import check_prerequisites

MAX_CREDITS_PER_TERM = 2.5


def parse_plan(plan: str) -> list[tuple[str, list[str]]]:
    terms = []
    for block in filter(None, (b.strip() for b in re.split(r"[;\n]", plan))):
        term, _, courses = block.partition(":")
        codes = [normalise_code(c) for c in re.split(r"[,\s]+", courses) if c.strip()]
        terms.append((term.strip(), codes))
    return terms


def validate_plan_text(plan: str, completed: list[str], catalog: Catalog) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    citations: set[str] = set()
    taken = {normalise_code(c) for c in completed}
    seen: set[str] = set()
    terms = parse_plan(plan)
    if not terms:
        return {"valid": False, "errors": ["Plan is empty or not in 'Term: CODE, CODE; Term: ...' form."]}
    for term, codes in terms:
        credits = 0.0
        planned_this_term: set[str] = set()
        for code in codes:
            rows = catalog.run("get_course", {"code": code})
            if not rows:
                errors.append(f"{term}: {code} is not in Waypoint's calendar data (check the code).")
                continue
            course = rows[0]
            full = course["code"]
            planned_this_term.add(full)
            if course.get("chunk_id"):
                citations.add(course["chunk_id"])
            credits += float(course["credits"])
            if full in seen or full in taken or full[:6] in taken:
                errors.append(f"{term}: {full} is already completed or planned earlier.")
            prereq = check_prerequisites(course.get("prerequisites") or "", sorted(taken | seen))
            if prereq.status == "unmet":
                errors.append(
                    f"{term}: prerequisites for {full} not met before this term (missing: "
                    f"{', '.join(prereq.missing)}). Requirement: {prereq.text}"
                )
            elif prereq.status == "unclear":
                warnings.append(f"{term}: confirm {full}'s prerequisite yourself: {prereq.text}")
            for excluded in course.get("exclusion_codes") or []:
                if excluded in taken or excluded in seen or excluded in planned_this_term:
                    errors.append(f"{term}: {full} is an exclusion of {excluded}; you can't take both for credit.")
        if credits > MAX_CREDITS_PER_TERM:
            errors.append(f"{term}: {credits} credits exceeds the {MAX_CREDITS_PER_TERM}-credit full load.")
        seen |= planned_this_term
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "terms": len(terms),
        "citations": sorted(citations),
    }


def make_plan_tools(catalog: Catalog) -> list[Callable[..., Any]]:
    def validate_plan(plan: str, completed_courses: list[str]) -> dict:
        """Check a term-by-term plan. plan format: "2026 Fall: MAT224H5, STA256H5; 2027 Winter: MAT301H5".
        completed_courses: codes the student has already passed. Returns valid=true/false, errors (unmet
        prerequisites, duplicates, exclusions, overloads) and warnings (conditions like minimum grades that
        the student must confirm). Fix every error and validate again."""
        return validate_plan_text(plan, completed_courses, catalog)

    return [validate_plan]
