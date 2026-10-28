# fde-kit

Small, tested building blocks for shipping [Google ADK](https://github.com/google/adk-python) agents to
production: request-wide budgets and cost metrics, legacy-system MCP adapters, offline model doubles, and a
bridge to the [ragaudit](https://github.com/Aliismail293/Rag-Tester) eval harness.

Each piece was extracted from [Waypoint](https://github.com/Aliismail293/waypoint), a multi-agent
degree-planning copilot, after it solved a real problem there.

```bash
pip install fde-kit              # not on PyPI yet: pip install ./fde-kit from this repo
pip install "fde-kit[ragaudit]"  # only for fde_kit.ragaudit_adk.AdkRagAdapter.query()
```

Python 3.12+, `google-adk>=2.13`, `mcp>=2.3`.

## Modules

### `fde_kit.adk_metrics`: one plugin for metrics, budget and span cost

```python
from google.adk.apps import App
from google.adk.runners import Runner
from fde_kit.adk_metrics import MetricsPlugin, request_scope

plugin = MetricsPlugin(max_model_calls=16, id_keys=("chunk_id", "chunk_ids"))
runner = Runner(app=App(name="app", root_agent=root, plugins=[plugin]), session_service=sessions)

with request_scope() as metrics:
    async for event in runner.run_async(user_id=uid, session_id=sid, new_message=msg):
        ...
print(metrics.cost_usd, metrics.input_tokens, metrics.retrieved_ids, metrics.limit_hit)
print(metrics.to_dict(latency_s=2.4))  # includes tokens_per_second
```

- **Request-wide budget.** It counts every model call in the request, including those made by `AgentTool`
  children, and once the budget is spent it answers in place of the model so the agent stops.
- **Recording.** It records each model call's tokens (input, output, thinking, cached), plus agents, tool
  calls, tool errors and retrieved ids. The ids come from any key you name in tool results.
- **Span totals.** Running `fde.cost_usd`, `fde.input_tokens`, `fde.output_tokens` and `fde.llm_calls`
  go on the current agent's `invoke_agent` span.

### `fde_kit.pricing`: cost per call

```python
from fde_kit.pricing import Price, cost_usd

cost_usd("gemini-3.5-flash-001", input_tokens=4000, output_tokens=300, cached_tokens=1000)
cost_usd("my-model", 100, 10, prices={"my-model": Price(input=1.0, output=2.0, cached_input=0.1)})
```

The model name is matched by its longest prefix. An unknown model costs 0.0 (or `default=`) instead of
raising, so a missing price entry never breaks a live request. The default figures are third-party, not
official Google prices, so pass your own table for billing-grade numbers.

### `fde_kit.mcp_legacy`: a legacy source behind MCP

```python
from fde_kit.mcp_legacy import LegacyAdapter, TransientError, build_mcp_server

adapter = LegacyAdapter(
    fetch=old_api.get_rows,                      # (course, session) -> list[dict]
    normalise=row_to_section,                    # dict -> your model
    canonical_args=lambda c, s: (c.upper(), s),  # "mat224" and "MAT224" share a cache entry
    retry_on=(TransientError, TimeoutError),
    cache_ttl_s=900,
)

def search_sections(course: str, session: str) -> dict:
    """List the sections of a course in a session."""
    return {"sections": [s.model_dump() for s in adapter.get(course, session)]}

build_mcp_server("timetable", "Course sections.", [search_sections]).run()  # stdio
```

The adapter retries with exponential backoff, keeps a TTL cache, normalises each record, and counts
`stats` (source calls, cache hits, retries). The clock and sleep are injectable, so tests never wait.

### `fde_kit.ragaudit_adk`: evaluate an async agent with ragaudit

```python
from fde_kit.adk_metrics import request_scope
from fde_kit.ragaudit_adk import AdkRagAdapter

async def ask(question: str):
    with request_scope() as m:
        started = time.perf_counter()
        text = await my_runtime.ask(question)
    return text, m.retrieved_ids, m.to_dict(time.perf_counter() - started)

def make_adapter(config: dict) -> AdkRagAdapter:  # ragaudit loads this from your adapter file
    return AdkRagAdapter(ask, metrics_log=config.get("metrics_log"), timeout_s=120)
```

Every question runs on one long-lived event loop in a background thread. Anything the agent opens on its
first question, such as an MCP stdio session, keeps working for the rest. The `metrics_log` JSONL holds
latency and cost per question, which is what P95 and $/query are computed from.

### `fde_kit.testing`: offline model doubles

```python
from fde_kit.testing import ScriptedLlm, text_response, tool_call_response

llm = ScriptedLlm(script=[
    tool_call_response("lookup_agent", {"request": "MAT224"}),  # parent delegates
    text_response("MAT224 needs MAT102.", input_tokens=120),    # child answers
    text_response("You need MAT102 first."),                    # parent's final answer
])
```

`ScriptedLlm` replays the queued responses with token usage attached, records every request it receives,
and raises `ScriptEmptyError` if the agent makes more calls than you scripted.

## Origin: the friction each piece works around

| Module | What happened in Waypoint |
|---|---|
| `adk_metrics` budget | `RunConfig.max_llm_calls` counts per invocation, and every `AgentTool` child is a new invocation. One request made 87 model calls with the cap set to 16. |
| `adk_metrics` span totals | `after_model_callback` runs after the `generate_content` span has closed, so attributes set there are lost. Totals go on the enclosing `invoke_agent` span instead. |
| `ragaudit_adk` | `asyncio.run()` per eval question broke the MCP stdio session opened on the first question. |
| `mcp_legacy` | The timetable source is slow, flaky and oddly shaped. Agents asked for the same course many times per conversation. |
| `testing` | `BaseLlm` subclasses must override `capabilities`, or ADK falls back to guessing from the model name and fails on made-up names. |

## Development

```bash
uv sync && uv run pytest && uv run ruff check && uv run mypy src && uv build
```

The tests are fully offline, with no network or cloud credentials. The ragaudit integration test runs when
ragaudit is installed (`uv run --with /path/to/Rag-Tester pytest`).

MIT licensed.
