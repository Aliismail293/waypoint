"""Polite fetcher for the public UTM Academic Calendar.

- Checks robots.txt before every fetch (urllib.robotparser) and refuses disallowed paths.
- Identifies itself with a User-Agent that links to the repo.
- Waits between requests and caches raw HTML on disk, so re-running ingestion doesn't re-hit the site.
"""

import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx

BASE_URL = "https://utm.calendar.utoronto.ca"
USER_AGENT = "WaypointBot/0.1 (student project; +https://github.com/Aliismail293/waypoint)"
DEFAULT_CACHE = Path("data/raw/calendar")

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


def cache_path(path: str, cache_dir: Path = DEFAULT_CACHE) -> Path:
    return cache_dir / (quote(path.strip("/"), safe="") + ".html")


class CalendarFetcher:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE, delay_seconds: float = 2.0) -> None:
        self.cache_dir = cache_dir
        self.delay_seconds = delay_seconds
        self._robots = urllib.robotparser.RobotFileParser(urljoin(BASE_URL, "/robots.txt"))
        self._robots_loaded = False
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)

    def allowed(self, path: str) -> bool:
        if not self._robots_loaded:
            # Fetched with httpx (certifi CAs) rather than RobotFileParser.read(), which uses urllib.
            robots = self._client.get(urljoin(BASE_URL, "/robots.txt"))
            robots.raise_for_status()
            self._robots.parse(robots.text.splitlines())
            self._robots_loaded = True
        return self._robots.can_fetch(USER_AGENT, urljoin(BASE_URL, path))

    def get(self, path: str, refresh: bool = False) -> str:
        target = cache_path(path, self.cache_dir)
        if target.exists() and not refresh:
            return target.read_text()
        if not self.allowed(path):
            raise PermissionError(f"robots.txt disallows {path}")
        time.sleep(self.delay_seconds)
        response = self._client.get(urljoin(BASE_URL, path))
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(response.text)
        return response.text
