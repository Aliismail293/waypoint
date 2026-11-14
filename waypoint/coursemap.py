"""Course map data (V3): visible courses placed in level bands with prerequisite-depth sub-columns.

Placement: the big column is the course's level (100/200/300/400). Inside a band the sub-column is the length of the
longest prerequisite chain that stays inside that band and inside the visible graph (an `or` group counts its
shallowest member, because one of them is enough). A course with no visible prerequisite in its band sits in the
band's first sub-column.

Lines are real prerequisite edges only. A prerequisite outside the filter is never dropped: it arrives as a "ghost"
node labelled with its subject, so the student can add that subject to the view.
"""

from typing import Any

from waypoint.datastore import DataStore
from waypoint.ingest.prereq_parser import describe
from waypoint.profile import Profile

BANDS = (1, 2, 3, 4)


def _band(level: int) -> int:
    return min(max(level, 1), 4)


def _in_filter(course: dict[str, Any], campus: str | None, subjects: set[str]) -> bool:
    return (not campus or course["campus"] == campus) and (not subjects or course["subject_code"] in subjects)


def build_map(
    store: DataStore,
    profile: Profile,
    planned: set[str],
    campus: str | None = None,
    subjects: set[str] | None = None,
) -> dict[str, Any]:
    subjects = subjects or set()
    campus = None if campus in ("", "all") else campus
    visible = {c: row for c, row in store.courses.items() if _in_filter(row, campus, subjects)}
    all_edges = store.edges()
    edges = [e for e in all_edges if e["to"] in visible]
    status = {c.code: c.status for c in profile.courses}

    def node(code: str, ghost: bool) -> dict[str, Any]:
        row = store.get(code)
        level = row["level"] if row else _guess_level(code)
        return {
            "code": code,
            "title": row["title"] if row else "",
            "level": level,
            "band": _band(level),
            "subject": row["subject_code"] if row else code[:3],
            "campus": row["campus"] if row else "",
            "credits": row["credits"] if row else None,
            "ghost": ghost,
            "prereq_text": describe(row["prereq_tree"]) if row else "",
            "in_data": row is not None,
            "status": status.get(code) or ("planned" if code in planned else None),
        }

    nodes = {code: node(code, False) for code in visible}
    for e in edges:
        if e["from"] not in visible and e["from"] not in nodes:
            nodes[e["from"]] = node(e["from"], True)

    inside = [e for e in edges if e["from"] in visible]
    _assign_sub_columns(nodes, inside)
    ordered = sorted(nodes.values(), key=lambda n: (n["ghost"], n["band"], n.get("sub", 0), n["code"]))
    return {
        "nodes": ordered,
        "edges": edges,
        "campuses": [{"code": c["code"], "name": c["name"]} for c in store.campuses],
        "subjects": store.subject_list(campus),
        "filter": {"campus": campus or "all", "subjects": sorted(subjects)},
    }


def _guess_level(code: str) -> int:
    ch = code[3]
    return int(ch) if ch.isdigit() else "ABCD".index(ch) + 1 if ch in "ABCD" else 1


def _assign_sub_columns(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    preds: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        if nodes[e["from"]]["band"] == nodes[e["to"]]["band"] and e["from"] != e["to"]:
            preds.setdefault(e["to"], []).append(e)
    memo: dict[str, int] = {}

    def depth(code: str, trail: frozenset[str]) -> int:
        if code in memo:
            return memo[code]
        best = 0
        groups: dict[int, list[int]] = {}
        for e in preds.get(code, []):
            if e["from"] in trail:  # a cycle in the data: ignore the back edge, never loop
                continue
            d = depth(e["from"], trail | {code}) + 1
            if e["kind"] == "one_of":
                groups.setdefault(e["group"], []).append(d)
            else:
                best = max(best, d)
        for members in groups.values():
            best = max(best, min(members))
        memo[code] = best
        return best

    for code, n in nodes.items():
        n["sub"] = 0 if n["ghost"] else depth(code, frozenset())


def ancestors(edges: list[dict[str, Any]], code: str) -> dict[str, Any]:
    """Every course on any prerequisite path into `code`, and the edges between them (the highlighted path)."""
    into: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        into.setdefault(e["to"], []).append(e)
    nodes, used, stack = {code}, [], [code]
    while stack:
        for e in into.get(stack.pop(), []):
            used.append(e)
            if e["from"] not in nodes:
                nodes.add(e["from"])
                stack.append(e["from"])
    return {"nodes": sorted(nodes), "edges": used}
