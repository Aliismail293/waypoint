"""Builds the V3 corpus (data/corpus/v3/*.jsonl) from whatever pages the adapters find in their raw cache.

Tables written (all carry `institution_id`, default `uoft`):
    institutions, campuses, subjects, courses, course_equivalents, programs, program_rules
`policy_chunks` stay in data/corpus/calendar_chunks.jsonl (the existing pgvector path).

Nothing here fetches. Raw pages come from data/raw/uoft/<campus>/ (git-ignored); a campus with no cached pages
simply contributes no rows, and `coverage()` says so.
"""

import json
import re
from pathlib import Path
from typing import Any

from waypoint.ingest.adapters.base import SourceAdapter
from waypoint.ingest.adapters.uoft import ALL_ADAPTERS
from waypoint.ingest.calendar_parser import COURSE_CODE
from waypoint.ingest.fetch import BLOCK_MARKER
from waypoint.ingest.prereq_parser import course_leaves, is_fully_parsed, parse_prereq
from waypoint.ingest.program_parser import parse_requirements, program_rules

V3_DIR = Path("data/corpus/v3")
CAMPUS_BY_SUFFIX = {"H5": "utm", "Y5": "utm", "H1": "utsg", "Y1": "utsg", "H3": "utsc", "Y3": "utsc"}
YEAR_RE = re.compile(r"\b(20\d\d)\s*-\s*(20\d\d)\b")


def campus_of(code: str) -> str | None:
    return CAMPUS_BY_SUFFIX.get(code[-2:])


def course_row(course: Any, adapter: SourceAdapter) -> dict[str, Any]:
    base = course.to_row()
    prereq_tree = parse_prereq(course.prerequisites)
    coreq_tree = parse_prereq(course.corequisites)
    enrolment = base["other"].split("; ")[0] if "Enrolment Limits" in base["other"] else ""
    return {
        **base,
        "institution_id": adapter.info.institution_id,
        "campus": adapter.info.campus,
        "subject_code": base["department"],
        "raw_prereq_text": course.prerequisites,
        "prereq_tree": prereq_tree,
        "coreq_tree": coreq_tree,
        "prereq_parsed": is_fully_parsed(prereq_tree),
        "enrolment_notes": enrolment.removeprefix("Enrolment Limits:").strip(),
        "breadth": course.distribution,
        "calendar_url": course.source_url,
        "calendar_year": None,  # the page text doesn't state a year we can read reliably; never guessed
        "status": "current",
    }


def equivalents(courses: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Exclusion lists that name a course at another campus. Labelled as listed exclusions, not 'equivalent'."""
    seen: set[tuple[str, str]] = set()
    rows = []
    for c in courses:
        for other in c["exclusion_codes"]:
            if campus_of(other) and campus_of(other) != c["campus"]:
                pair = tuple(sorted((c["code"], other)))
                if pair not in seen:
                    seen.add((pair[0], pair[1]))
                    rows.append({"code_a": pair[0], "code_b": pair[1], "source": "listed as exclusion"})
    return rows


def program_row(program: Any, adapter: SourceAdapter) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requirements = parse_requirements(program.completion_requirements)
    year = YEAR_RE.search(program.enrolment_requirements + program.completion_requirements)
    row = {
        "institution_id": adapter.info.institution_id,
        "campus": adapter.info.campus,
        "code": program.code,
        "name": program.name,
        "type": program.program_type.lower(),
        "admission_text": program.enrolment_requirements,
        "requirements_tree": requirements,
        "raw_text": program.completion_requirements,
        "calendar_url": program.source_url,
        "calendar_year": f"{year.group(1)}-{year.group(2)}" if year else None,
        "course_codes": program.course_codes,
        "area": program.area,
    }
    return row, program_rules(program.code, requirements, program.source_url)


def build(adapters: list[SourceAdapter] | None = None) -> dict[str, list[dict[str, Any]]]:
    adapters = adapters or [make() for make in ALL_ADAPTERS]
    tables: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("institutions", "campuses", "subjects", "courses", "course_equivalents", "programs")
    }
    tables["program_rules"] = []
    tables["institutions"].append({"id": "uoft", "name": "University of Toronto"})
    subject_names: dict[tuple[str, str], str] = {}
    for adapter in adapters:
        info = adapter.info
        tables["campuses"].append(
            {"institution_id": info.institution_id, "code": info.campus, "name": info.campus_name}
        )
        for page in adapter.list_course_pages():
            for course in adapter.parse_course(page):
                tables["courses"].append(course_row(course, adapter))
                subject_names.setdefault((info.campus, course.code[:3]), course.area)
        for page in adapter.list_programs():
            for program in adapter.parse_program(page):
                row, rules = program_row(program, adapter)
                tables["programs"].append(row)
                tables["program_rules"] += rules
    for (campus, code), area in sorted(subject_names.items()):
        # The calendar names the *section* (e.g. "Mathematical Sciences"), which can cover several subject codes.
        tables["subjects"].append({"institution_id": "uoft", "campus": campus, "code": code, "name": area})
    tables["course_equivalents"] = equivalents(tables["courses"])
    return tables


def coverage(tables: dict[str, list[dict[str, Any]]], adapters: list[SourceAdapter] | None = None) -> list[dict]:
    """Per campus: courses, subjects, parse coverage and whether the source is blocked."""
    adapters = adapters or [make() for make in ALL_ADAPTERS]
    out = []
    for adapter in adapters:
        campus = adapter.info.campus
        courses = [c for c in tables["courses"] if c["campus"] == campus]
        with_prereq = [c for c in courses if c["raw_prereq_text"]]
        parsed = [c for c in with_prereq if c["prereq_parsed"]]
        out.append(
            {
                "campus": campus,
                "courses": len(courses),
                "subjects": sorted({c["subject_code"] for c in courses}),
                "programs": len([p for p in tables["programs"] if p["campus"] == campus]),
                "prereq_strings": len(with_prereq),
                "prereq_fully_parsed": len(parsed),
                "parse_percent": round(100 * len(parsed) / len(with_prereq), 1) if with_prereq else None,
                "blocked": (adapter.info.cache_dir / BLOCK_MARKER).exists(),
            }
        )
    return out


def unknown_codes(tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Codes that appear in a prerequisite tree but are not in the data (drawn as ghost cards, never dropped)."""
    known = {c["code"] for c in tables["courses"]}
    mentioned = {code for c in tables["courses"] for code in course_leaves(c["prereq_tree"])}
    return sorted(mentioned - known)


def write(tables: dict[str, list[dict[str, Any]]], out_dir: Path = V3_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with open(out_dir / f"{name}.jsonl", "w") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read(name: str, corpus_dir: Path = V3_DIR) -> list[dict[str, Any]]:
    path = corpus_dir / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


__all__ = ["COURSE_CODE", "build", "campus_of", "coverage", "equivalents", "read", "unknown_codes", "write"]
