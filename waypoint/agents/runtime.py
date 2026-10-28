"""Runs the agent tree for one question and returns a structured answer.

A per-request `RequestRecord` (held in a ContextVar) is filled in by `RecorderPlugin`, which ADK calls for
every agent, model call and tool call, including inside the specialists that AgentTool runs. That gives us,
per request: which chunks were retrieved, which tools ran, and token usage per agent (for cost).
"""

import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import trace

from waypoint.agents.factory import Dependencies, build_root_agent
from waypoint.agents.prompts import BUDGET_MESSAGE
from waypoint.config import Settings, get_settings
from waypoint.llm.embeddings import get_embedder
from waypoint.observability.cost import cost_usd
from waypoint.observability.tracing import ObservabilityPlugin, hash_user, log_request_metrics, setup_tracing
from waypoint.retrieval.hybrid import HybridRetriever

APP_NAME = "waypoint"
tracer = trace.get_tracer("waypoint")
CITATION = re.compile(r"\[(cal:[a-z0-9-]+#[a-z0-9-]+)\]")


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

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict
    ) -> dict | None:
        record = current_record.get()
        if record is not None:
            record.tool_calls.append(tool.name)
            ids: list[str] = []
            _collect_chunk_ids(result, ids)
            record.retrieved_chunk_ids += [i for i in ids if i not in record.retrieved_chunk_ids]
            if isinstance(result, dict) and result.get("error"):
                record.tool_errors += 1
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
            "model_calls": len(r.model_calls),
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
    catalog: Catalog = CachedCatalog(BigQueryCatalog(settings)) if settings.catalog == "bigquery" else InMemoryCatalog()
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
    ) -> None:
        self.settings = settings or get_settings()
        self.deps: Dependencies | None = None
        self.session_service = session_service or InMemorySessionService()
        recorder = RecorderPlugin(self.settings.max_model_calls_per_request)
        self.app = App(name=APP_NAME, root_agent=agent, plugins=[recorder, ObservabilityPlugin(), *(plugins or [])])
        self.root_agent_name = agent.name
        self.runner = Runner(app=self.app, session_service=self.session_service)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "WaypointRuntime":
        settings = settings or get_settings()
        setup_tracing(settings)
        deps = build_dependencies(settings)
        runtime = cls(build_root_agent(deps), settings, build_session_service(settings))
        runtime.deps = deps
        return runtime

    async def ask(self, user_id: str, text: str, session_id: str | None = None) -> Answer:
        session = None
        if session_id:
            session = await self.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        if session is None:
            session = await self.session_service.create_session(app_name=APP_NAME, user_id=user_id)

        record = RequestRecord()
        token = current_record.set(record)
        started = time.perf_counter()
        final_text = ""
        with tracer.start_as_current_span("waypoint.request") as span:
            span.set_attribute("langfuse.trace.name", "waypoint.request")
            span.set_attribute("langfuse.user.id", hash_user(user_id))
            span.set_attribute("langfuse.session.id", session.id)
            try:
                message = types.Content(role="user", parts=[types.Part(text=text)])
                run_config = RunConfig(max_llm_calls=self.settings.max_model_calls_per_request)
                async for event in self.runner.run_async(
                    user_id=user_id, session_id=session.id, new_message=message, run_config=run_config
                ):
                    if event.author == self.root_agent_name and event.content and event.is_final_response():
                        texts = [p.text for p in event.content.parts or [] if p.text and not p.thought]
                        if texts:
                            final_text = "\n".join(texts)
            except LlmCallsLimitExceededError:
                record.limit_hit = True
                final_text = (
                    "That question needed more steps than Waypoint allows per request. Try asking about one "
                    "program, course or term at a time."
                )
            finally:
                current_record.reset(token)
            latency_ms = (time.perf_counter() - started) * 1000
            final_text, citations, invalid = check_citations(final_text, record.retrieved_chunk_ids)
            record.invalid_citations = invalid
            answer = Answer(final_text, citations, record, latency_ms, session.id)
            metrics = answer.metrics()
            for key, value in metrics.items():
                span.set_attribute(f"waypoint.{key}", value)
            log_request_metrics({"user": hash_user(user_id), "session": session.id, **metrics})
        return answer
