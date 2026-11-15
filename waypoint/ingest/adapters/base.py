"""The interface every institution adapter implements (see docs/ADDING_A_UNIVERSITY.md).

An adapter knows where one institution's public pages live and how to read them. It never invents data: a page it
can't read gives an empty list, and a field it can't read stays empty.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from waypoint.ingest.calendar_parser import Chunk, Course, Program


@dataclass
class Page:
    """One cached raw page: the path on the site and its HTML."""

    path: str
    html: str


@dataclass
class AdapterInfo:
    institution_id: str
    institution_name: str
    campus: str  # code, e.g. "utm"
    campus_name: str
    base_url: str
    course_suffixes: tuple[str, ...]  # code endings that identify this campus, e.g. ("H5", "Y5")
    cache_dir: Path
    notes: list[str] = field(default_factory=list)


class SourceAdapter(Protocol):
    info: AdapterInfo

    def list_course_pages(self) -> list[Page]:
        """Cached pages that list courses (department/subject pages)."""

    def parse_course(self, page: Page) -> list[Course]: ...

    def list_programs(self) -> list[Page]:
        """Cached pages that describe programs (often the same pages as the courses)."""

    def parse_program(self, page: Page) -> list[Program]: ...

    def list_policy_pages(self) -> list[Page]:
        """Cached academic-policy pages, chunked for hybrid search."""

    def parse_policy(self, page: Page) -> list[Chunk]: ...
