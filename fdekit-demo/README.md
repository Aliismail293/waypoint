# fdekit-demo: Parks Canada visits (DEMO deployment)

> **This is a demo.** It is the second deployment of the Waypoint pattern, in a different domain, built with
> [`fde-kit`](../fde-kit) to check that the pattern repeats. It uses public open data only. It is not an
> official Parks Canada service and has no users.

Ask "Which national park had the most visitors in 2025-26?" and an ADK agent answers from Parks Canada's
yearly attendance exports. It reaches them through a legacy-adapter MCP server.

## What it reuses from fde-kit

| Need | fde-kit piece | Here |
|---|---|---|
| A messy legacy export behind a clean API, with retry and a TTL cache | `mcp_legacy.LegacyAdapter` | `parks_demo/adapter.py` |
| Serve it to agents over MCP | `mcp_legacy.build_mcp_server` | `parks_demo/mcp_server.py` (stdio) |
| Request-wide model-call budget, tokens, cost, tokens/sec | `adk_metrics.MetricsPlugin`, `request_scope` | `parks_demo/agent.py` |
| Offline tests of the agent without a model | `testing.ScriptedLlm` | `tests/test_demo.py` |

## The data

Parks Canada attendance for fiscal years 2023-24, 2024-25 and 2025-26, from open.canada.ca. Contains
information licensed under the
[Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
The files are kept unmodified in `data/`.

The real exports have these quirks, which the adapter handles:
- A byte-order mark.
- Two files in Windows-1252 and one in UTF-8.
- Numbers stored as text ("4,230,156").
- Status codes instead of numbers (UR = unable to report, TC = temporarily closed).
- Footnote rows at the bottom.

The raw rows are also loaded into their own schema (`fdekit_demo`) on Waypoint's existing Cloud SQL
instance, so no new database was created: `uv run python -m parks_demo.load`.

## Results (live, 2026-10-28)

`uv run python -m parks_demo.eval` with Gemini 3.5 Flash, Postgres source. Each expected answer is computed
from the data at run time.

| Metric | Value |
|---|---|
| Correct | 6/6 (incl. one unreported park and one off-topic question) |
| P95 latency | 4.6 s |
| Mean cost per question | $0.003 |

Raw output: `eval_results.json`.

## Setup time

**6 min 29 s** of wall-clock time (04:32:11 → 04:38:40 EDT), measured from choosing the dataset to the
6/6 live eval. That covers the data download, source and adapter, MCP server, agent, web app, loader,
7 offline tests and the live run against Cloud SQL and Vertex AI.

Caveats, so the number isn't over-read:
- The coding agent that built Waypoint did this work in the same session, so it's agent time, not a
  person's.
- It excludes building fde-kit itself and the deployment, which is blocked on the same IAM grants as
  Waypoint (see the root README).
- Rejecting the first candidate dataset (City of Toronto: licence not stated) took a few more minutes
  before the clock started.

## Run it

```bash
uv sync && uv run pytest                                   # offline: scripted model, CSV source
PARKS_LLM=vertex uv run python -m parks_demo.eval          # live model, CSV source
PARKS_GOOGLE_CLIENT_ID=... uv run uvicorn parks_demo.app:app --port 8080
./deploy.sh                                                # Cloud Run service `fdekit-demo` (min 0, max 1)
```

Sign-in uses Google Identity Services, verified server-side, as in Waypoint. There is a cap of 20 questions
per user per day and 8 model calls per question.
