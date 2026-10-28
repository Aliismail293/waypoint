"""Live checks against real GCP resources. Skipped unless WAYPOINT_LIVE_TESTS=1 (never in CI)."""

import os

import pytest

from waypoint.config import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("WAYPOINT_LIVE_TESTS") != "1", reason="live GCP tests are opt-in"),
]


def test_bigquery_catalog_named_queries():
    from waypoint.tools.catalog import BigQueryCatalog
    from waypoint.tools.courses import make_course_tools

    tools = {f.__name__: f for f in make_course_tools(BigQueryCatalog(Settings()))}
    assert tools["get_course"]("MAT224")["code"] == "MAT224H5"
    assert tools["courses_unlocked_by"]("MAT224")["count"] > 3
    assert tools["get_program"]("ERMAJ2511")["count"] == 1
    assert tools["search_courses"]("graph", "MAT")["count"] >= 1
    assert len(tools["list_programs"]()["programs"]) >= 4
