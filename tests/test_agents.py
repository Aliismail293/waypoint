"""End-to-end agent tests: the real agent tree, offline (mock LLM, in-memory stores, stdio MCP server)."""

import pytest

from waypoint.agents.factory import build_agents
from waypoint.agents.runtime import WaypointRuntime, build_dependencies
from waypoint.config import Settings


@pytest.fixture(scope="module")
def settings():
    return Settings(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")


@pytest.fixture
def runtime(settings):
    return WaypointRuntime.from_settings(settings)


def test_tree_shape(settings):
    agents = build_agents(build_dependencies(settings))
    root = agents["waypoint"]
    assert {t.name for t in root.tools} == {"policy_agent", "course_data_agent", "timetable_agent", "planner_agent"}
    assert agents["planner_agent"].planner is not None  # ReAct planner
    assert "validate_plan" in {getattr(t, "__name__", "") for t in agents["planner_agent"].tools}


async def test_course_question_delegates_and_cites(runtime):
    answer = await runtime.ask("u1", "What are the prerequisites for MAT224?")
    assert answer.record.agents[:2] == ["waypoint", "course_data_agent"]
    assert "get_course" in answer.record.tool_calls
    assert "cal:mathematical-sciences#mat224h5" in answer.citations
    assert answer.record.model_calls and all(c.input_tokens > 0 for c in answer.record.model_calls)


async def test_policy_question_retrieves_chunks(runtime):
    answer = await runtime.ask("u1", "How many credits does the Mathematics major require?")
    assert "policy_agent" in answer.record.agents
    assert "search_calendar" in answer.record.tool_calls
    assert len(answer.record.retrieved_chunk_ids) == 5
    assert set(answer.citations) <= set(answer.record.retrieved_chunk_ids)


async def test_timetable_question_goes_through_mcp(runtime):
    answer = await runtime.ask("u1", "Which MAT224 sections fit Tue/Thu afternoons this winter?")
    assert "timetable_agent" in answer.record.agents
    assert "search_sections" in answer.record.tool_calls
    assert "synthetic" in answer.text


async def test_sessions_are_per_user(runtime):
    first = await runtime.ask("alice", "What is MAT301 about?")
    again = await runtime.ask("alice", "And MAT302?", session_id=first.session_id)
    assert again.session_id == first.session_id
    other = await runtime.ask("bob", "What is MAT301 about?", session_id=first.session_id)
    assert other.session_id != first.session_id  # bob can't read alice's session


async def test_request_wide_model_call_budget(settings):
    tight = settings.model_copy(update={"max_model_calls_per_request": 2})
    runtime = WaypointRuntime.from_settings(tight)
    answer = await runtime.ask("u1", "What are the prerequisites for MAT224?")
    # orchestrator (1) + course_data_agent (1) run; the specialist's 2nd call and everything after is skipped
    assert answer.record.limit_hit
    assert len(answer.record.model_calls) == 2
