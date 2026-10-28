"""Clean, typed attendance records from the raw rows, using fde-kit's LegacyAdapter (retry + TTL cache)."""

import re
from typing import Literal

from fde_kit.mcp_legacy import LegacyAdapter
from pydantic import BaseModel

from parks_demo.source import RawRow, Source

Status = Literal["reported", "unable_to_report", "temporarily_closed"]
STATUS_CODES: dict[str, Status] = {"UR": "unable_to_report", "TC": "temporarily_closed"}
LICENCE = "Contains information licensed under the Open Government Licence – Canada (Parks Canada attendance)."


class Attendance(BaseModel):
    place: str
    kind: str
    fiscal_year: str  # "2024-25": April 2024 to March 2025
    visitors: int | None
    status: Status


def place_kind(place: str) -> str:
    for kind in ("National Marine Conservation Area", "National Urban Park", "National Park", "National Historic Site"):
        if kind.lower() in place.lower():
            return kind
    return "Other"


def normalise(row: RawRow) -> Attendance | None:
    """One raw row -> a typed record, or None for footnote rows like "UR = UNABLE TO REPORT"."""
    place, value = row["place"].strip(), row["value"].strip()
    if "=" in place:
        return None
    code = value.upper()
    if code in STATUS_CODES:
        return Attendance(
            place=place,
            kind=place_kind(place),
            fiscal_year=row["fiscal_year"],
            visitors=None,
            status=STATUS_CODES[code],
        )
    digits = re.sub(r"[,\s]", "", value)
    if not digits.isdigit():
        raise ValueError(f"unrecognised attendance value {value!r} for {place!r}")
    return Attendance(
        place=place, kind=place_kind(place), fiscal_year=row["fiscal_year"], visitors=int(digits), status="reported"
    )


class AttendanceData:
    """Query helpers over the normalised records. The source is read through fde-kit's LegacyAdapter."""

    def __init__(self, source: Source, cache_ttl_s: float = 3600) -> None:
        self._adapter: LegacyAdapter[RawRow, Attendance | None] = LegacyAdapter(
            fetch=source.rows, normalise=normalise, retry_on=(ConnectionError, TimeoutError), cache_ttl_s=cache_ttl_s
        )

    def records(self) -> list[Attendance]:
        return [r for r in self._adapter.get() if r is not None]

    @property
    def stats(self) -> dict[str, int]:
        return self._adapter.stats

    def years(self) -> list[str]:
        return sorted({r.fiscal_year for r in self.records()})

    def find_places(self, query: str) -> list[str]:
        words = query.lower().split()
        return sorted({r.place for r in self.records() if all(w in r.place.lower() for w in words)})

    def attendance(self, place_query: str) -> list[Attendance]:
        places = set(self.find_places(place_query))
        return sorted((r for r in self.records() if r.place in places), key=lambda r: (r.place, r.fiscal_year))

    def top_places(self, fiscal_year: str, kind: str = "", limit: int = 10) -> list[Attendance]:
        rows = [
            r
            for r in self.records()
            if r.fiscal_year == fiscal_year and r.visitors is not None and (not kind or r.kind.lower() == kind.lower())
        ]
        return sorted(rows, key=lambda r: r.visitors or 0, reverse=True)[:limit]

    def total(self, fiscal_year: str, kind: str = "") -> dict[str, int]:
        rows = [
            r for r in self.records() if r.fiscal_year == fiscal_year and (not kind or r.kind.lower() == kind.lower())
        ]
        return {
            "visitors": sum(r.visitors or 0 for r in rows),
            "places_reported": sum(r.status == "reported" for r in rows),
            "places_not_reported": sum(r.status != "reported" for r in rows),
        }
