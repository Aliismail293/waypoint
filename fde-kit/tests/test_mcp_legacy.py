import json
from dataclasses import dataclass

import pytest

from fde_kit.mcp_legacy import LegacyAdapter, TransientError, build_mcp_server

RAW = {"CRS_CD": "MAT224H5", "SECT": "lec0102", "CAP": "80", "INSTR": "TBA"}


@dataclass
class Section:
    section_id: str
    capacity: int | None
    instructor: str | None


def normalise(raw: dict) -> Section:
    instructor = raw["INSTR"].strip()
    return Section(
        section_id=f"{raw['CRS_CD']}-{raw['SECT'].upper()}",
        capacity=int(raw["CAP"]) if raw["CAP"].isdigit() else None,
        instructor=None if instructor in ("", "TBA") else instructor,
    )


class FlakySource:
    def __init__(self, failures: int, error: type[Exception] = TransientError) -> None:
        self.failures = failures
        self.error = error
        self.calls: list[tuple] = []

    def fetch(self, course: str, term: str) -> list[dict]:
        self.calls.append((course, term))
        if len(self.calls) <= self.failures:
            raise self.error("503")
        return [dict(RAW, CRS_CD=course + "H5")]


def test_normalises_each_record():
    adapter = LegacyAdapter(FlakySource(0).fetch, normalise)
    assert adapter.get("MAT224", "20271") == [Section("MAT224H5-LEC0102", 80, None)]


def test_retries_with_exponential_backoff_then_succeeds():
    sleeps: list[float] = []
    adapter = LegacyAdapter(FlakySource(failures=2).fetch, normalise, sleep=sleeps.append)
    assert len(adapter.get("MAT224", "20271")) == 1
    assert adapter.stats == {"source_calls": 3, "cache_hits": 0, "retries": 2}
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_max_attempts():
    source = FlakySource(failures=5)
    adapter = LegacyAdapter(source.fetch, normalise, max_attempts=3, sleep=lambda _: None)
    with pytest.raises(TransientError):
        adapter.get("MAT224", "20271")
    assert len(source.calls) == 3


def test_non_transient_errors_are_not_retried():
    source = FlakySource(failures=1, error=KeyError)
    adapter = LegacyAdapter(source.fetch, normalise, sleep=lambda _: pytest.fail("should not sleep"))
    with pytest.raises(KeyError):
        adapter.get("MAT224", "20271")
    assert len(source.calls) == 1


def test_configurable_retry_exceptions_and_lazy_fetch():
    attempts = []

    def lazy_fetch(course: str):  # a generator only fails when iterated
        attempts.append(course)
        if len(attempts) == 1:
            raise TimeoutError
        yield dict(RAW, CRS_CD=course)

    adapter = LegacyAdapter(lazy_fetch, normalise, retry_on=(TimeoutError, ConnectionError), sleep=lambda _: None)
    assert adapter.get("STA256")[0].section_id == "STA256-LEC0102"
    assert adapter.stats["retries"] == 1


def test_cache_respects_ttl_and_canonical_args():
    now = [0.0]
    source = FlakySource(failures=0)
    adapter = LegacyAdapter(
        source.fetch,
        normalise,
        canonical_args=lambda course, term: (course.strip().upper(), term),
        cache_ttl_s=60,
        clock=lambda: now[0],
    )
    adapter.get("MAT224", "20271")
    adapter.get(" mat224", "20271")
    assert source.calls == [("MAT224", "20271")]  # fetch gets the canonical args
    assert adapter.stats["cache_hits"] == 1
    now[0] = 61
    adapter.get("MAT224", "20271")
    assert len(source.calls) == 2


def test_zero_ttl_disables_the_cache():
    source = FlakySource(failures=0)
    adapter = LegacyAdapter(source.fetch, normalise, cache_ttl_s=0)
    adapter.get("MAT224", "20271")
    adapter.get("MAT224", "20271")
    assert len(source.calls) == 2 and adapter.stats["cache_hits"] == 0


def make_tools():
    adapter = LegacyAdapter(FlakySource(0).fetch, normalise)

    def search_sections(course: str, term: str) -> dict:
        """List the sections of a course in a term, e.g. course "MAT224", term "20271"."""
        sections = adapter.get(course, term)
        return {"count": len(sections), "sections": [s.__dict__ for s in sections]}

    def ping() -> str:
        """Health check."""
        return "pong"

    return [search_sections, ping]


async def test_mcp_server_lists_and_calls_tools_in_process():
    server = build_mcp_server("timetable", "Course sections; data is synthetic.", make_tools())
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == {"search_sections", "ping"}
    assert tools["search_sections"].description.startswith("List the sections")
    assert set(tools["search_sections"].input_schema["properties"]) == {"course", "term"}

    result = await server.call_tool("search_sections", {"course": "MAT224", "term": "20271"})
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 1 and payload["sections"][0]["section_id"] == "MAT224H5-LEC0102"
    assert (await server.call_tool("ping", {})).content[0].text == "pong"
