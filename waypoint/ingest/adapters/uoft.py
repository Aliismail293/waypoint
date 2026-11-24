"""U of T adapters: UTM, St. George (Arts & Science) and UTSC.

All three publish their calendars on the same website platform, so one class reads them and the subclasses only
say where each lives. Only the UTM markup has been checked against a real page: the St. George and UTSC hosts
return 403 to our crawler (progress file V3-D2), so those two adapters are untested against live markup and
return nothing until pages are saved into their cache folder by hand.
"""

from pathlib import Path
from urllib.parse import unquote

from waypoint.ingest.adapters.base import AdapterInfo, Page
from waypoint.ingest.calendar_parser import Chunk, Course, ParsedPage, Program, parse_page

RAW_ROOT = Path("data/raw/uoft")


class UofTCalendarAdapter:
    def __init__(self, info: AdapterInfo) -> None:
        self.info = info

    def _pages(self) -> list[Page]:
        folder = self.info.cache_dir
        if not folder.exists():
            return []
        return [Page("/" + unquote(f.stem), f.read_text()) for f in sorted(folder.glob("*.html"))]

    def _parsed(self, page: Page) -> ParsedPage:
        return parse_page(page.path, page.html, self.info.base_url)

    def list_course_pages(self) -> list[Page]:
        return [p for p in self._pages() if p.path.startswith("/section/")]

    def parse_course(self, page: Page) -> list[Course]:
        return self._parsed(page).courses

    def list_programs(self) -> list[Page]:
        return self.list_course_pages()

    def parse_program(self, page: Page) -> list[Program]:
        return self._parsed(page).programs

    def list_policy_pages(self) -> list[Page]:
        return [p for p in self._pages() if not p.path.startswith("/section/")]

    def parse_policy(self, page: Page) -> list[Chunk]:
        return self._parsed(page).chunks


def _info(campus: str, name: str, host: str, suffixes: tuple[str, ...], notes: list[str]) -> AdapterInfo:
    return AdapterInfo(
        institution_id="uoft",
        institution_name="University of Toronto",
        campus=campus,
        campus_name=name,
        base_url=f"https://{host}.calendar.utoronto.ca",
        course_suffixes=suffixes,
        cache_dir=RAW_ROOT / campus,
        notes=notes,
    )


def uoft_utm() -> UofTCalendarAdapter:
    return UofTCalendarAdapter(
        _info("utm", "UTM (Mississauga)", "utm", ("H5", "Y5"), ["markup checked on a real page"])
    )


def uoft_utsg() -> UofTCalendarAdapter:
    note = "St. George / Arts & Science: host returns 403 to the crawler; markup assumed to match UTM, unverified"
    return UofTCalendarAdapter(_info("utsg", "St. George (Arts & Science)", "artsci", ("H1", "Y1"), [note]))


def uoft_utsc() -> UofTCalendarAdapter:
    note = "UTSC: host returns 403 to the crawler; markup assumed to match UTM, unverified"
    return UofTCalendarAdapter(_info("utsc", "UTSC (Scarborough)", "utsc", ("H3", "Y3"), [note]))


ALL_ADAPTERS = [uoft_utm, uoft_utsg, uoft_utsc]
