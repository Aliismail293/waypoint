"""Programs tab: a requirements checklist for one program against the student's taken, in-progress and planned courses.

Each calendar requirement line keeps its exact text. Its status:
    done / in_progress / planned   the line is satisfied by courses taken / in progress / in the plan
    missing                        a course the line needs isn't in any of those (the missing codes are listed)
    check                          the line has wording Waypoint can't evaluate (credits at a level, grades,
                                   "except ..."): shown with the exact text and the calendar link, never guessed
Admission requirements are returned separately: they are about getting into the program, not about courses.
"""

from typing import Any

from waypoint.datastore import DataStore
from waypoint.ingest.prereq_parser import Node, is_fully_parsed
from waypoint.prereq_eval import evaluate
from waypoint.profile import Profile


def _requirement_status(tree: Node | None, done: set[str], now: set[str], planned: set[str]) -> tuple[str, list[str]]:
    if tree is None or not is_fully_parsed(tree):
        return "check", []
    if evaluate(tree, done).status == "met":
        return "done", []
    if evaluate(tree, done | now).status == "met":
        return "in_progress", []
    after = evaluate(tree, done | now | planned)
    if after.status == "met":
        return "planned", []
    if after.status == "unclear":
        return "check", []
    return "missing", after.missing


def checklist(store: DataStore, code: str, profile: Profile, planned: set[str]) -> dict[str, Any] | None:
    program = store.program(code)
    if program is None:
        return None
    done, now = set(profile.codes("done")), set(profile.codes("now"))
    tree = program["requirements_tree"]
    versions = []
    for version in tree["versions"]:
        groups = []
        for group in version["groups"]:
            rows = []
            for req in group["requirements"]:
                status, needed = _requirement_status(req["tree"], done, now, planned)
                rows.append({"text": req["text"], "status": status, "missing": needed})
            groups.append({"heading": group["heading"], "requirements": rows})
        counts = [r["status"] for g in groups for r in g["requirements"]]
        versions.append(
            {
                "label": version["label"],
                "groups": groups,
                "summary": {s: counts.count(s) for s in ("done", "in_progress", "planned", "missing", "check")},
            }
        )
    credits_done = sum(float((store.get(c) or {}).get("credits") or 0) for c in done)
    need = tree["credits_required"]
    return {
        "code": code,
        "name": program["name"],
        "type": program["type"],
        "campus": program["campus"],
        "calendar_url": program["calendar_url"],
        "calendar_year": program["calendar_year"],
        "credits": {"done": credits_done, "required": [need["min"], need["max"]] if need else None},
        "versions": versions,
        "notes": tree["notes"],
        "rules": [
            {"type": r["rule_type"], "text": r["source_text"], "url": r["calendar_url"]}
            for r in store.rules.get(code, [])
        ],
        "admission": {"text": program["admission_text"], "url": program["calendar_url"]},
    }
