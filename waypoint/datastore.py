"""The V3 structured data (data/corpus/v3/*.jsonl) with the lookups the fact route, course map, plan, programs and
enrolment check use. Facts come from here, in code; the model only phrases them.

Everything is keyed by `institution_id` (default "uoft"), so a second university is a data change, not a code change.
Calendar data changes once a year, so the store is read once per process and kept in memory (a few hundred KB per
campus), which keeps lookups under a millisecond instead of a BigQuery round trip.
"""

import functools
import re
from pathlib import Path
from typing import Any

from waypoint.ingest import v3
from waypoint.ingest.prereq_parser import Node, course_leaves

DEFAULT_INSTITUTION = "uoft"
SUFFIX = re.compile(r"[HY]\d$")
CODE_IN_TEXT = re.compile(r"\b([A-Z]{3}[A-D0-9]\d{2}(?:[HY]\d)?)\b")
CAMPUS_NAMES = {"utm": "UTM", "utsg": "St. George", "utsc": "UTSC"}


def stem(code: str) -> str:
    """'MAT224H5' -> 'MAT224'; UTSC 'MATB24H3' -> 'MATB24'. Used to match a code typed without its suffix."""
    return SUFFIX.sub("", code)


class DataStore:
    def __init__(self, corpus_dir: Path = v3.V3_DIR, institution_id: str = DEFAULT_INSTITUTION) -> None:
        self.institution_id = institution_id
        rows = [c for c in v3.read("courses", corpus_dir) if c["institution_id"] == institution_id]
        self.courses: dict[str, dict[str, Any]] = {c["code"]: c for c in rows}
        self.subjects = [s for s in v3.read("subjects", corpus_dir) if s["institution_id"] == institution_id]
        self.campuses = [c for c in v3.read("campuses", corpus_dir) if c["institution_id"] == institution_id]
        self.programs: dict[str, dict[str, Any]] = {
            p["code"]: p for p in v3.read("programs", corpus_dir) if p["institution_id"] == institution_id
        }
        self.rules: dict[str, list[dict[str, Any]]] = {}
        for rule in v3.read("program_rules", corpus_dir):
            self.rules.setdefault(rule["program_code"], []).append(rule)
        self._equivalents: dict[str, list[str]] = {}
        for pair in v3.read("course_equivalents", corpus_dir):
            self._equivalents.setdefault(pair["code_a"], []).append(pair["code_b"])
            self._equivalents.setdefault(pair["code_b"], []).append(pair["code_a"])
        self._requirers: dict[str, set[str]] = {}
        for code, course in self.courses.items():
            for needed in course_leaves(course.get("prereq_tree")):
                self._requirers.setdefault(needed, set()).add(code)

    # ---- courses ----

    def get(self, code: str) -> dict[str, Any] | None:
        return self.courses.get(code.upper())

    def resolve(self, text: str) -> list[str]:
        """Course codes in `text` that exist in the data. A code typed without its suffix ('MAT224') matches every
        campus's version of it; a code that isn't in the data is returned by `mentioned_unknown`."""
        found: list[str] = []
        for token in CODE_IN_TEXT.findall(text.upper()):
            hits = [token] if token in self.courses else [c for c in self.courses if stem(c) == stem(token)]
            found += [c for c in hits if c not in found]
        return found

    def mentioned_unknown(self, text: str) -> list[str]:
        return [t for t in dict.fromkeys(CODE_IN_TEXT.findall(text.upper())) if not self.resolve(t)]

    def courses_requiring(self, code: str) -> list[str]:
        """Courses whose prerequisite tree mentions `code`, anywhere in it."""
        return sorted(self._requirers.get(code, set()))

    def equivalents(self, code: str) -> list[str]:
        """Courses another campus (or this one) lists as exclusions of `code`, or that list it."""
        return sorted(set(self._equivalents.get(code, [])))

    def exclusions(self, code: str) -> list[str]:
        course = self.get(code)
        return list(course["exclusion_codes"]) if course else []

    def edges(self, campus: str | None = None) -> list[dict[str, Any]]:
        """Prerequisite edges for the course map: {"from", "to", "kind": "all"|"one_of", "group": int}.

        An edge is `one_of` when its source sits under an `or` anywhere in the target's tree; `group` numbers
        each `or` so the map can draw its members together with an "or" badge."""
        out: list[dict[str, Any]] = []
        for code, course in sorted(self.courses.items()):
            if campus and course["campus"] != campus:
                continue
            counter = [0]
            _walk_edges(course.get("prereq_tree"), code, False, 0, counter, out)
        return out

    def subject_list(self, campus: str | None = None) -> list[dict[str, str]]:
        """Subjects present in the data as {code, name, campus}; `name` is the calendar's section name."""
        seen: dict[str, dict[str, str]] = {}
        for s in self.subjects:
            if campus and s["campus"] != campus:
                continue
            seen.setdefault(s["code"], {"code": s["code"], "name": s["name"], "campus": s["campus"]})
        return sorted(seen.values(), key=lambda s: s["code"])

    # ---- programs ----

    def program(self, code: str) -> dict[str, Any] | None:
        return self.programs.get(code.upper())

    def find_programs(self, query: str = "", campus: str | None = None) -> list[dict[str, Any]]:
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w]
        out = []
        for p in self.programs.values():
            if campus and p["campus"] != campus:
                continue
            haystack = f"{p['code']} {p['name']} {p['type']}".lower()
            if all(w in haystack for w in words):
                out.append(p)
        return sorted(out, key=lambda p: p["code"])


def _walk_edges(
    tree: Node | None, target: str, under_or: bool, group: int, counter: list[int], out: list[dict[str, Any]]
) -> None:
    if tree is None:
        return
    if "course" in tree:
        edge = {"from": tree["course"], "to": target, "kind": "one_of" if under_or else "all", "group": group}
        if edge not in out:
            out.append(edge)
        return
    if "one_of" in tree:
        counter[0] += 1
        gid = counter[0]
        for child in tree["one_of"]:
            _walk_edges(child, target, True, gid, counter, out)
    elif "all_of" in tree:
        for child in tree["all_of"]:
            _walk_edges(child, target, under_or, group, counter, out)


@functools.lru_cache(maxsize=4)
def get_store(institution_id: str = DEFAULT_INSTITUTION) -> DataStore:
    return DataStore(institution_id=institution_id)
