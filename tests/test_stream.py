"""Streaming (SSE), the fast-path router and the answer cache, all offline with the mock LLM."""

import json

import pytest
from fastapi.testclient import TestClient

from waypoint.agents.router import RouteDecision, heuristic_route, parse_decision
from waypoint.agents.runtime import WaypointRuntime
from waypoint.api import auth
from waypoint.api.app import create_app
from waypoint.cache import CachedAnswer, MemoryAnswerCache, cache_key, normalise
from waypoint.config import Settings

# fact_route off: these tests exercise the agent/router/stream paths; the fact route has tests/test_facts.py
OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory", fact_route="off")


def parse_sse(body: str) -> list[tuple[str, object]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


@pytest.fixture
def stream_client(monkeypatch):
    monkeypatch.setattr(
        auth.id_token,
        "verify_oauth2_token",
        lambda token, req, audience=None: {
            "iss": "https://accounts.google.com",
            "aud": "cid",
            "email": "s@mail.utoronto.ca",
            "email_verified": True,
            "sub": "1",
        },
    )
    settings = Settings(**OFFLINE, google_client_id="cid")
    with TestClient(create_app(settings, WaypointRuntime.from_settings(settings))) as client:
        yield client


def post(client, message, **extra):
    return client.post("/api/chat/stream", json={"message": message, **extra}, headers={"Authorization": "Bearer t"})


def test_stream_headers_and_event_order(stream_client):
    response = post(stream_client, "What are the prerequisites for MAT224?")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"].startswith("no-cache")
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds[-2:] == ["final", "done"]
    # sources arrive before the first token; tokens before the final text
    assert kinds.index("sources") < kinds.index("token") < kinds.index("final")
    assert {k for k in kinds} <= {"status", "sources", "token", "final", "done"}


def test_status_events_have_labels_and_pair_up(stream_client):
    events = parse_sse(post(stream_client, "What are the prerequisites for MAT224?").text)
    statuses = [d for k, d in events if k == "status"]
    assert statuses[0]["state"] == "running" and statuses[0]["label"] == "Understanding your question"
    by_id: dict[str, list[str]] = {}
    for s in statuses:
        by_id.setdefault(s["id"], []).append(s["state"])
    assert all(states == ["running", "done"] for states in by_id.values())
    assert any(s["label"].startswith("Looked up MAT224") and "ms" in s for s in statuses)


def test_done_event_reports_route_and_timings(stream_client):
    events = parse_sse(post(stream_client, "What are the prerequisites for MAT224?").text)
    done = events[-1][1]
    assert done["route"] == "fast" and done["category"] == "simple_lookup"
    assert 0 <= done["ttft_ms"] <= done["total_ms"]
    # fast path: course_data_agent alone (tool call + answer), no orchestrator hop
    assert done["model_calls"] == 2


def test_tokens_reassemble_to_the_answer(stream_client):
    events = parse_sse(post(stream_client, "What are the prerequisites for MAT224?").text)
    streamed = "".join(d["text"] for k, d in events if k == "token")
    final = next(d for k, d in events if k == "final")
    assert streamed.split() == final["answer"].split() or final["answer"].split()[:5] == streamed.split()[:5]
    assert final["citations"][0]["chunk_id"] == "cal:mathematical-sciences#mat224h5"
    sources = [d for k, d in events if k == "sources"][0]
    assert sources[0]["n"] == 1 and sources[0]["url"].startswith("https://")


def test_stream_requires_sign_in():
    settings = Settings(**OFFLINE, google_client_id="cid")
    with TestClient(create_app(settings, WaypointRuntime.from_settings(settings))) as client:
        assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401


def test_error_event_when_the_runtime_fails(monkeypatch, stream_client):
    async def boom(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(WaypointRuntime, "_run", boom)
    events = parse_sse(post(stream_client, "What are the prerequisites for MAT224?").text)
    assert events[-1][0] == "error" and "try again" in events[-1][1]["message"]
    assert "model unavailable" not in json.dumps(events)  # no internals leak to the student


def test_out_of_scope_reply_streams_without_model_calls(stream_client):
    events = parse_sse(post(stream_client, "Write my essay on Hamlet please").text)
    assert events[-1][1]["model_calls"] == 0
    assert any(k == "token" for k, _ in events)


# --- router -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "category"),
    [
        ("What are the prerequisites for MAT224H5?", "simple_lookup"),
        ("What is the minimum cGPA to stay in good standing?", "simple_lookup"),
        ("Which MAT224 sections are on Tuesday afternoons?", "timetable"),
        ("Make me a term-by-term plan to finish the math minor", "planning"),
        ("Compare MAT224 and MAT223 and also tell me the credits", "unsure"),
        ("hello there", "unsure"),
    ],
)
def test_heuristic_router(question, category):
    assert heuristic_route(question).category == category


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "{}", '{"route": "banana", "specialist": ""}', '{"route": "simple_lookup", "specialist": "x"}'],
)
def test_router_falls_back_to_the_full_path_on_bad_output(raw):
    assert parse_decision(raw).category == "unsure"


