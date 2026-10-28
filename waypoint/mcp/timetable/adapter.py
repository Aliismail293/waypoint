"""Legacy timetable adapter: wraps an old, flaky, oddly-shaped source behind a clean, typed interface.

What a legacy adapter has to do, and where it happens here:
- Retry transient failures with exponential backoff ............ `TimetableAdapter._fetch_with_retry`
- Cache responses so a slow source isn't hit on every question .. `TimetableAdapter._cache` (TTL)
- Normalise the source's schema into ours ....................... `normalise_record`
  ("TU 13:00-15:00 DH2080;TH ..." -> structured meetings, "20271" -> "2027 Winter", "LEC0101" -> type + number)

Sources implement `TimetableSource.fetch_raw`. Only `SampleFileSource` exists today: the live UTM
timetable's terms of use are unclear, so `LiveTimetableSource` is a deliberate stub.
"""

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

SAMPLE_FILE = Path(__file__).parent / "sample_data" / "timetable_sample.json"

DAYS = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday", "FR": "Friday", "SA": "Saturday"}
SEASONS = {"9": "Fall", "1": "Winter", "5": "Summer"}
DELIVERY = {
    "INPER": "In person",
    "ONLSYNC": "Online (synchronous)",
    "ONLASYNC": "Online (asynchronous)",
    "HYBR": "Hybrid",
}
SECTION_TYPES = {"LEC": "Lecture", "TUT": "Tutorial", "PRA": "Practical"}
MEETING = re.compile(r"^(?P<day>[A-Z]{2})\s+(?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2})(?:\s+(?P<room>\S+))?$")


class TransientSourceError(Exception):
    """The source failed in a way worth retrying (timeout, 5xx)."""


class Meeting(BaseModel):
    day: str
    start: str
    end: str
    room: str | None


class Section(BaseModel):
    section_id: str
    course: str
    term: str
    section_type: str
    section_number: str
    meetings: list[Meeting]
    instructor: str | None
    capacity: int | None
    enrolled: int | None
    delivery: str
    data_source: str


class TimetableSource(Protocol):
    label: str

    def fetch_raw(self, course: str, session_code: str) -> list[dict]: ...


class SampleFileSource:
    label = "sample (synthetic, not real UTM offerings)"

    def __init__(self, path: Path = SAMPLE_FILE) -> None:
        self._records: list[dict] = json.loads(path.read_text())["records"]

    def fetch_raw(self, course: str, session_code: str) -> list[dict]:
        return [r for r in self._records if r["CRS_CD"].startswith(course) and r["SESS"] == session_code]


class LiveTimetableSource:
    """Placeholder for the real UTM timetable. Not implemented until its terms of use are confirmed."""

    label = "live UTM timetable"

    def fetch_raw(self, course: str, session_code: str) -> list[dict]:
        raise NotImplementedError("Live timetable access is disabled pending a terms-of-use check.")


def term_to_session(term: str) -> str:
    """'2027 Winter' / 'winter 2027' / '20271' -> '20271' (U of T session code: year + 9/1/5)."""
    term = term.strip()
    if re.fullmatch(r"\d{4}[915]", term):
        return term
    year = re.search(r"\d{4}", term)
    season = next((code for code, name in SEASONS.items() if name.lower() in term.lower()), None)
    if not year or not season:
        raise ValueError(f"Unrecognised term {term!r}; use e.g. '2026 Fall' or '2027 Winter'.")
    return year.group(0) + season


def session_to_term(session_code: str) -> str:
    return f"{session_code[:4]} {SEASONS.get(session_code[4], '?')}"


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None


def normalise_record(raw: dict, data_source: str) -> Section:
    meetings = []
    for part in filter(None, (p.strip() for p in raw.get("MTG", "").split(";"))):
        match = MEETING.match(part)
        if match:
            meetings.append(
                Meeting(
                    day=DAYS.get(match["day"], match["day"]),
                    start=match["start"].zfill(5),
                    end=match["end"].zfill(5),
                    room=match["room"],
                )
            )
    section = raw["SECT"].strip().upper()
    instructor = raw.get("INSTR", "").strip()
    return Section(
        section_id=f"{raw['CRS_CD']}-{raw['SESS']}-{section}",
        course=raw["CRS_CD"],
        term=session_to_term(raw["SESS"]),
        section_type=SECTION_TYPES.get(section[:3], section[:3]),
        section_number=section[3:],
        meetings=meetings,
        instructor=None if instructor in ("", "TBA") else instructor,
        capacity=_int_or_none(raw.get("CAP")),
        enrolled=_int_or_none(raw.get("ENRL")),
        delivery=DELIVERY.get(raw.get("DELIV", ""), raw.get("DELIV", "unknown")),
        data_source=data_source,
    )


class TimetableAdapter:
    def __init__(
        self,
        source: TimetableSource,
        cache_ttl_seconds: float = 900,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[str, str], tuple[float, list[Section]]] = {}
        self.stats = {"source_calls": 0, "cache_hits": 0, "retries": 0}

    def search_sections(self, course: str, term: str) -> list[Section]:
        key = (course.strip().upper(), term_to_session(term))
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < self.cache_ttl_seconds:
            self.stats["cache_hits"] += 1
            return cached[1]
        raw = self._fetch_with_retry(*key)
        sections = [normalise_record(r, self.source.label) for r in raw]
        self._cache[key] = (self._clock(), sections)
        return sections

    def get_section(self, section_id: str) -> Section | None:
        course, session, _ = section_id.split("-", 2)
        return next((s for s in self.search_sections(course, session) if s.section_id == section_id), None)

    def _fetch_with_retry(self, course: str, session_code: str) -> list[dict]:
        for attempt in range(1, self.max_attempts + 1):
            self.stats["source_calls"] += 1
            try:
                return self.source.fetch_raw(course, session_code)
            except TransientSourceError:
                if attempt == self.max_attempts:
                    raise
                self.stats["retries"] += 1
                self._sleep(self.base_delay_seconds * 2 ** (attempt - 1))
        raise AssertionError("unreachable")
