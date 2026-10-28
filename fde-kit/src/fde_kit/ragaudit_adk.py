"""Evaluate an async agent with ragaudit, whose adapters are synchronous.

ragaudit calls `adapter.query(question)` from plain synchronous code, once per golden question, and scores
retrieval (are the expected chunk ids among `retrieved_chunk_ids`?) and the answer. An ADK agent is async.
Calling `asyncio.run()` per question doesn't work for agents that hold loop-bound resources, such as an MCP
stdio session opened on the first question: the next `asyncio.run()` makes a new loop and the session breaks.
So `AdkRagAdapter` runs every question on one long-lived event loop in a background thread.

    async def ask(question: str) -> tuple[str, list[str], dict]:
        with request_scope() as metrics:        # from fde_kit.adk_metrics
            started = time.perf_counter()
            answer = await my_runtime.ask(question)
            latency_s = time.perf_counter() - started
        return answer, metrics.retrieved_ids, metrics.to_dict(latency_s)

    def make_adapter(config: dict) -> AdkRagAdapter:   # what ragaudit looks for in an adapter file
        return AdkRagAdapter(ask, metrics_log=config.get("metrics_log"))

ragaudit is an optional dependency (`pip install "fde-kit[ragaudit]"`), needed only by `query()`;
`run()` returns a plain `RagResult` and works without it.
"""

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ragaudit.models.adapter import RAGResponse

AskFn = Callable[[str], Awaitable[tuple[str, list[str], dict[str, Any]]]]


class LoopThread:
    """One event loop running forever on a daemon thread; `run()` blocks the caller until a coroutine finishes."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        # daemon=True: an eval script that forgets close() still exits instead of hanging on this thread.
        self._thread = threading.Thread(target=self.loop.run_forever, name="fde-kit-loop", daemon=True)
        self._thread.start()

    def run[T](self, coro: Coroutine[Any, Any, T], timeout_s: float | None = None) -> T:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout_s)
        except TimeoutError:
            future.cancel()  # stop the stuck call so it doesn't keep running alongside the next question
            raise

    def close(self) -> None:
        if self.loop.is_closed():
            return
        # Let cancelled or still-running calls unwind on the loop before it stops, so no task is destroyed
        # half-finished (and async generators, like an ADK run, get their cleanup).
        self.run(self._shutdown())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()
        self.loop.close()

    async def _shutdown(self) -> None:
        others = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        await self.loop.shutdown_asyncgens()


@dataclass
class RagResult:
    """What one question produced; `query()` converts it to ragaudit's RAGResponse."""

    answer: str
    retrieved_ids: list[str]
    metrics: dict[str, Any]
    latency_ms: float


def _rag_response_class() -> Any:  # Any: ragaudit may not be installed for the type checker either
    # Imported here, not at module top, so fde_kit.ragaudit_adk imports fine without the extra installed.
    try:
        from ragaudit.models.adapter import RAGResponse
    except ImportError as exc:
        raise ImportError(
            "AdkRagAdapter.query() needs ragaudit. Install it with: pip install 'fde-kit[ragaudit]'"
        ) from exc
    return RAGResponse


class AdkRagAdapter:
    """A ragaudit adapter around any async `ask(question) -> (answer_text, retrieved_ids, metrics_dict)`.

    Args:
        ask: the async function under test. It runs on this adapter's loop, so anything it opens (sessions,
            clients) stays usable for the next question.
        metrics_log: optional JSONL path; one row per question with latency and `metrics_dict`. ragaudit
            doesn't track latency percentiles or cost, so this file is what you compute P95 and $/query from.
        timeout_s: per-question limit; a hung agent raises TimeoutError instead of stalling the whole eval.
        loop: share one LoopThread between adapters; by default each adapter starts its own.
    """

    def __init__(
        self,
        ask: AskFn,
        *,
        metrics_log: str | Path | None = None,
        timeout_s: float | None = None,
        loop: LoopThread | None = None,
    ) -> None:
        self._ask = ask
        self.metrics_log = Path(metrics_log) if metrics_log else None
        self.timeout_s = timeout_s
        self.loop = loop or LoopThread()

    def run(self, question: str) -> RagResult:
        """Answer one question on the background loop. Needs no ragaudit."""
        started = time.perf_counter()
        answer, retrieved_ids, metrics = self.loop.run(self._call(question), self.timeout_s)
        latency_ms = (time.perf_counter() - started) * 1000
        result = RagResult(answer, list(retrieved_ids), dict(metrics), latency_ms)
        if self.metrics_log is not None:
            with self.metrics_log.open("a") as f:
                f.write(json.dumps({"question": question, "latency_ms": round(latency_ms, 1), **metrics}) + "\n")
        return result

    def query(self, question: str) -> "RAGResponse":
        """ragaudit's entry point."""
        response_class = _rag_response_class()
        result = self.run(question)
        return response_class(
            answer=result.answer,
            retrieved_chunk_ids=result.retrieved_ids,
            contexts=[],  # the agent's prompt context isn't exposed; ragaudit scores retrieval by ids
            latency_ms=result.latency_ms,
            raw=result.metrics,
        )

    def close(self) -> None:
        self.loop.close()

    async def _call(self, question: str) -> tuple[str, list[str], dict[str, Any]]:
        # Wrapping in a coroutine lets `ask` be any awaitable-returning callable, not only an `async def`.
        return await self._ask(question)
