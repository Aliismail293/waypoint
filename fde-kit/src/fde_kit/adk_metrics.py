"""Per-request metrics, a request-wide model-call budget, and per-agent cost on trace spans, for Google ADK.

One plugin, `MetricsPlugin`, does three jobs. ADK calls a plugin for every agent, model call and tool call in
the run, including inside the child agents that `AgentTool` runs (AgentTool hands its parent's plugins to the
child Runner by default), so a single plugin sees the whole request:

1. Recording. Inside `request_scope()`, every model call, agent, tool call, tool error and retrieved id is
   added to a `RequestMetrics` record held in a ContextVar.
2. Budget. ADK's `RunConfig.max_llm_calls` counts calls per *invocation*, and each AgentTool child is its own
   invocation with its own counter, so it can't cap a whole request. The plugin counts every call in the
   request and, once over budget, answers in place of the model so the agent stops.
3. Span totals. Running cost/token totals are written onto the current span after each model call. With
   ADK's default telemetry schema (v2) that is the agent's `invoke_agent` span, because the
   `generate_content` span has already closed when `after_model_callback` runs; an agent can call the model
   several times, hence running totals per span.

Usage:

    plugin = MetricsPlugin(max_model_calls=16)
    runner = Runner(app=App(name="app", root_agent=agent, plugins=[plugin]), session_service=...)
    with request_scope() as metrics:
        async for event in runner.run_async(...):
            ...
    print(metrics.cost_usd, metrics.retrieved_ids, metrics.limit_hit)
"""

from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import trace

from fde_kit.pricing import DEFAULT_PRICES, Price, cost_usd

DEFAULT_BUDGET_MESSAGE = "Stopping here: this request reached its model-call limit."
DEFAULT_ID_KEYS = ("chunk_id", "chunk_ids")


@dataclass
class ModelCall:
    """One completed model call, priced when it happened (so the record doesn't need the price table)."""

    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cached_tokens: int
    cost_usd: float


