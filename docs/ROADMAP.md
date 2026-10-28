# Roadmap: what's deferred and why

Items the spec includes that this build deliberately didn't do, or couldn't finish. Each has a reason and
the next step.

| Item | Status | Why | Next step |
|---|---|---|---|
| Discovery write-up (`docs/DISCOVERY.md`) | Not started | Skipped for this build. It needs a real stakeholder and students | Interview an advisor and 3–5 students, then reprioritise this list |
| Deploy `waypoint` and `fdekit-demo` | Code and scripts ready, not deployed | The build session wasn't permitted to grant IAM roles | Review and run `infra/bootstrap_iam.sh`, then `infra/deploy.sh` and `fdekit-demo/deploy.sh`. Add both URLs as authorised JavaScript origins |
| Real launch (society channel, Discord, Reddit) | Not done | Needs a deployed service, and posting is a human decision | Launch to a small group first. Usage tracking, thumbs and escalation are built |
| Usage numbers, resume bullets | [TBD] | No users yet. Numbers must be real | `uv run python -m waypoint.analytics` after launch |
| Publish fde-kit to PyPI | Package builds (`uv build`), not published | Out of scope for this build | Pick a free name, add a release workflow with trusted publishing, tag 0.1.0 |
| File upstream issues on google/adk-python | 3 drafts with repros in `docs/upstream-issues/` | Not filed by request | Re-run the repros on the latest ADK, check for duplicates, file |
| `terraform apply` | Validated and planned only (7 import, 24 add, 0 change, 0 destroy) | Not applied by request | Move state to a GCS backend, set `billing_account` in `terraform.tfvars`, apply |
| Live MCP Toolbox | Config and client tested offline against a fake server | Running the external Toolbox binary wasn't permitted in the build session | `brew install mcp-toolbox`, `toolbox --config infra/toolbox/tools.yaml`, set `WAYPOINT_CATALOG=toolbox`. Later, run it as a Cloud Run sidecar |
| Wider calendar coverage | Only the Mathematical Sciences section | The calendar site returned 403 to the bot after the first pages, so fetching stopped | Save pages manually or ask for an export. Then `ingest parse`, `ingest pgvector`, and re-generate the golden set |
| Real timetable | Synthetic sample data, labelled | The public timetable's terms of use are unclear | Ask the Registrar for permission or a feed, then swap `SampleFileSource` for a live source |
| State-optimisation win | Not achieved. The measured v1 policy cost 31% more | See `docs/STATE_OPTIMISATION.md` | Re-measure on 15+ turn conversations with more samples |
| Explicit Gemini context caching | Implemented behind a flag, off | It creates a billed CachedContent resource, and prompts are below the 4096-token minimum | Revisit if static prompts grow past the minimum |
| Reranker | Not added | The Vertex Ranking API isn't enabled. RRF reaches 81% recall@5 | Measure a reranker on the dev split before adding latency |
| Portfolio site | Skipped | Out of scope for this build | Use `docs/CASE_STUDY.md` as the source |
| Public MCP endpoint for Waypoint | stdio and localhost HTTP only | No auth on the MCP transport yet | Add OAuth on streamable HTTP before exposing it |
