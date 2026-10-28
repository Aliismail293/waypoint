"""Waypoint as an MCP server: in-process and through a real stdio MCP client (offline, mock LLM)."""

import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from waypoint.config import Settings
from waypoint.mcp.waypoint.server import build_server


def payload(result) -> dict:
    return json.loads(result.content[0].text)


async def test_tools_listed_and_deterministic_tools_work():
    server = build_server(Settings())
    assert {t.name for t in await server.list_tools()} == {
        "ask_waypoint",
        "search_calendar",
        "get_course",
        "validate_plan",
    }
    course = payload(await server.call_tool("get_course", {"code": "MAT224"}))
    assert course["found"] and course["chunk_id"] == "cal:mathematical-sciences#mat224h5"
    check = payload(await server.call_tool("validate_plan", {"plan": "2026 Fall: MAT301H5", "completed_courses": []}))
    assert check["valid"] is False


async def test_ask_waypoint_returns_cited_answer_and_conversation_id():
    server = build_server(Settings())
    first = payload(await server.call_tool("ask_waypoint", {"question": "What are the prerequisites for MAT224?"}))
    assert first["citations"] and first["citations"][0]["url"].startswith("https://")
    follow = payload(
        await server.call_tool("ask_waypoint", {"question": "And MAT301?", "conversation_id": first["conversation_id"]})
    )
    assert follow["conversation_id"] == first["conversation_id"]


async def test_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "waypoint.mcp.waypoint"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search_calendar", {"query": "Mathematics minor completion requirements"})
        passages = payload(result)["passages"]
        assert passages and all(p["chunk_id"].startswith("cal:") for p in passages)
