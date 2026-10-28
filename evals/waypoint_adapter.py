"""ragaudit adapter for Waypoint.

ragaudit calls `make_adapter(config).query(question)` for every golden question and scores:
- retrieval: is the question's source chunk id among `retrieved_chunk_ids` (recall@k, MRR)?
- answer: does an LLM judge accept `answer` against the expected answer?

Config knobs (ragaudit `--config` YAML):
- mode: "agent" (default) runs the full multi-agent Waypoint for each question;
        "retrieval" runs only hybrid search and returns the top passage as the answer (cheap, offline-able).
- retrieval_mode: "hybrid" | "vector" | "keyword" (retrieval mode only; for ablations).
- top_k: passages returned by search (default 5).

Every query also appends latency / tokens / cost to the JSONL file named by WAYPOINT_EVAL_METRICS_LOG,
which evals/run_eval.py turns into P95 latency and cost per query (ragaudit doesn't track those).
"""

import asyncio
import json
import os
import threading
import time
from typing import Any

from ragaudit.models.adapter import RAGResponse

from waypoint.agents.runtime import WaypointRuntime, build_dependencies
from waypoint.config import get_settings


class _LoopThread:
    """One long-lived event loop: the agent's MCP stdio session is bound to the loop that opened it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


def _log_metrics(row: dict[str, Any]) -> None:
    path = os.environ.get("WAYPOINT_EVAL_METRICS_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")


class WaypointAdapter:
    def __init__(self, config: dict) -> None:
        self.mode = config.get("mode", "agent")
        self.retrieval_mode = config.get("retrieval_mode", "hybrid")
        self.top_k = int(config.get("top_k", 5))
        settings = get_settings()
        if self.mode == "agent":
            self._loop = _LoopThread()
            self._runtime = WaypointRuntime.from_settings(settings)
        else:
            self._retriever = build_dependencies(settings).retriever

    def query(self, question: str) -> RAGResponse:
        if self.mode == "agent":
            answer = self._loop.run(self._runtime.ask("ragaudit", question))
            metrics = answer.metrics()
            _log_metrics({"question": question, **metrics})
            retrieved = answer.record.retrieved_chunk_ids
            return RAGResponse(
                answer=answer.text,
                retrieved_chunk_ids=retrieved,
                contexts=[],
                latency_ms=answer.latency_ms,
                raw=metrics,
            )
        started = time.perf_counter()
        results = self._retriever.search(question, top_k=self.top_k, mode=self.retrieval_mode)
        latency_ms = (time.perf_counter() - started) * 1000
        _log_metrics({"question": question, "latency_ms": latency_ms, "cost_usd": 0.0})
        return RAGResponse(
            answer=results[0].chunk.text if results else "",
            retrieved_chunk_ids=[r.chunk.chunk_id for r in results],
            contexts=[r.chunk.text for r in results],
            latency_ms=latency_ms,
        )


def make_adapter(config: dict) -> WaypointAdapter:
    return WaypointAdapter(config)
