# Observability

## What is traced

```
waypoint.request                      root span per question (ours)
├─ invoke_agent waypoint              ADK; + waypoint.cost_usd / input_tokens / output_tokens / llm_calls
│  ├─ generate_content <model>        ADK; gen_ai.usage.input_tokens / output_tokens
│  ├─ execute_tool policy_agent       ADK
│  │  └─ invoke_agent policy_agent    ADK; + our per-agent totals
│  │     ├─ generate_content …
│  │     └─ execute_tool search_calendar
│  └─ …
```

`waypoint.request` attributes: `latency_ms`, `model_calls`, `input_tokens`, `output_tokens`, `cached_tokens`,
`cache_hit_rate`, `tokens_per_second` (output tokens incl. thinking ÷ wall time), `cost_usd`, `tool_calls`,
`tool_errors`, `retrieved_chunks`, `citations`, `invalid_citations`, `limit_hit`, plus Langfuse's
`langfuse.trace.name`, `langfuse.user.id` (a salted SHA-256 of the user id, never the email) and
`langfuse.session.id`. The same metrics are logged as one JSON line per request (`waypoint.requests` logger),
which Cloud Logging indexes on Cloud Run.

Message content is **not** captured in spans (`ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`, controlled by
`WAYPOINT_TRACE_CONTENT`): students' questions stay out of third-party trace storage.

## Exporters

| Destination | Enabled by | Notes |
|---|---|---|
| Cloud Trace | `WAYPOINT_CLOUD_TRACE=true` | `opentelemetry-exporter-gcp-trace` |
| Langfuse | `WAYPOINT_LANGFUSE_PUBLIC_KEY` + `_SECRET_KEY` (from Secret Manager) | OTLP/HTTP to `{host}/api/public/otel/v1/traces`. Read back with `GET /api/public/v2/observations` (v1 `/traces` is deprecated on Langfuse Cloud). |

## Cost model

`waypoint/observability/cost.py`. USD per 1M tokens, Vertex AI. Google's pricing page didn't list the
3.5 models at build time; these are from third-party trackers (getmaxim.ai, futureagi.com, llmreference.com)
and should be checked against the Billing console:

| Model | Input | Cached input | Output (incl. thinking) |
|---|---|---|---|
| gemini-3.5-flash | 1.50 | 0.15 | 9.00 |
| gemini-3.5-flash-lite | 0.30 | 0.03 | 2.50 |
| gemini-embedding-001 | 0.15 | – | – |

## Guards

- Request-wide model-call budget (`WAYPOINT_MAX_MODEL_CALLS_PER_REQUEST`, default 16), enforced by
  `RecorderPlugin.before_model_callback` across the orchestrator *and* every specialist. ADK's
  `RunConfig.max_llm_calls` alone only counts the root invocation.
- `max_output_tokens` per call, `thinking_level=minimal`.
