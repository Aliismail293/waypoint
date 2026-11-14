"""Course map: the prerequisite graph and routes through it.

Built only from the prerequisite links that exist in the catalog data (BigQuery `prerequisites`): a link that isn't
in the data is never drawn. A route is a shortest chain of those links from a course the student has taken or is
taking to the target. It says nothing about requirements the data doesn't hold (grades, program enrolment), and the
UI says so.
"""

import re
from collections import deque
from dataclasses import dataclass
from typing import Any

from waypoint.profile import Profile
from waypoint.tools.catalog import Catalog

COLUMNS = ["before", "now", "lower", "upper"]


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]]
    prereqs: dict[str, set[str]]  # course -> codes it requires
    unlocks: dict[str, set[str]]  # course -> codes that require it

    @property
    def edges(self) -> list[dict[str, str]]:
        return [{"from": r, "to": c} for c in sorted(self.prereqs) for r in sorted(self.prereqs[c])]


def _level_of(code: str) -> int:
    digit = re.search(r"\d", code)
    return int(digit.group()) if digit and digit.group() in "1234" else 2


def column_for(level: int, status: str | None) -> str:
    if status == "done":
        return "before"
    if status == "now":
        return "now"
    return "lower" if level <= 2 else "upper"


def build_graph(catalog: Catalog, profile: Profile | None = None) -> Graph:
    """Nodes: every course in the data, plus any code that appears in a link but not in the data (flagged
    `in_data: false`, drawn so the link isn't hidden). Edges: exactly the rows of the prerequisites table."""
    status = {c.code: c.status for c in profile.courses} if profile else {}
    nodes: dict[str, dict[str, Any]] = {}
    for row in catalog.run("list_courses", {}):
        nodes[row["code"]] = {"code": row["code"], "title": row["title"], "level": row["level"], "in_data": True}
    prereqs: dict[str, set[str]] = {}
    unlocks: dict[str, set[str]] = {}
    for row in catalog.run("prerequisite_edges", {}):
        course, required = row["course_code"], row["required_code"]
        prereqs.setdefault(course, set()).add(required)
        unlocks.setdefault(required, set()).add(course)
        for code in (course, required):
            nodes.setdefault(code, {"code": code, "title": "", "level": _level_of(code), "in_data": False})
    for code in status:  # a taken course outside the data (can't happen after validation) still shows
        nodes.setdefault(code, {"code": code, "title": "", "level": _level_of(code), "in_data": False})
    for code, node in nodes.items():
        node["status"] = status.get(code)
        node["column"] = column_for(node["level"], status.get(code))
    return Graph(nodes, prereqs, unlocks)


def find_route(graph: Graph, target: str, profile: Profile) -> dict[str, Any]:
    """Shortest chain of prerequisite links from the student's courses to `target`.

    Result `state`: unknown_course | already_taken | no_links (the data lists no prerequisites for it) |
    route | no_route (it has prerequisites in the data but none lead back to the student's courses)."""
    target = target.strip().upper()
    if target not in graph.nodes:
        return {"state": "unknown_course", "target": target, "steps": [], "missing": []}
    status = {c.code: c.status for c in profile.courses}
    direct = sorted(graph.prereqs.get(target, set()))
    if status.get(target) == "done":
        return {"state": "already_taken", "target": target, "steps": [], "missing": []}
    if not direct:
        return {"state": "no_links", "target": target, "steps": [], "missing": []}

    # Breadth-first from every course the student has, along "unlocks" links, stopping at the first way to `target`.
    starts = [c for c in status if c in graph.nodes]
    parent: dict[str, str | None] = {c: None for c in starts}
    queue = deque(starts)
    while queue:
        current = queue.popleft()
        if current == target:
            break
        for nxt in sorted(graph.unlocks.get(current, ())):
            if nxt not in parent:
                parent[nxt] = current
                queue.append(nxt)
    missing = [c for c in direct if c not in status]
    if target not in parent:
        return {"state": "no_route", "target": target, "steps": [], "missing": missing}
    chain = [target]
    while parent[chain[-1]] is not None:
        chain.append(parent[chain[-1]])  # type: ignore[arg-type]
    chain.reverse()
    steps = [{"code": c, "title": graph.nodes[c]["title"], "status": status.get(c)} for c in chain]
    return {"state": "route", "target": target, "steps": steps, "missing": missing}
