"""MCP Toolbox for Databases: generated config and the HTTP client, offline (a fake Toolbox server)."""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from waypoint.config import Settings
from waypoint.tools.catalog import NAMED_QUERIES, InMemoryCatalog
from waypoint.tools.courses import make_course_tools
from waypoint.tools.toolbox import ToolboxCatalog, query_params, render_tools_yaml

TOOLS_YAML = Path("infra/toolbox/tools.yaml")


def test_committed_tools_yaml_is_current():
    s = Settings()
    assert TOOLS_YAML.read_text() == render_tools_yaml(s.gcp_project, s.bq_dataset)


def test_tools_yaml_mirrors_the_allow_list_and_locks_the_source():
    docs = list(yaml.safe_load_all(TOOLS_YAML.read_text()))
    source = next(d for d in docs if d["kind"] == "source")
    assert source["writeMode"] == "blocked"
    assert source["allowedDatasets"] == ["waypoint-513003.waypoint"]
    assert source["maximumBytesBilled"] == 50 * 1024 * 1024
    tools = {d["name"]: d for d in docs if d["kind"] == "tool"}
    assert set(tools) == set(NAMED_QUERIES)
    for name, tool in tools.items():
        assert tool["type"] == "bigquery-sql"  # parameterised SQL only; no free-form execute_sql tool
        assert [p["name"] for p in tool.get("parameters", [])] == query_params(NAMED_QUERIES[name])


def fake_toolbox(backend: InMemoryCatalog, calls: list[tuple[str, dict]]) -> httpx.Client:
    """Answers like Toolbox's /api/tool/<name>/invoke: rows JSON-encoded under "result"."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.split("/")[3]
        params = json.loads(request.content)
        calls.append((name, params))
        rows = backend.run(name, params)
        return httpx.Response(200, json={"result": json.dumps(rows) if rows else "null"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_toolbox_catalog_matches_in_memory_results():
    backend, calls = InMemoryCatalog(), []
    catalog = ToolboxCatalog("http://toolbox:5000", client=fake_toolbox(backend, calls))
    assert catalog.run("get_course", {"code": "MAT224"}) == backend.run("get_course", {"code": "MAT224"})
    assert catalog.run("get_course", {"code": "XYZ999"}) == []
    catalog.run("search_courses", {"pattern": "%algebra%"})
    assert calls[-1] == ("search_courses", {"pattern": "%algebra%", "department": ""})  # every param sent


def test_course_tools_work_over_toolbox():
    catalog = ToolboxCatalog("http://toolbox:5000", client=fake_toolbox(InMemoryCatalog(), []))
    get_course = next(t for t in make_course_tools(catalog) if t.__name__ == "get_course")
    result = get_course("MAT224")
    assert result["found"] and result["chunk_id"] == "cal:mathematical-sciences#mat224h5"


def test_toolbox_catalog_rejects_unlisted_queries():
    with pytest.raises(ValueError):
        ToolboxCatalog("http://toolbox:5000", client=httpx.Client()).run("drop_tables", {})
