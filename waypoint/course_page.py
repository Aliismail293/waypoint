"""Data for the Course page: calendar fields, prerequisites checked against the student's profile, sample sections.

Calendar fields come straight from the ingested record; anything missing is returned as None so the page shows an
explicit empty state, never a guess. Sections come from the synthetic sample timetable and are always labelled.
"""

from typing import Any

from waypoint.mcp.timetable.adapter import TimetableAdapter
from waypoint.profile import Profile
from waypoint.tools.catalog import Catalog, normalise_code
from waypoint.tools.prereqs import check_prerequisites

TERMS = ("2026 Fall", "2027 Winter")


def _clean(value: Any) -> Any:
    return value if value not in ("", None, []) else None


def course_page(catalog: Catalog, timetable: TimetableAdapter, profile: Profile, code: str) -> dict[str, Any] | None:
    rows = catalog.run("get_course", {"code": normalise_code(code)})
    exact = [r for r in rows if r["code"] == normalise_code(code)]
    if not exact:
        return None
    course = exact[0]
    status = {c.code: c.status for c in profile.courses}
    done = profile.codes("done")

    check = check_prerequisites(course.get("prerequisites") or "", done)
    required = [
        {"code": req, "status": status.get(req) or "missing", "in_data": bool(catalog.run("get_course", {"code": req}))}
        for req in course.get("prerequisite_codes") or []
    ]
    unlocks = [
        {"code": r["code"], "title": r["title"]} for r in catalog.run("courses_unlocked_by", {"code": course["code"]})
    ]

    sections = []
    for term in TERMS:
        for s in timetable.search_sections(course["code"], term):
            sections.append(s.model_dump())

    return {
        "code": course["code"],
        "title": course["title"],
        "credits": course.get("credits"),
        "level": course.get("level"),
        "status": status.get(course["code"]),  # done | now | None
        "calendar": {
            "description": _clean(course.get("description")),
            "prerequisites": _clean(course.get("prerequisites")),
            "corequisites": _clean(course.get("corequisites")),
            "exclusions": _clean(course.get("exclusions")),
            "recommended_preparation": _clean(course.get("recommended_preparation")),
            "distribution": _clean(course.get("distribution")),
            "delivery": _clean(course.get("delivery")),
            "source_url": course.get("source_url"),
            "chunk_id": course.get("chunk_id"),
        },
        "prerequisite_check": {
            "status": check.status,  # met | unmet | unclear (conditions like grades need a human)
            "missing": check.missing,
            "text": check.text,
            "required": required,
        },
        "unlocks": unlocks,
        "sections": sections,
        "sections_source": timetable.source.label,
        "suggested_questions": [
            f"What are the prerequisites for {course['code']}?",
            f"What does {course['code']} unlock?",
            f"Is {course['code']} offered this winter, and when do the sections meet?",
        ],
    }
