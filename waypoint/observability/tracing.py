"""OpenTelemetry setup: one TracerProvider, exporting to Cloud Trace and/or Langfuse.

ADK already emits a span per agent invocation, LLM call and tool call (with gen_ai.usage.* token counts).
We add: a root `waypoint.request` span per question with request-level metrics, and cost attributes on
each LLM-call span (`ObservabilityPlugin`). Message content is NOT captured in spans by default
(Settings.trace_content=False, enforced per request by WaypointRuntime's TelemetryConfig): students'
questions stay out of third-party trace storage.
"""

import base64
import hashlib
import json
import logging
import os
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from waypoint.config import Settings
from waypoint.observability.cost import cost_usd

logger = logging.getLogger("waypoint.requests")
_provider: TracerProvider | None = None


def setup_tracing(settings: Settings, extra_processors: list[SpanProcessor] | None = None) -> TracerProvider:
    """Install the global TracerProvider once. Exporters are added only when configured."""
    global _provider
    if _provider is not None:
        return _provider
    os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true" if settings.trace_content else "false")
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    if settings.cloud_trace:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.gcp_project)))
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        auth = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    for processor in extra_processors or []:
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider
    return provider


def hash_user(user_id: str) -> str:
    """Stable pseudonymous id for traces and logs, never the raw Google account id or email."""
    return hashlib.sha256(f"waypoint:{user_id}".encode()).hexdigest()[:16]


class ObservabilityPlugin(BasePlugin):
    """Adds cost and token totals to each agent-invocation span (any agent, any depth).

    ADK calls after_model_callback after its generate_content span has closed, so the current span is the
    agent's invoke_agent span. An agent can make several LLM calls, so we keep running totals per span.
    (generate_content spans already carry ADK's gen_ai.usage.* token counts.)
    """

    def __init__(self) -> None:
        super().__init__(name="waypoint_observability")
        self._totals: dict[int, dict[str, float]] = {}

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        usage = llm_response.usage_metadata
        if usage is None or llm_response.partial:
            return None
        input_tokens = usage.prompt_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        cost = cost_usd(
            llm_response.model_version or "",
            input_tokens,
            usage.candidates_token_count or 0,
            usage.cached_content_token_count or 0,
            usage.thoughts_token_count or 0,
        )
        span = trace.get_current_span()
        if not span.is_recording():
            return None
        key = span.get_span_context().span_id
        totals = self._totals.setdefault(key, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "llm_calls": 0})
        totals["cost_usd"] += cost
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["llm_calls"] += 1
        span.set_attribute("waypoint.agent", callback_context.agent_name)
        for name, value in totals.items():
            span.set_attribute(f"waypoint.{name}", value)
        if len(self._totals) > 10_000:  # spans are short-lived; don't let the map grow forever
            self._totals.clear()
        return None


def log_request_metrics(metrics: dict[str, Any]) -> None:
    """One JSON line per request; on Cloud Run this lands in Cloud Logging as a structured entry."""
    logger.info(json.dumps({"message": "waypoint.request", **metrics}))
