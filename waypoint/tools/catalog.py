"""Course catalog queries: an allow-list of named, parameterised queries. The model never writes SQL.

The same named queries have two backends:
- BigQueryCatalog runs the SQL below against the `waypoint` dataset with query parameters.
- InMemoryCatalog answers them from data/corpus/*.jsonl, for tests and offline dev.
- ToolboxCatalog (waypoint/tools/toolbox.py) calls MCP Toolbox for Databases, whose tools.yaml is generated
  from NAMED_QUERIES.
"""

import json
import re
from pathlib import Path
from typing import Any, Protocol

from waypoint.config import Settings

COURSE_COLUMNS = (
    "code, title, description, credits, level, prerequisites, prerequisite_codes, corequisites, exclusions, "
    "exclusion_codes, recommended_preparation, distribution, delivery, source_url, chunk_id"
)

NAMED_QUERIES: dict[str, str] = {
    "get_course": f"SELECT {COURSE_COLUMNS} FROM `{{dataset}}`.courses WHERE STARTS_WITH(code, @code) "
    "ORDER BY code LIMIT 2",
    "search_courses": "SELECT code, title, level, credits FROM `{dataset}`.courses "
    "WHERE (LOWER(title) LIKE @pattern OR LOWER(description) LIKE @pattern) "
    "AND (@department = '' OR department = @department) ORDER BY code LIMIT 15",
    "courses_unlocked_by": "SELECT c.code, c.title, c.prerequisites, c.chunk_id FROM `{dataset}`.prerequisites p "
    "JOIN `{dataset}`.courses c ON c.code = p.course_code WHERE STARTS_WITH(p.required_code, @code) "
    "ORDER BY c.code",
    "get_program": "SELECT code, name, program_type, enrolment_requirements, completion_requirements, "
    "course_codes, source_url, chunk_ids FROM `{dataset}`.programs "
    "WHERE code = @code OR LOWER(name) LIKE @pattern ORDER BY code LIMIT 3",
    "list_programs": "SELECT code, name, program_type, area FROM `{dataset}`.programs ORDER BY code",
}


def normalise_code(code: str) -> str:
    """' mat224 ' -> 'MAT224'; keeps the H5/Y5 suffix if given."""
    return re.sub(r"\s+", "", code).upper()


class Catalog(Protocol):
    def run(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class BigQueryCatalog:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import bigquery

        self._bq = bigquery
        self._client = bigquery.Client(project=settings.gcp_project)
        self._dataset = f"{settings.gcp_project}.{settings.bq_dataset}"

    def run(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if name not in NAMED_QUERIES:
            raise ValueError(f"query {name!r} is not allow-listed")
        sql = NAMED_QUERIES[name].replace("{dataset}", self._dataset)
        config = self._bq.QueryJobConfig(
            query_parameters=[self._bq.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()],
            maximum_bytes_billed=50 * 1024 * 1024,  # hard cap per query: 50 MB
        )
        rows = self._client.query(sql, job_config=config).result()
        return [dict(row.items()) for row in rows]


class InMemoryCatalog:
    def __init__(self, corpus_dir: Path = Path("data/corpus")) -> None:
        self.courses = _read(corpus_dir / "courses.jsonl")
        self.programs = _read(corpus_dir / "programs.jsonl")

    def run(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if name not in NAMED_QUERIES:
            raise ValueError(f"query {name!r} is not allow-listed")
        code = params.get("code", "")
        like = _like_to_regex(params.get("pattern", ""))
        if name == "get_course":
            columns = [col.strip() for col in COURSE_COLUMNS.split(",")]
            return [{k: c[k] for k in columns} for c in self.courses if c["code"].startswith(code)][:2]
        if name == "search_courses":
            dept = params.get("department", "")
            return [
                {k: c[k] for k in ("code", "title", "level", "credits")}
                for c in self.courses
                if (like.search(c["title"]) or like.search(c["description"])) and (not dept or c["department"] == dept)
            ][:15]
        if name == "courses_unlocked_by":
            return [
                {k: c[k] for k in ("code", "title", "prerequisites", "chunk_id")}
                for c in self.courses
                if any(req.startswith(code) for req in c["prerequisite_codes"])
            ]
        if name == "get_program":
            columns = ["code", "name", "program_type", "enrolment_requirements", "completion_requirements"]
            columns += ["course_codes", "source_url", "chunk_ids"]
            return [{k: p[k] for k in columns} for p in self.programs if p["code"] == code or like.search(p["name"])][
                :3
            ]
        return [{k: p[k] for k in ("code", "name", "program_type", "area")} for p in self.programs]


class CachedCatalog:
    """Memoises another catalog. Calendar data changes once a year, so a process-lifetime cache is safe
    and saves a BigQuery round trip (~0.5 s) on every repeated lookup, e.g. while validating a plan."""

    def __init__(self, inner: Catalog, max_entries: int = 2048) -> None:
        self.inner = inner
        self.max_entries = max_entries
        self._cache: dict[tuple[str, tuple[tuple[str, Any], ...]], list[dict[str, Any]]] = {}

    def run(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        key = (name, tuple(sorted(params.items())))
        if key not in self._cache:
            if len(self._cache) >= self.max_entries:
                self._cache.clear()
            self._cache[key] = self.inner.run(name, params)
        return self._cache[key]


def _like_to_regex(pattern: str) -> re.Pattern[str]:
    """SQL LIKE ('%linear%algebra%') -> case-insensitive regex, matching BigQuery's LOWER(x) LIKE behaviour."""
    if not pattern.strip("%"):
        return re.compile(r"(?!)") if not pattern else re.compile("")
    return re.compile(".*".join(re.escape(part) for part in pattern.lower().strip("%").split("%")), re.I)


def _read(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
