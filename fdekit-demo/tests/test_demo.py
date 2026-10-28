"""Offline tests for the fde-kit demo: legacy data cleanup, MCP tools, the agent (scripted model), the API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fde_kit.testing import ScriptedLlm, text_response, tool_call_response

from parks_demo import app as app_module
from parks_demo.adapter import AttendanceData, normalise
from parks_demo.agent import Assistant
from parks_demo.config import Settings
from parks_demo.mcp_server import build_server
from parks_demo.source import CsvSource


def test_normalise_handles_the_legacy_formats():
    assert (
        normalise({"place": "Banff National Park", "fiscal_year": "2024-25", "value": "4,230,156"}).visitors == 4230156
    )
    closed = normalise({"place": "X National Historic Site", "fiscal_year": "2024-25", "value": "TC"})
    assert closed.visitors is None and closed.status == "temporarily_closed" and closed.kind == "National Historic Site"
    assert normalise({"place": "UR = UNABLE TO REPORT", "fiscal_year": "2024-25", "value": ""}) is None
    with pytest.raises(ValueError):
        normalise({"place": "Y National Park", "fiscal_year": "2024-25", "value": "n/a?"})


def test_reads_every_file_whatever_its_encoding():
    data = AttendanceData(CsvSource())
    assert data.years() == ["2023-24", "2024-25", "2025-26"]
    assert len(data.records()) == 372
    assert "Grand-Pré National Historic Site" in data.find_places("grand historic")
    data.records()
    assert data.stats["source_calls"] == 1  # later reads come from fde-kit's TTL cache


def test_cp1252_fallback(tmp_path: Path):
    (tmp_path / "parks-canada-attendance-2099_00.csv").write_bytes(
        'Place,2099-00 FY Attendance\nGrand-Pré National Historic Site,"1,234"\n'.encode("cp1252")
    )
    assert AttendanceData(CsvSource(tmp_path)).records()[0].place == "Grand-Pré National Historic Site"


async def test_mcp_tools():
    server = build_server(AttendanceData(CsvSource()))
    assert {t.name for t in await server.list_tools()} == {
        "list_years",
        "find_places",
        "place_attendance",
        "top_places",
        "total_attendance",
    }
    result = json.loads((await server.call_tool("top_places", {"fiscal_year": "2024-25", "limit": 1})).content[0].text)
    assert result["rows"][0]["place"] == "Banff National Park" and result["rows"][0]["visitors"] == 4230156


async def test_agent_answers_through_mcp_with_metrics():
    llm = ScriptedLlm(
        script=[
            tool_call_response("top_places", {"fiscal_year": "2024-25", "kind": "National Park", "limit": 1}),
            text_response("Banff National Park: 4,230,156 visitors in 2024-25.", input_tokens=500, output_tokens=20),
        ]
    )
    reply = await Assistant(Settings(), model=llm).ask("u", "Most visited national park in 2024-25?")
    assert "4,230,156" in reply.text
    assert reply.metrics["model_calls"] == 2 and reply.metrics["tool_calls"] == 1 and reply.metrics["tool_errors"] == 0
    tool_turn = llm.requests[-1].contents[-1].parts[0].function_response.response
    assert "Banff National Park" in json.dumps(tool_turn)  # the real MCP server answered over stdio


async def test_agent_budget_stops_runaway_loops():
    llm = ScriptedLlm(script=[tool_call_response("list_years")] * 5)
    reply = await Assistant(Settings(max_model_calls=2), model=llm).ask("u", "loop")
    assert reply.metrics["limit_hit"] is True and reply.metrics["model_calls"] == 2


class FakeAssistant:
    async def ask(self, user_id, question):
        from parks_demo.agent import Reply

        return Reply(text=f"answer for {user_id}", metrics={"latency_ms": 1.0, "model_calls": 1, "cost_usd": 0.0})


def test_api_fails_closed_and_verifies_tokens(monkeypatch):
    with TestClient(app_module.create_app(Settings(google_client_id=""), FakeAssistant())) as client:
        assert client.post("/api/ask", json={"question": "hi"}).status_code == 503
    with TestClient(app_module.create_app(Settings(google_client_id="cid"), FakeAssistant())) as client:
        assert client.post("/api/ask", json={"question": "hi"}).status_code == 401
        claims = {"sub": "42", "email_verified": True}
        monkeypatch.setattr(app_module.id_token, "verify_oauth2_token", lambda token, req, audience: claims)
        r = client.post("/api/ask", json={"question": "hi"}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json()["answer"] == "answer for 42"
        assert client.get("/healthz").json()["demo"] is True
