# Upstream issue drafts (google/adk-python)

**Drafts only: not filed.** Each comes from friction hit while building Waypoint, and each has a repro
script that runs offline with a scripted model (no credentials). The scripts were run against
`google-adk==2.13.0` on 2026-10-28, and the outputs quoted in the drafts are from those runs.

| Draft | Repro | Waypoint workaround |
|---|---|---|
| [01: max_llm_calls ignores AgentTool children](01-max-llm-calls-agenttool.md) | `repro_max_llm_calls.py` | Request-wide budget in `RecorderPlugin` (now `fde_kit.adk_metrics.MetricsPlugin`) |
| [02: malformed turns become silent empty answers](02-malformed-final-response.md) | `repro_malformed_final.py` | `RetryingGemini` retries malformed turns |
| [03: after_model_callback can't see the model-call span](03-after-model-callback-span.md) | `repro_after_model_span.py` | Running totals on the `invoke_agent` span |

Run a repro: `cd docs/upstream-issues && uv run python repro_max_llm_calls.py`.

Before filing: search for duplicates again, re-run against the latest ADK release, and trim each draft to
the template google/adk-python asks for.
