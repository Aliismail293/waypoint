# MCP Toolbox for Databases

`tools.yaml` puts Waypoint's BigQuery allow-list behind [MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox).
It is generated from `NAMED_QUERIES` in `waypoint/tools/catalog.py`, and `tests/test_toolbox.py` fails if the two
drift apart:

```bash
uv run python -m waypoint.tools.toolbox > infra/toolbox/tools.yaml
```

What Toolbox enforces, not the model:
- The source is read-only (`writeMode: blocked`) and limited to the `waypoint` dataset (`allowedDatasets`).
- Each query has a 50 MB billing cap (`maximumBytesBilled`).
- Only the five `bigquery-sql` tools exist. They run fixed statements with bound `@parameters`, and there is no
  `execute_sql` tool.

## Run it locally

```bash
brew install mcp-toolbox                 # or a release binary: github.com/googleapis/mcp-toolbox/releases
toolbox --config infra/toolbox/tools.yaml  # uses gcloud application-default credentials, serves :5000
WAYPOINT_CATALOG=toolbox WAYPOINT_LLM=vertex ... uv run python -m waypoint.agents "What does MAT224 unlock?"
```

`WAYPOINT_CATALOG=toolbox` makes Waypoint's catalog tools call Toolbox's HTTP API (`ToolboxCatalog`) instead of
BigQuery. MCP clients can use the same server at `http://127.0.0.1:5000/mcp`.

## Status

The config and client are tested offline against a fake Toolbox server. They have **not** been run against a real
Toolbox server yet: the build session wasn't allowed to download and run the external binary. Production
still uses the direct `BigQueryCatalog`, which runs the same SQL with the same parameters and cap. Running
Toolbox as a Cloud Run sidecar is listed in `docs/ROADMAP.md`.
