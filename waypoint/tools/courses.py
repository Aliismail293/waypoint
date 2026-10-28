"""Course-data tools for the agents. Each is a plain function with a docstring, which ADK turns into a
tool declaration the model can call. All of them go through the allow-listed Catalog queries."""

from collections.abc import Callable
from typing import Any

from waypoint.tools.catalog import Catalog, normalise_code
from waypoint.tools.prereqs import check_prerequisites


def make_course_tools(catalog: Catalog) -> list[Callable[..., Any]]:
    def get_course(code: str) -> dict:
        """Get one UTM course by code (e.g. "MAT224" or "MAT224H5"): title, description, credits,
        prerequisites, exclusions, distribution and the calendar URL."""
        rows = catalog.run("get_course", {"code": normalise_code(code)})
        if not rows:
            return {"found": False, "code": code, "note": "No such course in Waypoint's calendar data."}
        return {"found": True, **rows[0]}

    def search_courses(keyword: str, department: str = "") -> dict:
        """Search course titles and descriptions for a keyword (e.g. "probability"). Optionally limit to a
        department prefix such as "MAT", "STA" or "CSC"."""
        rows = catalog.run("search_courses", {"pattern": f"%{keyword.lower()}%", "department": department.upper()[:3]})
        return {"count": len(rows), "courses": rows}

    def courses_unlocked_by(code: str) -> dict:
        """List courses that name this course in their prerequisites (what taking it unlocks)."""
        rows = catalog.run("courses_unlocked_by", {"code": normalise_code(code)})
        return {"code": code, "count": len(rows), "courses": rows}

    def get_program(program: str) -> dict:
        """Get a program's enrolment and completion requirements by program code (e.g. "ERMAJ2511") or by
        part of its name (e.g. "Mathematics major")."""
        words = [w for w in program.lower().split() if w not in {"major", "minor", "specialist", "program", "in"}]
        pattern = "%" + "%".join(words) + "%" if words else "%"
        rows = catalog.run("get_program", {"code": program.strip().upper(), "pattern": pattern})
        return {"count": len(rows), "programs": rows}

    def list_programs() -> dict:
        """List the programs Waypoint has calendar data for."""
        return {"programs": catalog.run("list_programs", {})}

    def check_course_prerequisites(code: str, completed_courses: list[str]) -> dict:
        """Check whether a student who completed `completed_courses` meets the prerequisites for course
        `code`. Returns status met / unmet / unclear (unclear = conditions like grades that need a human)."""
        rows = catalog.run("get_course", {"code": normalise_code(code)})
        if not rows:
            return {"found": False, "code": code}
        result = check_prerequisites(rows[0].get("prerequisites") or "", completed_courses)
        return {
            "found": True,
            "code": rows[0]["code"],
            "status": result.status,
            "missing": result.missing,
            "prerequisite_text": result.text,
            "source_url": rows[0].get("source_url"),
        }

    return [get_course, search_courses, courses_unlocked_by, get_program, list_programs, check_course_prerequisites]