@dataclass
class RequestMetrics:
    """Everything one request did. Filled in by `MetricsPlugin` while agents, models and tools run."""

    model_calls: list[ModelCall] = field(default_factory=list)
    model_calls_started: int = 0  # includes calls the budget skipped, so it can exceed len(model_calls)
    agents: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    retrieved_ids: list[str] = field(default_factory=list)
    limit_hit: bool = False

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.model_calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.model_calls)

    @property
    def thinking_tokens(self) -> int:
        return sum(c.thinking_tokens for c in self.model_calls)

    @property
    def cached_tokens(self) -> int:
        return sum(c.cached_tokens for c in self.model_calls)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.model_calls)

    def tokens_per_second(self, latency_s: float) -> float:
        """Generated tokens (output + thinking) per second of end-to-end request latency."""
        if latency_s <= 0:
            return 0.0
        return (self.output_tokens + self.thinking_tokens) / latency_s

    def to_dict(self, latency_s: float | None = None) -> dict[str, Any]:
        """Flat summary for logs, span attributes or an eval's raw payload."""
        summary: dict[str, Any] = {
            "model_calls": len(self.model_calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "tool_calls": len(self.tool_calls),
            "tool_errors": self.tool_errors,
            "agents": sorted(set(self.agents)),
            "retrieved_ids": len(self.retrieved_ids),
            "limit_hit": self.limit_hit,
        }
        if latency_s is not None:
            summary["latency_ms"] = round(latency_s * 1000, 1)
            summary["tokens_per_second"] = round(self.tokens_per_second(latency_s), 2)
        return summary

    def as_records(self) -> list[dict[str, Any]]:
        """Per-model-call rows, e.g. for a per-agent cost breakdown."""
        return [asdict(c) for c in self.model_calls]


# A ContextVar (not a plugin attribute) because one plugin instance serves concurrent requests. ADK copies
# the context into any tasks/threads it starts, and those copies all point at the same RequestMetrics object,
# which we only ever mutate (never rebind), so child agents and threaded tools write to the right record.
_current: ContextVar[RequestMetrics | None] = ContextVar("fde_kit_request_metrics", default=None)


def current_metrics() -> RequestMetrics | None:
    """The record for the request running in this context, or None outside `request_scope()`."""
    return _current.get()


@contextmanager
def request_scope() -> Iterator[RequestMetrics]:
    """Install a fresh `RequestMetrics` for everything run inside the block, and yield it."""
    metrics = RequestMetrics()
    token = _current.set(metrics)
    try:
        yield metrics
    finally:
        _current.reset(token)  # restore the outer value, so nested or sequential scopes don't leak


def collect_ids(value: Any, keys: Collection[str]) -> list[str]:
    """String ids found under any of `keys`, anywhere in a nested dict/list tool result.

    A key's value may be one id ("chunk_id": "a") or a list of ids ("chunk_ids": ["a", "b"]).
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in keys and isinstance(item, str):
                    found.append(item)
                elif key in keys and isinstance(item, list):
                    found.extend(i for i in item if isinstance(i, str))
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


class MetricsPlugin(BasePlugin):
    """Records each request, enforces a request-wide model-call budget, and puts cost totals on spans.

    Args:
        max_model_calls: model calls allowed per `request_scope()`, across every agent; None = no budget.
        prices: price table for cost (see `fde_kit.pricing`).
        default_price: price for models not in `prices`; None prices them at 0.0.
        id_keys: keys whose values in tool results are retrieved ids (e.g. chunk ids, for citation checks
            and retrieval evals).
        budget_message: text the agent receives instead of a model answer once the budget is spent.
        attribute_prefix: span attributes are named "<prefix>.cost_usd", "<prefix>.llm_calls", ...
    """

    def __init__(
        self,
        max_model_calls: int | None = None,
        *,
        prices: Mapping[str, Price] = DEFAULT_PRICES,
        default_price: Price | None = None,
        id_keys: Collection[str] = DEFAULT_ID_KEYS,
        budget_message: str = DEFAULT_BUDGET_MESSAGE,
        attribute_prefix: str = "fde",
        name: str = "fde_metrics",
    ) -> None:
        super().__init__(name=name)
        self.max_model_calls = max_model_calls
        self.prices = prices
        self.default_price = default_price
        self.id_keys = frozenset(id_keys)
        self.budget_message = budget_message
        self.attribute_prefix = attribute_prefix
        self._span_totals: dict[int, dict[str, float]] = {}

    async def before_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> types.Content | None:
        record = _current.get()
        if record is not None:
            record.agents.append(agent.name)
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        record = _current.get()
        if record is None or self.max_model_calls is None:
            return None
        record.model_calls_started += 1
        if record.model_calls_started > self.max_model_calls:
            record.limit_hit = True
            # Returning a response skips the model call: the agent takes this text as its final answer and
            # stops. A child agent's "answer" goes back to its parent, whose next model call is also skipped.
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.budget_message)]))
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        usage = llm_response.usage_metadata
        # Streaming partials carry incomplete usage; the final (non-partial) response has the totals.
        if usage is None or llm_response.partial:
            return None
        model = llm_response.model_version or ""
        input_tokens = usage.prompt_token_count or 0
        output_tokens = usage.candidates_token_count or 0
        thinking_tokens = usage.thoughts_token_count or 0
        cached_tokens = usage.cached_content_token_count or 0
        call = ModelCall(
            agent=callback_context.agent_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd(
                model,
                input_tokens,
                output_tokens,
                cached_tokens,
                thinking_tokens,
                prices=self.prices,
                default=self.default_price,
            ),
        )
        record = _current.get()
        if record is not None:
            record.model_calls.append(call)
        self._add_to_span(call)
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        record = _current.get()
        if record is None:
            return None
        record.tool_calls.append(tool.name)
        seen = set(record.retrieved_ids)
        for item in collect_ids(result, self.id_keys):
            if item not in seen:  # keep first-seen order (it's the ranking), drop repeats
                record.retrieved_ids.append(item)
                seen.add(item)
        # Tools that report failure in their result (rather than raising) still count as errors.
        if isinstance(result, dict) and result.get("error"):
            record.tool_errors += 1
        return None

    async def on_tool_error_callback(
        self, *, tool: BaseTool, tool_args: dict[str, Any], tool_context: ToolContext, error: Exception
    ) -> dict[str, Any] | None:
        record = _current.get()
        if record is not None:
            record.tool_errors += 1
        return None  # don't swallow the error: ADK handles it as it would without the plugin

    def _add_to_span(self, call: ModelCall) -> None:
        span = trace.get_current_span()
        if not span.is_recording():
            return
        key = span.get_span_context().span_id
        totals = self._span_totals.setdefault(
            key, {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cost_usd": 0.0}
        )
        totals["llm_calls"] += 1
        totals["input_tokens"] += call.input_tokens
        totals["output_tokens"] += call.output_tokens
        totals["thinking_tokens"] += call.thinking_tokens
        totals["cost_usd"] += call.cost_usd
        span.set_attribute(f"{self.attribute_prefix}.agent", call.agent)
        for name, value in totals.items():
            span.set_attribute(f"{self.attribute_prefix}.{name}", value)
        # Spans are short-lived and we get no "span ended" hook here, so bound the map instead of leaking.
        if len(self._span_totals) > 10_000:
            self._span_totals.clear()
