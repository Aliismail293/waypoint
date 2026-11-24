"""Polite fetcher for the public U of T academic calendars (UTM, St. George / Arts & Science, UTSC).

- Needs WAYPOINT_CRAWLER_CONTACT (an email or URL for the site owner to reach us); without it, it refuses to crawl.
  The value goes into the User-Agent and is never printed or stored in the repo.
- Checks robots.txt before every fetch (urllib.robotparser) and refuses disallowed paths.
- At most one request every `delay_seconds` (default 2) per host; raw HTML is cached on disk, so each page is
  fetched once.
- A 403 or 429 (including on robots.txt) means "stop": it writes a `_BLOCKED.json` marker next to the cache and
  raises SourceBlocked. Later runs see the marker and don't ask again. There is no retry, no other client and no
  other User-Agent: a blocked site stays blocked until its owner says otherwise.
"""

import json
import os
import time
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx

BASE_URL = "https://utm.calendar.utoronto.ca"
CONTACT_ENV = "WAYPOINT_CRAWLER_CONTACT"
DEFAULT_CACHE = Path("data/raw/uoft/utm")
BLOCK_MARKER = "_BLOCKED.json"

# Program areas (Math & Stats, CS) plus degree-level and general academic policy pages.
PROGRAM_PAGES = [
    "/section/Mathematical-Sciences",
    "/section/Statistics,-Applied",
    "/section/Computer-Science",
    "/Bachelor-of-Computer-Science",
]
POLICY_PAGES = [
    "/degree-requirements",
    "/honours-bachelor-science-hbsc",
    "/program-selection",
    "/course-enrolment",
    "/understanding-courses",
    "/grades-and-academic-record",
    "/term-work-tests-and-appeals",
    "/petitions-and-appeals",
    "/withdrawal-and-absences",
    "/final-examinations",
    "/graduation",
]
ALL_PAGES = PROGRAM_PAGES + POLICY_PAGES


class SourceBlocked(RuntimeError):
    """The site refused us (403/429) or forbids this path. Stop crawling it."""


def cache_path(path: str, cache_dir: Path = DEFAULT_CACHE) -> Path:
    return cache_dir / (quote(path.strip("/"), safe="") + ".html")


def user_agent() -> str:
    contact = os.environ.get(CONTACT_ENV, "").strip()
    if not contact:
        raise SourceBlocked(f"{CONTACT_ENV} is not set: not crawling. Set it to an email or URL and run again.")
    return f"WaypointBot/0.3 (student project; +https://github.com/Aliismail293/waypoint; contact: {contact})"


class CalendarFetcher:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE,
        delay_seconds: float = 2.0,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.delay_seconds = delay_seconds
        self.base_url = base_url
        self._user_agent = ""
        self._robots = urllib.robotparser.RobotFileParser(urljoin(base_url, "/robots.txt"))
        self._robots_loaded = False
        self._client = client
        self._last_request = 0.0

    @property
    def blocked_marker(self) -> Path:
        return self.cache_dir / BLOCK_MARKER

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._user_agent = user_agent()
            self._client = httpx.Client(headers={"User-Agent": self._user_agent}, timeout=30, follow_redirects=True)
        return self._client

    def _request(self, url: str) -> httpx.Response:
        wait = self._last_request + self.delay_seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        response = self._http().get(url)
        self._last_request = time.monotonic()
        if response.status_code in (403, 429):
            self._record_block(url, response.status_code)
        return response

    def _record_block(self, url: str, status: int) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        note = {"url": url, "status": status, "at": datetime.now(UTC).isoformat(), "action": "stopped crawling"}
        self.blocked_marker.write_text(json.dumps(note, indent=2))
        raise SourceBlocked(f"{url} returned {status}: stopped crawling {self.base_url}")

    def allowed(self, path: str) -> bool:
        if not self._robots_loaded:
            # Fetched with httpx (certifi CAs) rather than RobotFileParser.read(), which uses urllib.
            robots = self._request(urljoin(self.base_url, "/robots.txt"))
            if robots.status_code == 404:
                self._robots.parse([])  # no robots.txt means no stated restrictions
            else:
                robots.raise_for_status()
                self._robots.parse(robots.text.splitlines())
            self._robots_loaded = True
        return self._robots.can_fetch(self._user_agent or "WaypointBot", urljoin(self.base_url, path))

    def get(self, path: str, refresh: bool = False) -> str:
        target = cache_path(path, self.cache_dir)
        if target.exists() and not refresh:
            return target.read_text()
        if self.blocked_marker.exists():
            raise SourceBlocked(f"{self.base_url} blocked us earlier ({self.blocked_marker}): not asking again")
        if not self.allowed(path):
            raise SourceBlocked(f"robots.txt disallows {path}")
        response = self._request(urljoin(self.base_url, path))
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(response.text)
        return response.text
