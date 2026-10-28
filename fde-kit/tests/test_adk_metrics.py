"""MetricsPlugin on a real ADK tree: a root agent that delegates to a child through AgentTool, offline."""

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from fde_kit.adk_metrics import (
    DEFAULT_BUDGET_MESSAGE,
    MetricsPlugin,
    RequestMetrics,
    collect_ids,
    current_metrics,
    request_scope,
)
from fde_kit.pricing import cost_usd
from fde_kit.testing import ScriptedLlm, text_response, tool_call_response

FLASH, LITE = "gemini-3.5-flash", "gemini-3.5-flash-lite"


def lookup_course(code: str) -> dict:
    """Look up a course by code."""
    return {"results": [{"chunk_id": "cal:mat224", "text": "MAT224 ..."}], "chunk_ids": ["cal:mat102", "cal:mat224"]}


def broken_tool(code: str) -> dict:
    """A tool that reports failure in its result."""
    return {"error": "source unavailable"}


def script() -> list:
    """Root delegates to the child; the child calls a tool, then answers; root answers. 4 model calls."""
    return [
        tool_call_response("lookup_agent", {"request": "MAT224"}, input_tokens=100, output_tokens=10, model=FLASH),
        tool_call_response("lookup_course", {"code": "MAT224"}, input_tokens=200, output_tokens=20, model=LITE),
        text_response("MAT224 needs MAT102.", input_tokens=300, output_tokens=30, thinking_tokens=5, model=LITE),
        text_response("You need MAT102 first.", input_tokens=400, output_tokens=40, cached_tokens=100, model=FLASH),
    ]


EXPECTED_COST = (
    cost_usd(FLASH, 100, 10)
    + cost_usd(LITE, 200, 20)
    + cost_usd(LITE, 300, 30, thinking_tokens=5)
    + cost_usd(FLASH, 400, 40, cached_tokens=100)
)


def build_tree(llm: ScriptedLlm, child_tools: list | None = None) -> LlmAgent:
    child = LlmAgent(
        name="lookup_agent",
        model=llm,
        description="Looks up courses.",
        instruction="Look up the course.",
        tools=child_tools or [lookup_course],
    )
    return LlmAgent(name="root", model=llm, instruction="Answer course questions.", tools=[AgentTool(child)])


async def run(
    root: LlmAgent, plugin: MetricsPlugin, run_config: RunConfig | None = None, scoped: bool = True
) -> tuple[RequestMetrics | None, str]:
    runner = Runner(
        app=App(name="fde_test", root_agent=root, plugins=[plugin]), session_service=InMemorySessionService()
    )
    session = await runner.session_service.create_session(app_name="fde_test", user_id="u1")
    message = types.Content(role="user", parts=[types.Part(text="What do I need before MAT224?")])

    async def drive() -> str:
        final = ""
        async for event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=message, run_config=run_config
        ):
            if event.author == root.name and event.is_final_response() and event.content:
                final = "".join(p.text or "" for p in event.content.parts or [])
        return final

    if not scoped:
        return None, await drive()
    with request_scope() as metrics:
        final = await drive()
    return metrics, final


async def test_records_the_whole_request_including_the_child_agent():
    llm = ScriptedLlm(script=script())
    metrics, final = await run(build_tree(llm), MetricsPlugin())
    assert final == "You need MAT102 first."
    assert [c.agent for c in metrics.model_calls] == ["root", "lookup_agent", "lookup_agent", "root"]
    assert metrics.agents == ["root", "lookup_agent"]
    assert metrics.tool_calls == ["lookup_course", "lookup_agent"]  # child's tool finishes before the AgentTool
    assert metrics.retrieved_ids == ["cal:mat224", "cal:mat102"]  # first-seen order, no repeats
    assert metrics.input_tokens == 1000 and metrics.output_tokens == 100
    assert metrics.thinking_tokens == 5 and metrics.cached_tokens == 100
    assert metrics.cost_usd == pytest.approx(EXPECTED_COST)
    assert metrics.tokens_per_second(2.0) == pytest.approx(52.5)
    assert not metrics.limit_hit
    summary = metrics.to_dict(latency_s=2.0)
    assert summary["model_calls"] == 4 and summary["agents"] == ["lookup_agent", "root"]
    assert summary["latency_ms"] == 2000.0


