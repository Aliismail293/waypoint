import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from waypoint.mcp.timetable.adapter import (
    SampleFileSource,
    TimetableAdapter,
    TransientSourceError,
    normalise_record,
    term_to_session,
)
from waypoint.mcp.timetable.server import build_server

RAW = {
    "CRS_CD": "MAT224H5",
    "SESS": "20271",
    "SECT": "lec0102",
    "MTG": "TU 9:00-11:00 DV2082; TH 15:00-16:00",
    "INSTR": "TBA",
    "CAP": "80",
    "ENRL": "",
    "DELIV": "HYBR",
}


def test_normalise_messy_legacy_record():
    section = normalise_record(RAW, "test")
    assert section.section_id == "MAT224H5-20271-LEC0102"
    assert section.term == "2027 Winter"
    assert section.section_type == "Lecture" and section.section_number == "0102"
    assert [(m.day, m.start, m.end, m.room) for m in section.meetings] == [
        ("Tuesday", "09:00", "11:00", "DV2082"),
        ("Thursday", "15:00", "16:00", None),
    ]
    assert section.instructor is None and section.capacity == 80 and section.enrolled is None
    assert section.delivery == "Hybrid"


@pytest.mark.parametrize(("term", "code"), [("2027 Winter", "20271"), ("fall 2026", "20269"), ("20265", "20265")])
def test_term_parsing(term, code):
    assert term_to_session(term) == code


class FlakySource:
    label = "flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def fetch_raw(self, course, session_code):
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientSourceError("503")
        return [dict(RAW, CRS_CD=course + "H5", SESS=session_code)]


def test_retries_then_succeeds():
    sleeps = []
    adapter = TimetableAdapter(FlakySource(failures=2), sleep=sleeps.append)
    assert len(adapter.search_sections("MAT224", "2027 Winter")) == 1
    assert adapter.stats["retries"] == 2 and sleeps == [0.5, 1.0]


def test_gives_up_after_max_attempts():
    adapter = TimetableAdapter(FlakySource(failures=5), sleep=lambda _: None)
    with pytest.raises(TransientSourceError):
        adapter.search_sections("MAT224", "2027 Winter")


def test_cache_respects_ttl():
    now = [0.0]
    source = FlakySource(failures=0)
    adapter = TimetableAdapter(source, cache_ttl_seconds=60, clock=lambda: now[0])
    adapter.search_sections("MAT224", "2027 Winter")
    adapter.search_sections("mat224", "winter 2027")
    assert source.calls == 1 and adapter.stats["cache_hits"] == 1
    now[0] = 61
    adapter.search_sections("MAT224", "2027 Winter")
    assert source.calls == 2


def test_sample_source_is_labelled_synthetic():
    sections = TimetableAdapter(SampleFileSource()).search_sections("MAT224", "2027 Winter")
    assert len(sections) == 2
    assert all("synthetic" in s.data_source for s in sections)


async def test_mcp_tools_in_process():
    server = build_server()
    names = {t.name for t in await server.list_tools()}
    assert names == {"search_sections", "get_section"}
    result = await server.call_tool("get_section", {"section_id": "MAT224H5-20271-LEC0101"})
    assert json.loads(result.content[0].text)["found"] is True


async def test_mcp_server_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "waypoint.mcp.timetable"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search_sections", {"course": "STA256", "term": "2026 Fall"})
        payload = json.loads(result.content[0].text)
        assert payload["count"] == 1
        assert "synthetic" in payload["data_source"]