def test_parse_decision_maps_specialists():
    decision = parse_decision('{"route": "simple_lookup", "specialist": "policy"}')
    assert (decision.category, decision.specialist, decision.fast) == ("simple_lookup", "policy_agent", True)
    assert parse_decision('{"route": "planning", "specialist": ""}').fast


async def test_unsure_takes_the_full_orchestrator_path():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    answer = await runtime.ask("u", "hello there, what can you do?")
    assert answer.record.route == "full" and "waypoint" in answer.record.agents


async def test_router_off_always_takes_the_full_path():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE, router="off"))
    answer = await runtime.ask("u", "What are the prerequisites for MAT224?")
    assert answer.record.route == "full" and answer.record.agents[0] == "waypoint"


async def test_fast_path_skips_the_orchestrator():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    answer = await runtime.ask("u", "What are the prerequisites for MAT224?")
    assert answer.record.route == "fast" and "waypoint" not in answer.record.agents
    assert len(answer.record.model_calls) == 2


async def test_short_follow_up_takes_the_full_path():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    first = await runtime.ask("u", "What are the prerequisites for MAT224?")
    again = await runtime.ask("u", "and the exclusions?", session_id=first.session_id)
    assert again.record.route == "full"


async def test_router_failure_does_not_fail_the_request(monkeypatch):
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))

    async def failing(text, settings):
        return RouteDecision("unsure", "", model_calls=1, source="fallback")

    monkeypatch.setattr("waypoint.agents.runtime.classify", failing)
    answer = await runtime.ask("u", "What are the prerequisites for MAT224?")
    assert answer.record.route == "full" and answer.text


# --- answer cache -----------------------------------------------------------------------------------------


def test_normalise_and_key():
    assert normalise("  What are the PREREQS for MAT224?? ") == "what are the prereqs for mat224"
    assert cache_key("MAT224 prereqs?", "1") == cache_key("mat224   prereqs", "1")
    assert cache_key("x", "1") != cache_key("x", "2")  # a re-ingest changes the version
    assert cache_key("x", "1", "profile-a") != cache_key("x", "1", "profile-b")


def test_cache_expires_after_ttl():
    cache = MemoryAnswerCache(ttl_s=60)
    cache.put("k", CachedAnswer("a", [], stored_at=0.0))  # stored at the epoch: long expired
    assert cache.get("k") is None
    cache.put("k2", CachedAnswer("a", []))
    assert cache.get("k2") is not None


async def test_identical_question_is_served_from_cache():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    first = await runtime.ask("alice", "What are the prerequisites for MAT224?")
    second = await runtime.ask("bob", "what are the PREREQUISITES for mat224")
    assert not first.record.cache_hit and second.record.cache_hit
    assert second.record.route == "cache" and not second.record.model_calls
    assert second.text == first.text and second.citations == first.citations
    assert second.metrics()["model_calls"] == 0


async def test_cache_is_skipped_for_follow_ups():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    first = await runtime.ask("alice", "What are the prerequisites for MAT224?")
    await runtime.ask("alice", "What are the prerequisites for MAT301?", session_id=first.session_id)
    again = await runtime.ask("bob", "What are the prerequisites for MAT301?", session_id=first.session_id)
    assert not again.record.cache_hit  # bob's new session isn't alice's; and follow-ups never write the cache


async def test_reingest_invalidates_the_cache():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    await runtime.ask("alice", "What are the prerequisites for MAT224?")
    runtime.cache.bump()  # what `python -m waypoint.ingest` does in production (Firestore version doc)
    again = await runtime.ask("bob", "What are the prerequisites for MAT224?")
    assert not again.record.cache_hit


async def test_out_of_scope_and_errors_are_not_cached():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE))
    await runtime.ask("alice", "Write my essay on Hamlet please")
    again = await runtime.ask("bob", "Write my essay on Hamlet please")
    assert not again.record.cache_hit


async def test_cache_can_be_turned_off():
    runtime = WaypointRuntime.from_settings(Settings(**OFFLINE, answer_cache="off"))
    await runtime.ask("alice", "What are the prerequisites for MAT224?")
    again = await runtime.ask("bob", "What are the prerequisites for MAT224?")
    assert runtime.cache is None and not again.record.cache_hit
