"""Keeping a conversation's context small: prune old tool outputs, summarise old turns, cache the static prefix.

Only the orchestrator sees conversation history (each specialist starts fresh for every AgentTool call), so
history is where tokens grow turn after turn: every earlier specialist answer is re-sent on every model call.

1. PruneOldToolOutputs (plugin): in the orchestrator's request, specialist outputs from *earlier* turns are
   replaced by a one-line stub. The orchestrator's own answer for that turn, which summarised them, stays.
   The current turn is never touched.
2. Running summary: ADK's events compaction. Every `compaction_interval` turns, older turns are summarised
   into one event by a model call that runs after the answer has been sent. `MeteredLlm` records that
   call's tokens, which the plugins never see, so the before/after cost is honest.
3. Prefix caching: Gemini caches repeated prompt prefixes implicitly (no resource to manage). Instructions
   are static text with the date line last, and pruning keeps earlier history stable, so the prefix repeats.
   Explicit ADK context caching (`ContextCacheConfig`) creates a billed CachedContent resource and needs a
   4096-token prefix on Gemini 3, so it is off unless WAYPOINT_EXPLICIT_CACHE=true.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from google.adk.agents.callback_context import CallbackContext
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types
from pydantic import ConfigDict, Field

PRUNED = "[Earlier specialist output removed to save context; your answer from that turn is above.]"


class PruneOldToolOutputs(BasePlugin):
    def __init__(self, agent_name: str) -> None:
        super().__init__(name="waypoint_prune_old_tool_outputs")
        self.agent_name = agent_name

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        if callback_context.agent_name == self.agent_name:
            prune_old_tool_outputs(llm_request.contents)
        return None


def prune_old_tool_outputs(contents: list[types.Content]) -> int:
    """Stub out function responses that come before the latest user message. Returns how many were pruned."""
    latest_user = max(
        (i for i, c in enumerate(contents) if c.role == "user" and any(p.text for p in c.parts or [])), default=-1
    )
    pruned = 0
    for content in contents[: max(latest_user, 0)]:
        for part in content.parts or []:
            if part.function_response and part.function_response.response != {"result": PRUNED}:
                part.function_response.response = {"result": PRUNED}
                pruned += 1
    return pruned


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    log: list[tuple[str, int, int, int]] = field(default_factory=list)  # (model, input, output, thinking)


class MeteredLlm(BaseLlm):
    """Wraps a model and records the tokens of every call (for calls no plugin sees, like summarisation)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseLlm
    usage: Usage = Field(default_factory=Usage)

    @property
    def capabilities(self) -> LlmCapabilities:
        return self.inner.capabilities

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        async for response in self.inner.generate_content_async(llm_request, stream):
            meta = response.usage_metadata
            if meta is not None and not response.partial:
                self.usage.calls += 1
                self.usage.input_tokens += meta.prompt_token_count or 0
                self.usage.output_tokens += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
                self.usage.log.append(
                    (
                        response.model_version or self.inner.model,
                        meta.prompt_token_count or 0,
                        meta.candidates_token_count or 0,
                        meta.thoughts_token_count or 0,
                    )
                )
            yield response


def state_options(
    model: BaseLlm, *, compaction_interval: int, overlap: int, explicit_cache: bool
) -> tuple[dict[str, object], MeteredLlm]:
    """App(...) keyword arguments that turn on summary compaction (and optionally explicit caching)."""
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps.app import EventsCompactionConfig

    metered = MeteredLlm(model=model.model, inner=model, usage=Usage())
    options: dict[str, object] = {
        "events_compaction_config": EventsCompactionConfig(
            summarizer=LlmEventSummarizer(llm=metered),
            compaction_interval=compaction_interval,
            overlap_size=overlap,
        )
    }
    if explicit_cache:
        options["context_cache_config"] = ContextCacheConfig(min_tokens=4096, ttl_seconds=900, cache_intervals=10)
    return options, metered
