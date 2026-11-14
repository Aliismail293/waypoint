"""Human-readable step labels for the live steps list (DESIGN_SPEC section 6): what Waypoint is doing, in words
a student understands. Labels never include the student's message text, only course codes and counts."""

import re
from typing import Any

COURSE = re.compile(r"[A-Za-z]{3}\d{3}(?:[HYhy]5)?")

SPECIALIST_LABELS = {
    "policy_agent": "Reading program requirements and policies",
    "course_data_agent": "Looking up course facts",
    "timetable_agent": "Looking up sections (sample data)",
    "planner_agent": "Drafting and checking a plan",
}


def _code(args: dict[str, Any]) -> str:
    for key in ("code", "course", "course_code"):
        value = args.get(key)
        if isinstance(value, str) and COURSE.fullmatch(value.strip()):
            return value.strip().upper()
    return ""


def running_label(tool: str, args: dict[str, Any]) -> str:
    code = _code(args)
    if tool == "search_calendar":
        return "Searching the Academic Calendar"
    if tool == "get_course":
        return f"Looking up {code}…" if code else "Looking up a course…"
    if tool == "check_course_prerequisites":
        return f"Checking prerequisites for {code}…" if code else "Checking prerequisites…"
    if tool == "courses_unlocked_by":
        return f"Finding what {code} unlocks…" if code else "Finding what a course unlocks…"
    if tool in ("get_program", "list_programs", "program_course_status"):
        return "Reading the program requirements"
    if tool in ("search_courses",):
        return "Searching the course catalog"
    if tool in ("search_sections", "find_sections", "get_section"):
        return "Looking up sections (sample data)"
    if tool in ("validate_plan", "check_plan"):
        return "Validating the plan"
    return SPECIALIST_LABELS.get(tool, "Working…")


def done_label(tool: str, args: dict[str, Any], result: Any) -> str:
    base = running_label(tool, args).rstrip("…")
    if tool == "search_calendar" and isinstance(result, dict):
        n = len(result.get("passages", []))
        return f"Searched the Academic Calendar · {n} match{'es' if n != 1 else ''}"
    if tool == "get_course":
        return base.replace("Looking up", "Looked up")
    if tool == "check_course_prerequisites":
        return base.replace("Checking", "Checked")
    if tool == "courses_unlocked_by":
        return base.replace("Finding", "Found")
    if tool in ("validate_plan", "check_plan"):
        return "Validated the plan"
    if tool in SPECIALIST_LABELS:
        return {
            "policy_agent": "Read program requirements and policies",
            "course_data_agent": "Looked up course facts",
            "timetable_agent": "Looked up sections (sample data)",
            "planner_agent": "Drafted and checked a plan",
        }[tool]
    if tool in ("get_program", "list_programs", "program_course_status"):
        return "Read the program requirements"
    if tool == "search_courses":
        return "Searched the course catalog"
    if tool in ("search_sections", "find_sections", "get_section"):
        return "Looked up sections (sample data)"
    return base