async def test_budget_counts_the_child_agents_calls():
    llm = ScriptedLlm(script=script())
    metrics, final = await run(build_tree(llm), MetricsPlugin(max_model_calls=2))
    # root's call 1 and the child's call 2 run. The child's call 3 is answered by the budget, so the child
    # stops; root's call 4 is answered by the budget too. If child calls didn't count, root would be at 1.
    assert metrics.limit_hit
    assert [c.agent for c in metrics.model_calls] == ["root", "lookup_agent"]
    assert metrics.model_calls_started == 4
    assert len(llm.requests) == 2 and len(llm.script) == 2  # the model was really called only twice
    assert final == DEFAULT_BUDGET_MESSAGE


async def test_adk_max_llm_calls_alone_does_not_cap_the_request():
    # The friction the budget works around: max_llm_calls is per invocation, and the AgentTool child is its
    # own invocation, so 4 calls (2 in root, 2 in the child) pass a limit of 2.
    llm = ScriptedLlm(script=script())
    metrics, final = await run(build_tree(llm), MetricsPlugin(), run_config=RunConfig(max_llm_calls=2))
    assert len(metrics.model_calls) == 4
    assert final == "You need MAT102 first."


async def test_outside_request_scope_the_plugin_records_and_limits_nothing():
    llm = ScriptedLlm(script=script())
    _, final = await run(build_tree(llm), MetricsPlugin(max_model_calls=1), scoped=False)
    assert final == "You need MAT102 first."
    assert current_metrics() is None


async def test_tool_errors_are_counted():
    llm = ScriptedLlm(
        script=[
            tool_call_response("lookup_agent", {"request": "MAT224"}),
            tool_call_response("broken_tool", {"code": "MAT224"}),
            text_response("Couldn't look that up."),
            text_response("Sorry, the lookup failed."),
        ]
    )
    metrics, _ = await run(build_tree(llm, child_tools=[broken_tool]), MetricsPlugin())
    assert metrics.tool_errors == 1
    # raised errors are counted by on_tool_error_callback
    with request_scope() as scoped:
        await MetricsPlugin().on_tool_error_callback(tool=None, tool_args={}, tool_context=None, error=ValueError())
    assert scoped.tool_errors == 1


async def test_span_totals_per_agent(spans, monkeypatch):
    monkeypatch.delenv("ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN", raising=False)  # ADK's default schema (v2)
    llm = ScriptedLlm(script=script())
    metrics, _ = await run(build_tree(llm), MetricsPlugin(attribute_prefix="kit"))
    agent_spans = [s for s in spans.get_finished_spans() if s.name.startswith("invoke_agent")]
    by_agent = {s.attributes["kit.agent"]: s.attributes for s in agent_spans}
    assert set(by_agent) == {"root", "lookup_agent"}
    assert by_agent["root"]["kit.llm_calls"] == 2
    assert by_agent["root"]["kit.input_tokens"] == 500
    assert by_agent["lookup_agent"]["kit.thinking_tokens"] == 5
    assert sum(a["kit.cost_usd"] for a in by_agent.values()) == pytest.approx(metrics.cost_usd)


def test_request_scope_is_isolated_and_restored():
    assert current_metrics() is None
    with request_scope() as outer:
        with request_scope() as inner:
            assert current_metrics() is inner
        assert current_metrics() is outer
    assert current_metrics() is None


def test_collect_ids_with_custom_keys():
    result = {"hits": [{"doc_id": "a"}, {"doc": {"doc_id": "b"}}], "related": {"doc_ids": ["c", 7]}, "chunk_id": "x"}
    assert collect_ids(result, {"doc_id", "doc_ids"}) == ["a", "b", "c"]
