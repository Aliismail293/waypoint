"""V3 data foundation: fetcher politeness/blocks, adapters, program parser, corpus build."""

import json
from pathlib import Path

import httpx
import pytest

from waypoint.ingest import v3
from waypoint.ingest.adapters.base import AdapterInfo
from waypoint.ingest.adapters.uoft import UofTCalendarAdapter, uoft_utsc
from waypoint.ingest.fetch import BLOCK_MARKER, CalendarFetcher, SourceBlocked, user_agent
from waypoint.ingest.program_parser import parse_requirements, program_rules

FIXTURE = Path("tests/fixtures/calendar/section%2FMathematical-Sciences.html")


def test_fetcher_refuses_without_contact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WAYPOINT_CRAWLER_CONTACT", raising=False)
    with pytest.raises(SourceBlocked, match="WAYPOINT_CRAWLER_CONTACT"):
        CalendarFetcher(cache_dir=tmp_path).get("/section/X")


def test_user_agent_carries_the_contact_without_hardcoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_CRAWLER_CONTACT", "someone@example.org")
    assert "contact: someone@example.org" in user_agent()


def test_403_stops_crawling_and_is_remembered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403, text="Forbidden")

    monkeypatch.setenv("WAYPOINT_CRAWLER_CONTACT", "someone@example.org")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = CalendarFetcher(cache_dir=tmp_path, delay_seconds=0, client=client)
    with pytest.raises(SourceBlocked):
        fetcher.get("/section/Statistics")
    assert (tmp_path / BLOCK_MARKER).exists()
    assert len(calls) == 1  # the robots.txt request; the page itself was never asked for
    with pytest.raises(SourceBlocked, match="not asking again"):
        CalendarFetcher(cache_dir=tmp_path, delay_seconds=0, client=client).get("/section/Statistics")
    assert len(calls) == 1  # a later run didn't ask again


def test_fetcher_caches_pages_and_obeys_robots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text="<html>ok</html>")

    monkeypatch.setenv("WAYPOINT_CRAWLER_CONTACT", "someone@example.org")
    fetcher = CalendarFetcher(
        cache_dir=tmp_path, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert fetcher.get("/section/A") == "<html>ok</html>"
    assert fetcher.get("/section/A") == "<html>ok</html>"  # second read is from the cache
    assert calls == ["/robots.txt", "/section/A"]
    with pytest.raises(SourceBlocked, match="disallows"):
        fetcher.get("/private/x")


def _utm_adapter(tmp_path: Path) -> UofTCalendarAdapter:
    (tmp_path / FIXTURE.name).write_text(FIXTURE.read_text())
    info = AdapterInfo(
        "uoft", "University of Toronto", "utm", "UTM", "https://utm.calendar.utoronto.ca", ("H5", "Y5"), tmp_path
    )
    return UofTCalendarAdapter(info)


def test_build_from_cached_utm_page(tmp_path: Path) -> None:
    tables = v3.build([_utm_adapter(tmp_path)])
    courses = {c["code"]: c for c in tables["courses"]}
    assert len(courses) == 59
    mat224 = courses["MAT224H5"]
    assert mat224["institution_id"] == "uoft" and mat224["campus"] == "utm" and mat224["subject_code"] == "MAT"
    assert mat224["prereq_tree"] == {"all_of": [{"course": "MAT102H5"}, {"course": "MAT223H5"}]}
    assert mat224["status"] == "current"
    pairs = {(e["code_a"], e["code_b"]) for e in tables["course_equivalents"]}
    assert ("MAT224H5", "MATB24H3") in pairs and ("MAT224H1", "MAT224H5") in pairs
    assert all(e["source"] == "listed as exclusion" for e in tables["course_equivalents"])
    # same-campus exclusions are not cross-campus equivalents
    assert ("MAT224H5", "MAT240H5") not in pairs


def test_programs_get_requirements_trees_and_rules(tmp_path: Path) -> None:
    tables = v3.build([_utm_adapter(tmp_path)])
    programs = {p["code"]: p for p in tables["programs"]}
    assert set(programs) == {"ERSPE2511", "ERMAJ2511", "ERMAJ2512", "ERMIN2511"}
    minor = programs["ERMIN2511"]["requirements_tree"]
    assert minor["credits_required"] == {"min": 4.0, "max": 4.0}
    first = minor["versions"][0]["groups"][0]["requirements"][0]
    assert first == {"text": "MAT102H5", "tree": {"course": "MAT102H5"}}
    rules = [r for r in tables["program_rules"] if r["program_code"] == "ERMAJ2511"]
    assert any(r["rule_type"] == "program_exclusion" and "ERMAJ2512" in r["params"]["programs"] for r in rules)
    assert any(r["rule_type"] == "credits_required" for r in rules)
    assert any(r["rule_type"] == "manual_check" and r["source_text"] for r in rules)


def test_wrapped_bullets_are_joined_and_unparsed_kept() -> None:
    text = "8.5-9.0 credits are required.\nFirst year:\n- MAT102H5 or\nCSC110Y5\n"
    text += "- 0.5 additional credits in MAT at 300/400 level"
    tree = parse_requirements(text)
    reqs = tree["versions"][0]["groups"][0]["requirements"]
    assert reqs[0]["tree"] == {"one_of": [{"course": "MAT102H5"}, {"course": "CSC110Y5"}]}
    assert "unparsed" in reqs[1]["tree"]
    assert tree["credits_required"] == {"min": 8.5, "max": 9.0}
    assert program_rules("X", tree, "u")[0]["rule_type"] == "credits_required"


def test_empty_cache_contributes_nothing_and_coverage_says_so(tmp_path: Path) -> None:
    adapter = uoft_utsc()
    adapter.info.cache_dir = tmp_path
    tables = v3.build([adapter])
    assert tables["courses"] == [] and tables["campuses"][0]["code"] == "utsc"
    assert v3.coverage(tables, [adapter])[0]["courses"] == 0


def test_committed_v3_corpus_matches_a_fresh_build_shape() -> None:
    rows = v3.read("courses")
    assert rows and {"institution_id", "campus", "subject_code", "prereq_tree", "status", "calendar_url"} <= set(
        rows[0]
    )
    assert {r["institution_id"] for r in rows} == {"uoft"}
    assert json.dumps(v3.read("institutions")) == '[{"id": "uoft", "name": "University of Toronto"}]'
