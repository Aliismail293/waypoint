"""AdkRagAdapter with a fake async ask(). ragaudit itself is optional: faked via sys.modules, or skipped."""

import asyncio
import json
import sys
import threading
import types
from dataclasses import dataclass, field

import pytest

from fde_kit.ragaudit_adk import AdkRagAdapter, LoopThread, RagResult


class FakeAgent:
    """Holds a loop-bound resource, like an MCP stdio session opened on the first question."""

    def __init__(self) -> None:
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.lock: asyncio.Lock | None = None

    async def ask(self, question: str) -> tuple[str, list[str], dict]:
        self.loops.append(asyncio.get_running_loop())
        self.lock = self.lock or asyncio.Lock()
        async with self.lock:  # fails if a later question ran on a different loop than the first
            await asyncio.sleep(0)
        return f"answer to {question}", ["c1", "c2"], {"cost_usd": 0.01, "model_calls": 2}


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
def adapter(agent, tmp_path):
    adapter = AdkRagAdapter(agent.ask, metrics_log=tmp_path / "metrics.jsonl")
    yield adapter
    adapter.close()


def test_run_returns_a_plain_result_without_ragaudit(adapter, tmp_path):
    result = adapter.run("q1")
    assert isinstance(result, RagResult)
    assert result.answer == "answer to q1" and result.retrieved_ids == ["c1", "c2"]
    assert result.metrics["cost_usd"] == 0.01 and result.latency_ms >= 0
    rows = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    assert rows[0]["question"] == "q1" and rows[0]["model_calls"] == 2 and "latency_ms" in rows[0]


def test_every_question_runs_on_the_same_background_loop(adapter, agent):
    adapter.run("q1")
    adapter.run("q2")
    assert agent.loops[0] is agent.loops[1]
    assert agent.loops[0] is adapter.loop.loop


def test_query_returns_ragaudits_response_type(adapter, monkeypatch):
    @dataclass
    class FakeRAGResponse:  # same fields as ragaudit.models.adapter.RAGResponse
        answer: str
        retrieved_chunk_ids: list[str] = field(default_factory=list)
        contexts: list[str] = field(default_factory=list)
        latency_ms: float | None = None
        raw: dict | None = None

    module = types.ModuleType("ragaudit.models.adapter")
    module.RAGResponse = FakeRAGResponse
    monkeypatch.setitem(sys.modules, "ragaudit", types.ModuleType("ragaudit"))
    monkeypatch.setitem(sys.modules, "ragaudit.models", types.ModuleType("ragaudit.models"))
    monkeypatch.setitem(sys.modules, "ragaudit.models.adapter", module)

    response = adapter.query("q1")
    assert isinstance(response, FakeRAGResponse)
    assert response.retrieved_chunk_ids == ["c1", "c2"] and response.raw["model_calls"] == 2


def test_query_without_ragaudit_explains_how_to_install(adapter, monkeypatch):
    monkeypatch.setitem(sys.modules, "ragaudit", None)  # makes `import ragaudit...` raise ImportError
    with pytest.raises(ImportError, match=r"fde-kit\[ragaudit\]"):
        adapter.query("q1")


def test_query_with_real_ragaudit(adapter):
    adapter_module = pytest.importorskip("ragaudit.models.adapter")
    response = adapter.query("q1")
    assert isinstance(response, adapter_module.RAGResponse)
    assert response.answer == "answer to q1"


def test_timeout_cancels_a_hung_question():
    started = threading.Event()

    async def hangs(question: str) -> tuple[str, list[str], dict]:
        started.set()
        await asyncio.sleep(60)
        return "", [], {}

    adapter = AdkRagAdapter(hangs, timeout_s=0.05)
    try:
        with pytest.raises(TimeoutError):
            adapter.run("q")
        assert started.is_set()
    finally:
        adapter.close()


def test_loop_thread_can_be_shared_and_closed():
    loop = LoopThread()

    async def answer(question: str) -> tuple[str, list[str], dict]:
        return question.upper(), [], {}

    first, second = AdkRagAdapter(answer, loop=loop), AdkRagAdapter(answer, loop=loop)
    assert first.run("a").answer == "A" and second.run("b").answer == "B"
    loop.close()
    loop.close()  # idempotent
    assert loop.loop.is_closed()
