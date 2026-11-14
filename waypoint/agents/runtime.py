"""Runs the agent tree for one question and returns a structured answer.

A per-request `RequestRecord` (held in a ContextVar) is filled in by `RecorderPlugin`, which ADK calls for
every agent, model call and tool call, including inside the specialists that AgentTool runs. That gives us,
per request: which chunks were retrieved, which tools ran, and token usage per agent (for cost).
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.apps import App
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.telemetry import ContentCapturingMode, TelemetryConfig
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import trace

from waypoint.agents.factory import Dependencies, build_agents
from waypoint.agents.labels import COURSE, done_label, running_label
from waypoint.agents.prompts import BUDGET_MESSAGE
from waypoint.agents.router import RouteDecision, classify
from waypoint.agents.state import MeteredLlm, PruneOldToolOutputs, state_options
from waypoint.cache import AnswerCache, CachedAnswer, build_answer_cache, cache_key
from waypoint.config import Settings, get_settings
from waypoint.llm.embeddings import get_embedder
from waypoint.llm.models import agent_model
from waypoint.observability.cost import cost_usd
from waypoint.observability.tracing import ObservabilityPlugin, hash_user, log_request_metrics, setup_tracing
from waypoint.retrieval.hybrid import HybridRetriever
from waypoint.safety import COURSE_CODE, flag_unknown_codes, redact_pii, scope_check
from waypoint.sources import locate_passage

APP_NAME = "waypoint"
tracer = trace.get_tracer("waypoint")
logger = logging.getLogger(__name__)
FAST_ROUTES = ("policy_agent", "course_data_agent", "timetable_agent", "planner_agent")
CHUNK_ID = r"cal:[a-z0-9-]+#[a-z0-9-]+"
CITATION = re.compile(rf"\[({CHUNK_ID})\]")
# Models sometimes group several ids in one bracket: [cal:a#x, cal:a#y]. Split those into [id][id].
CITATION_GROUP = re.compile(rf"\[\s*({CHUNK_ID}(?:\s*[,;]\s*{CHUNK_ID})+)\s*\]")


@dataclass
class ModelCall:
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int


@dataclass
class RequestRecord:
    """Everything one request did. Filled in by RecorderPlugin as agents, models and tools run."""

    retrieved_chunk_ids: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    model_calls: list[ModelCall] = field(default_factory=list)
    tool_errors: int = 0
    model_calls_started: int = 0
    limit_hit: bool = False
    invalid_citations: list[str] = field(default_factory=list)
    plan_checks: list[bool] = field(default_factory=list)  # critic's check_plan results, in order
    seen_codes: set[str] = field(default_factory=set)  # course codes that appeared in any tool result
    unknown_codes: list[str] = field(default_factory=list)
    pii_redacted: list[str] = field(default_factory=list)
    out_of_scope: bool = False
    # Streaming / routing (DESIGN_SPEC section 6). `emit(kind, data)` pushes a live event to the stream.
    emit: Callable[[str, Any], None] | None = None
    started: float = field(default_factory=time.perf_counter)
    ttft_ms: float | None = None
    route: str = "full"  # fast | full | cache
    category: str = "unsure"  # router class: simple_lookup | timetable | planning | unsure
    router_ms: float = 0.0
    router_calls: int = 0
    cache_hit: bool = False
    tool_starts: dict[str, float] = field(default_factory=dict)

    def send(self, kind: str, data: Any) -> None:
        if self.emit is not None:
            self.emit(kind, data)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.model_calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens + c.thinking_tokens for c in self.model_calls)

    @property
    def cached_tokens(self) -> int:
        return sum(c.cached_tokens for c in self.model_calls)

    @property
    def cost_usd(self) -> float:
        return sum(
            cost_usd(c.model, c.input_tokens, c.output_tokens, c.cached_tokens, c.thinking_tokens)
            for c in self.model_calls
        )


current_record: ContextVar[RequestRecord | None] = ContextVar("waypoint_request_record", default=None)


def _collect_chunk_ids(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "chunk_id" and isinstance(item, str):
                out.append(item)
            elif key in ("chunk_ids", "citations") and isinstance(item, list):
                out.extend(i for i in item if isinstance(i, str))
            else:
                _collect_chunk_ids(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_chunk_ids(item, out)


class RecorderPlugin(BasePlugin):
    """Records each request, and enforces a request-wide model-call budget.

    ADK's RunConfig.max_llm_calls counts calls per invocation, and every AgentTool specialist is its own
    invocation, so it can't cap a whole request. Counting here (the plugin sees every agent) can.
    """

    def __init__(self, max_model_calls: int = 16) -> None:
        super().__init__(name="waypoint_recorder")
        self.max_model_calls = max_model_calls

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        record = current_record.get()
        if record is None:
            return None
        record.model_calls_started += 1
        if record.model_calls_started > self.max_model_calls:
            record.limit_hit = True
            # Skips the model call: the agent receives this text as its final answer and stops.
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=BUDGET_MESSAGE)]))
        return None

    async def before_agent_callback(self, *, agent: BaseAgent, callback_context: CallbackContext) -> None:
        record = current_record.get()
        if record is not None:
            record.agents.append(agent.name)
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        record = current_record.get()
        usage = llm_response.usage_metadata
        if record is not None and usage is not None and not llm_response.partial:
            record.model_calls.append(
                ModelCall(
                    agent=callback_context.agent_name,
                    model=llm_response.model_version or "",
                    input_tokens=usage.prompt_token_count or 0,
                    output_tokens=usage.candidates_token_count or 0,
                    cached_tokens=usage.cached_content_token_count or 0,
                    thinking_tokens=usage.thoughts_token_count or 0,
                )
            )
        return None

    async def before_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext
    ) -> dict | None:
        record = current_record.get()
        if record is not None:
            record.tool_starts[tool_context.function_call_id or tool.name] = time.perf_counter()
            record.send(
                "status",
                {
                    "step": tool.name,
                    "id": tool_context.function_call_id or tool.name,
                    "label": running_label(tool.name, tool_args),
                    "state": "running",
                },
            )
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict
    ) -> dict | None:
        record = current_record.get()
        if record is not None:
            record.tool_calls.append(tool.name)
            call_id = tool_context.function_call_id or tool.name
            started = record.tool_starts.pop(call_id, time.perf_counter())
            record.send(
                "status",
                {
                    "step": tool.name,
                    "id": call_id,
                    "label": done_label(tool.name, tool_args, result),
                    "state": "done",
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )
            ids: list[str] = []
            _collect_chunk_ids(result, ids)
            new_ids = [i for i in dict.fromkeys(ids) if i not in record.retrieved_chunk_ids]
            record.retrieved_chunk_ids += new_ids
            if new_ids:  # sources go out as soon as retrieval finishes, before any answer text
                record.send("sources", list(record.retrieved_chunk_ids))
            if isinstance(result, dict) and result.get("error"):
                record.tool_errors += 1
            record.seen_codes |= set(COURSE_CODE.findall(json.dumps(result, default=str)))
            if tool.name == "check_plan" and isinstance(result, dict):
                record.plan_checks.append(bool(result.get("valid")))
        return None

    async def on_tool_error_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, error: Exception
    ) -> dict | None:
        record = current_record.get()
        if record is not None:
            record.tool_errors += 1
        return None


def check_citations(text: str, retrieved: list[str]) -> tuple[str, list[str], list[str]]:
    """Every citation must point at a chunk a tool actually returned in this request.

    Citations to anything else (a hallucinated or mistyped id) are removed from the text and reported,
    so the UI never shows a source link that doesn't back the claim. Returns (text, valid, invalid).
    """
    allowed = set(retrieved)
    text = CITATION_GROUP.sub(lambda m: "".join(f"[{c}]" for c in re.split(r"\s*[,;]\s*", m.group(1))), text)
    cited = list(dict.fromkeys(CITATION.findall(text)))
    invalid = [c for c in cited if c not in allowed]
    for chunk_id in invalid:
        text = text.replace(f"[{chunk_id}]", "")
    return text, [c for c in cited if c in allowed], invalid


@dataclass
class Answer:
    text: str
    citations: list[str]
    record: RequestRecord
    latency_ms: float
    session_id: str

    def metrics(self) -> dict[str, Any]:
        r = self.record
        seconds = max(self.latency_ms / 1000, 1e-6)
        return {
            "latency_ms": round(self.latency_ms, 1),
            "total_ms": round(self.latency_ms, 1),
            "ttft_ms": round(r.ttft_ms if r.ttft_ms is not None else self.latency_ms, 1),
            "route": r.route,
            "category": r.category,
            "router_ms": round(r.router_ms, 1),
            "cache_hit": r.cache_hit,
            # Router calls are billed model calls too; cached answers make none.
            "model_calls": len(r.model_calls) + r.router_calls,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cached_tokens": r.cached_tokens,
            "cache_hit_rate": round(r.cached_tokens / r.input_tokens, 4) if r.input_tokens else 0.0,
            "tokens_per_second": round(r.output_tokens / seconds, 2),
            "cost_usd": round(r.cost_usd, 6),
            "tool_calls": len(r.tool_calls),
            "tool_errors": r.tool_errors,
            "agents": sorted(set(r.agents)),
            "retrieved_chunks": len(r.retrieved_chunk_ids),
            "citations": len(self.citations),
            "invalid_citations": len(r.invalid_citations),
            "limit_hit": r.limit_hit,
            "out_of_scope": r.out_of_scope,
            "pii_redacted": r.pii_redacted,
            "unknown_codes": len(r.unknown_codes),
            "critic_rounds": r.agents.count("plan_critic"),
            # Plan validity as the critic's deterministic check saw it: first draft vs. the plan that shipped.
            "plan_valid_first": r.plan_checks[0] if r.plan_checks else None,
            "plan_valid_final": r.plan_checks[-1] if r.plan_checks else None,
        }


def build_dependencies(settings: Settings) -> Dependencies:
    """Real backends when configured (Cloud SQL, BigQuery); otherwise offline ones from data/corpus."""
    from waypoint.ingest.load import load_chunks, read_corpus
    from waypoint.retrieval.store import ChunkStore, InMemoryStore, PgVectorStore
    from waypoint.tools.catalog import BigQueryCatalog, CachedCatalog, Catalog, InMemoryCatalog

    embedder = get_embedder(settings)
    store: ChunkStore
    if settings.store == "pgvector":
        store = PgVectorStore(settings)
    else:
        store = InMemoryStore()
        load_chunks(store, read_corpus(Path("data/corpus/calendar_chunks.jsonl")), embedder)
    catalog: Catalog
    if settings.catalog == "bigquery":
        catalog = CachedCatalog(BigQueryCatalog(settings))
    elif settings.catalog == "toolbox":
        from waypoint.tools.toolbox import ToolboxCatalog

        catalog = CachedCatalog(ToolboxCatalog(settings.toolbox_url))
    else:
        catalog = InMemoryCatalog()
    return Dependencies(retriever=HybridRetriever(store, embedder), catalog=catalog, settings=settings)


def build_session_service(settings: Settings) -> BaseSessionService:
    if settings.sessions == "firestore":
        from google.adk.integrations.firestore import FirestoreSessionService
        from google.cloud import firestore

        return FirestoreSessionService(client=firestore.AsyncClient(project=settings.gcp_project))
    return InMemorySessionService()


class WaypointRuntime:
    def __init__(
        self,
        agent: LlmAgent,
        settings: Settings | None = None,
        session_service: BaseSessionService | None = None,
        plugins: list[BasePlugin] | None = None,
        route_agents: dict[str, BaseAgent] | None = None,
        cache: AnswerCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache
        self.deps: Dependencies | None = None
        self.session_service = session_service or InMemorySessionService()
        recorder = RecorderPlugin(self.settings.max_model_calls_per_request)
        all_plugins: list[BasePlugin] = [recorder, ObservabilityPlugin(), *(plugins or [])]
        options: dict[str, Any] = {}
        self.summarizer: MeteredLlm | None = None
        if self.settings.state_opt:
            if self.settings.prune_tool_outputs:
                all_plugins.append(PruneOldToolOutputs(agent.name))
            options, self.summarizer = state_options(
                agent_model(self.settings),
                token_threshold=self.settings.compaction_token_threshold,
                keep_events=self.settings.compaction_keep_events,
                compaction_interval=self.settings.compaction_interval,
                overlap=self.settings.compaction_overlap,
                explicit_cache=self.settings.explicit_cache,
            )
        self.app = App(name=APP_NAME, root_agent=agent, plugins=all_plugins, **options)
        self.root_agent_name = agent.name
        self.runner = Runner(app=self.app, session_service=self.session_service)
        # Fast-path roots: one specialist (or the planner) answers directly, skipping the orchestrator's
        # delegate-then-rewrite round trips. They share the session service and the recording plugins.
        self.fast_runners: dict[str, tuple[Runner, str]] = {}
        for key, fast_agent in (route_agents or {}).items():
            fast_app = App(name=APP_NAME, root_agent=fast_agent, plugins=all_plugins)
            self.fast_runners[key] = (Runner(app=fast_app, session_service=self.session_service), fast_agent.name)
        # Message content in spans is decided here, per request, not by env vars: on ADK 2.13 the GenAI spans
        # follow OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT, which ADK_CAPTURE_... doesn't override.
        capture = ContentCapturingMode.SPAN_ONLY if self.settings.trace_content else ContentCapturingMode.NO_CONTENT
        self._telemetry = TelemetryConfig(capture_message_content=capture)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "WaypointRuntime":
        settings = settings or get_settings()
        setup_tracing(settings)
        deps = build_dependencies(settings)
        agents = build_agents(deps)
        runtime = cls(
            agents["waypoint"],  # type: ignore[arg-type]
            settings,
            build_session_service(settings),
            route_agents={k: agents[k] for k in FAST_ROUTES},
            cache=build_answer_cache(settings),
        )
        runtime.deps = deps
        return runtime

    def citation_sources(self, chunk_ids: list[str]) -> list[dict[str, str]]:
        """Heading and calendar URL for each cited chunk, in citation order."""
        if not chunk_ids or self.deps is None:
            return []
        found = {c.chunk_id: c for c in self.deps.retriever.store.get(chunk_ids)}
        return [
            {"chunk_id": cid, "heading": found[cid].heading, "url": found[cid].source_url}
            for cid in chunk_ids
            if cid in found
        ]

    def source_detail(self, chunk_id: str, claim: str = "") -> dict[str, Any] | None:
        """Everything the Source reader shows for one chunk: its exact text, the line that backs `claim`
        (computed from the text, see waypoint/sources.py), and the official calendar URL."""
        if self.deps is None:
            return None
        found = self.deps.retriever.store.get([chunk_id])
        if not found:
            return None
        chunk = found[0]
        parts = chunk.heading.split(" > ")
        match = re.match(r"([A-Z]{3}\d{3}[HY]\d)\s+(.*)", parts[-1])
        passage = locate_passage(chunk.text, claim) if claim else None
        if passage:
            why = f"Pip cited this entry for: “{claim.strip()[:240]}”. The highlighted line shares: {', '.join(passage.matched)}."
        elif claim:
            why = "Pip retrieved this entry for the question, but no single line matches the claim closely enough to highlight."
        else:
            why = "Pip retrieved this entry while searching for your question."
        return {
            "chunk_id": chunk.chunk_id,
            "heading": chunk.heading,
            "breadcrumb": ["Academic Calendar", *parts],
            "code": match.group(1) if match else "",
            "title": match.group(2) if match else parts[-1],
            "text": chunk.text,
            "highlight": {"start": passage.start, "end": passage.end} if passage else None,
            "why": why,
            "url": chunk.source_url,
        }

    def source_rows(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """Rows for the `sources` event: number, course code and title when the chunk is a course, and the
        calendar URL. `n` is the retrieval order; the text cites by chunk id and the UI maps it to `n`."""
        if not chunk_ids or self.deps is None:
            return []
        found = {c.chunk_id: c for c in self.deps.retriever.store.get(chunk_ids)}
        rows: list[dict[str, Any]] = []
        for cid in chunk_ids:
            chunk = found.get(cid)
            if chunk is None:
                continue
            tail = chunk.heading.split(" > ")[-1]
            match = re.match(r"([A-Z]{3}\d{3}[HY]\d)\s+(.*)", tail)
            rows.append(
                {
                    "n": len(rows) + 1,
                    "code": match.group(1) if match else "",
                    "title": match.group(2) if match else tail,
                    "chunk_id": cid,
                    "heading": chunk.heading,
                    "url": chunk.source_url,
                }
            )
        return rows

    def _cache_lookup(self, text: str, session_new: bool, context: str) -> tuple[str | None, CachedAnswer | None]:
        if self.cache is None or not session_new:
            return None, None
        key = cache_key(text, self.cache.version(), context)
        return key, self.cache.get(key)

    async def ask(self, user_id: str, text: str, session_id: str | None = None) -> Answer:
        """Non-streaming entry point: drains `ask_stream` and returns the final Answer."""
        answer: Answer | None = None
        async for kind, data in self.ask_stream(user_id, text, session_id):
            if kind == "final":
                answer = data
            elif kind == "error":
                raise RuntimeError(data["message"])
        assert answer is not None
        return answer

    async def ask_stream(
        self, user_id: str, text: str, session_id: str | None = None, context: str = ""
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yields (kind, data): status, sources, token, then ("final", Answer). Or ("error", {...}).

        The agents run in a worker task that pushes live events onto a queue; this generator forwards them.
        `context` (e.g. a profile hash) is folded into the answer-cache key so personalised answers never
        cross users."""
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        record = RequestRecord(emit=lambda kind, data: queue.put_nowait((kind, data)))

        async def worker() -> None:
            try:
                answer = await self._answer(user_id, text, session_id, record, context)
                queue.put_nowait(("final", answer))
            except Exception as exc:
                logger.exception("request failed")
                message = "Something went wrong answering that. Please try again."
                queue.put_nowait(("error", {"message": message, "type": type(exc).__name__}))
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(worker())
        sent_ids = 0
        try:
            while (item := await queue.get()) is not None:
                kind, data = item
                if kind == "sources":  # data: chunk ids retrieved so far -> rows (blocking store call off the loop)
                    if len(data) == sent_ids:
                        continue
                    sent_ids = len(data)
                    data = await asyncio.to_thread(self.source_rows, data)
                yield kind, data
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _answer(
        self, user_id: str, text: str, session_id: str | None, record: RequestRecord, context: str
    ) -> Answer:
        started = record.started
        session = None
        if session_id:
            session = await self.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        session_new = session is None
        if session is None:
            session = await self.session_service.create_session(app_name=APP_NAME, user_id=user_id)

        # PII never reaches the model, the session store or the traces.
        text, record.pii_redacted = redact_pii(text)
        token = current_record.set(record)
        final_text = ""
        with tracer.start_as_current_span("waypoint.request") as span:
            span.set_attribute("langfuse.trace.name", "waypoint.request")
            span.set_attribute("langfuse.user.id", hash_user(user_id))
            span.set_attribute("langfuse.session.id", session.id)
            cache_key_value: str | None = None
            try:
                if (reply := scope_check(text)) is not None:
                    record.out_of_scope = True
                    final_text = reply
                    self._stream_text(record, final_text)
                else:
                    cache_key_value, hit = await asyncio.to_thread(self._cache_lookup, text, session_new, context)
                    if hit is not None:
                        record.cache_hit, record.route = True, "cache"
                        record.retrieved_chunk_ids = list(hit.chunk_ids)
                        # Cached only when every code in it was verified, so don't re-flag them on a hit.
                        record.seen_codes = set(COURSE_CODE.findall(hit.text))
                        label = "Found a recent answer to this question"
                        record.send("status", {"step": "cache", "label": label, "state": "done", "ms": 0})
                        record.send("sources", list(hit.chunk_ids))
                        final_text = hit.text
                        self._stream_text(record, final_text)
                    else:
                        final_text = await self._run(user_id, session.id, text, record, len(session.events) > 0)
            finally:
                current_record.reset(token)
            latency_ms = (time.perf_counter() - started) * 1000
            final_text, citations, invalid = check_citations(final_text, record.retrieved_chunk_ids)
            record.invalid_citations = invalid
            known = record.seen_codes | set(COURSE_CODE.findall(text))
            final_text, record.unknown_codes = flag_unknown_codes(final_text, known)
            final_text, _ = redact_pii(final_text)
            answer = Answer(final_text, citations, record, latency_ms, session.id)
            if (
                cache_key_value
                and self.cache is not None
                and not record.cache_hit
                and final_text
                and not (record.out_of_scope or record.limit_hit or record.tool_errors or record.unknown_codes)
            ):
                entry = CachedAnswer(final_text, list(record.retrieved_chunk_ids), record.route)
                await asyncio.to_thread(self._cache_put, cache_key_value, entry)
            metrics = answer.metrics()
            for key, value in metrics.items():
                if value is not None:  # OTel attributes can't be None (plan validity is None for non-plans)
                    span.set_attribute(f"waypoint.{key}", value)
            log_request_metrics({"user": hash_user(user_id), "session": session.id, **metrics})
        return answer

    def _cache_put(self, key: str, entry: CachedAnswer) -> None:
        try:
            assert self.cache is not None
            self.cache.put(key, entry)
        except Exception:  # the cache must never break an answer
            logger.exception("answer cache write failed")

    @staticmethod
    def _stream_text(record: RequestRecord, text: str) -> None:
        """Answers that didn't come from a live model stream (cache hit, scope reply) still arrive as tokens."""
        for piece in re.findall(r"\S+\s*", text):
            if record.ttft_ms is None:
                record.ttft_ms = (time.perf_counter() - record.started) * 1000
            record.send("token", {"text": piece})

    async def _route(self, text: str, has_history: bool) -> RouteDecision:
        """Router decision. A short follow-up in an ongoing conversation ("what about MAT301?") may lean on
        earlier turns that a direct specialist wouldn't weigh as carefully, so it takes the full path."""
        if not self.fast_runners:
            return RouteDecision("unsure", "", source="fallback")
        if has_history and len(text.split()) < 8 and not COURSE.search(text):
            return RouteDecision("unsure", "", source="fallback")
        return await classify(text, self.settings)

    def _fast_key(self, decision: RouteDecision) -> str | None:
        key = {
            "simple_lookup": decision.specialist,
            "timetable": "timetable_agent",
            "planning": "planner_agent",
        }.get(decision.category)
        return key if key in self.fast_runners else None

    async def _run(self, user_id: str, session_id: str, text: str, record: RequestRecord, has_history: bool) -> str:
        running = {"step": "route", "id": "route", "label": "Understanding your question", "state": "running"}
        record.send("status", running)
        decision = await self._route(text, has_history)
        record.category, record.router_ms, record.router_calls = decision.category, decision.ms, decision.model_calls
        fast_key = self._fast_key(decision)
        record.route = "fast" if fast_key else "full"
        record.send(
            "status",
            {
                "step": "route",
                "id": "route",
                "label": "Chose a direct path" if fast_key else "Chose the full team of specialists",
                "state": "done",
                "ms": round(decision.ms),
            },
        )
        runner, root_name = self.fast_runners[fast_key] if fast_key else (self.runner, self.root_agent_name)
        final_text = ""
        streamed = ""
        try:
            message = types.Content(role="user", parts=[types.Part(text=text)])
            run_config = RunConfig(
                max_llm_calls=self.settings.max_model_calls_per_request,
                telemetry=self._telemetry,
                streaming_mode=StreamingMode.SSE,
            )
            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=message, run_config=run_config
            ):
                if event.author != root_name or not event.content:
                    continue
                texts = [p.text for p in event.content.parts or [] if p.text and not p.thought]
                has_call = any(p.function_call for p in event.content.parts or [])
                if event.partial and texts and not has_call:
                    chunk = "".join(texts)
                    streamed += chunk
                    if record.ttft_ms is None:
                        record.ttft_ms = (time.perf_counter() - record.started) * 1000
                    record.send("token", {"text": chunk})
                elif event.is_final_response() and texts:
                    final_text = "\n".join(texts)
            if final_text and not streamed:  # the model didn't stream: send what we have in word-sized pieces
                self._stream_text(record, final_text)
        except LlmCallsLimitExceededError:
            record.limit_hit = True
            final_text = (
                "That question needed more steps than Waypoint allows per request. Try asking about one "
                "program, course or term at a time."
            )
        return final_text
