from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from waypoint.llm.embeddings import HashEmbedder
from waypoint.llm.mock import MockLlm


def lookup_course(code: str) -> dict:
    """Look up a course by code."""
    return {"code": code, "title": "Linear Algebra II", "chunk_id": "cal:mathematical-sciences#mat224h5"}


async def run_once(agent: LlmAgent, text: str) -> list:
    runner = InMemoryRunner(agent=agent, app_name="test")
    session = await runner.session_service.create_session(app_name="test", user_id="u1")
    message = types.Content(role="user", parts=[types.Part(text=text)])
    return [e async for e in runner.run_async(user_id="u1", session_id=session.id, new_message=message)]


async def test_mock_llm_calls_tool_then_answers_with_citation():
    agent = LlmAgent(name="probe", model=MockLlm(), instruction="Answer.", tools=[lookup_course])
    events = await run_once(agent, "What is MAT224H5 about?")

    calls = [c for e in events for c in e.get_function_calls()]
    assert calls and calls[0].name == "lookup_course"
    assert calls[0].args == {"code": "MAT224"}
    final = events[-1].content.parts[0].text
    assert "[cal:mathematical-sciences#mat224h5]" in final
    assert events[-1].usage_metadata.prompt_token_count > 0


async def test_mock_llm_without_tools_echoes():
    agent = LlmAgent(name="plain", model=MockLlm(), instruction="Answer.")
    events = await run_once(agent, "hello")
    assert "hello" in events[-1].content.parts[0].text


def test_hash_embedder_is_unit_length_and_lexical():
    embedder = HashEmbedder(dim=64)
    a, b, c = embedder.embed_documents(["linear algebra", "linear algebra", "art history"])
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9
    assert a == b
    assert sum(x * y for x, y in zip(a, c, strict=True)) < 0.99
