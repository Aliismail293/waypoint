import pytest

from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import Settings
from waypoint.observability.cost import cost_usd, price_for


def test_cost_model():
    assert price_for("gemini-3.5-flash-lite-001").input == 0.30
    assert price_for("gemini-3.5-flash").input == 1.50
    # 1M uncached input + 1M output on Flash = 1.50 + 9.00
    assert cost_usd("gemini-3.5-flash", 1_000_000, 1_000_000) == pytest.approx(10.5)
    # cached input is cheaper; thinking tokens bill as output
    assert cost_usd("gemini-3.5-flash", 1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.15)
    assert cost_usd("gemini-3.5-flash", 0, 0, thinking_tokens=1_000_000) == pytest.approx(9.0)


async def test_request_span_and_llm_spans_carry_metrics(spans):
    exporter = spans
    runtime = WaypointRuntime.from_settings(Settings(llm="mock", embeddings="mock"))
    answer = await runtime.ask("student@example.com", "What are the prerequisites for MAT224?")
    spans = exporter.get_finished_spans()
    request = next(s for s in spans if s.name == "waypoint.request")
    assert request.attributes["waypoint.cost_usd"] == pytest.approx(answer.record.cost_usd, rel=1e-3)
    assert request.attributes["waypoint.model_calls"] == len(answer.record.model_calls)
    assert "student@example.com" not in str(dict(request.attributes))  # pseudonymous user id only
    agent_spans = [s for s in spans if s.name.startswith("invoke_agent")]
    assert {s.attributes["waypoint.agent"] for s in agent_spans} == {"waypoint", "course_data_agent"}
    assert sum(s.attributes["waypoint.llm_calls"] for s in agent_spans) == len(answer.record.model_calls)
    assert sum(s.attributes["waypoint.cost_usd"] for s in agent_spans) == pytest.approx(answer.record.cost_usd)
    assert all("gen_ai.usage.input_tokens" in s.attributes for s in spans if s.name.startswith("generate_content"))
    assert any(s.name.startswith("execute_tool") for s in spans)  # ADK's own tool spans
    assert answer.metrics()["tokens_per_second"] > 0
