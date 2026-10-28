# Waypoint runbook

For whoever is on call for the `waypoint` Cloud Run service (project `waypoint-513003`, region `us-central1`).

## At a glance

| Thing | Where |
|---|---|
| Service | Cloud Run `waypoint` (min 0, max 2 instances, concurrency 10, 1 CPU / 1 GiB, 300 s timeout) |
| Logs | Cloud Logging: `resource.labels.service_name="waypoint"`. One `waypoint.request` JSON line per question |
| Traces | Cloud Trace (`waypoint.request` root span) and Langfuse (cloud.langfuse.com) |
| Dashboard | Cloud Monitoring "Waypoint: requests" (after `infra/terraform` is applied) |
| Data | Cloud SQL `waypoint-db` (pgvector chunks), BigQuery `waypoint` dataset, Firestore `(default)` |
| Secrets | Secret Manager: `db-password`, `langfuse-public-key`, `langfuse-secret-key` |
| Usage numbers | `uv run python -m waypoint.analytics` or `GET /api/stats` (operator emails only) |

## Deploy and roll back

```bash
infra/bootstrap_iam.sh        # once per project: runtime service account + least-privilege roles
infra/deploy.sh               # builds from source with Cloud Build, deploys a new revision
gcloud run services describe waypoint --region us-central1 --format='value(status.url)'
```

`deploy.sh` first runs `infra/check_deploy_iam.sh`, a read-only check that prints the exact grant
command if the build service account is missing a role. Without push access, a source build looks like
this:
- the docker step succeeds,
- the build fails at PUSH with `FAILED_PRECONDITION` (code 9) and no step logs,
- the `cloud-run-source-deploy` repository stays empty.

Roll back to the previous revision:

```bash
gcloud run revisions list --service waypoint --region us-central1
gcloud run services update-traffic waypoint --region us-central1 --to-revisions <REVISION>=100
```

After each deploy:
1. Add the service URL as an authorised JavaScript origin on the OAuth client.
2. Ask the 3 demo questions (README).

Operators can smoke-test without the browser:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H 'Content-Type: application/json' \
  -d '{"message":"What are the prerequisites for MAT224?"}' "$URL/api/chat" | jq .answer
```

## Alerts and what to do

**Answers fail with "Vertex AI 429 / RESOURCE_EXHAUSTED"**
- The model client retries 429 and 503 four times with 2–20 s backoff. Persistent 429s mean quota.
- Check Vertex AI quotas for `gemini-3.5-flash` on the global endpoint.
- Short term: lower `--max-instances` or `WAYPOINT_MAX_REQUESTS_PER_USER_PER_DAY`.

**Empty or very short answers**
- Look for `MALFORMED_RESPONSE` warnings in logs. `RetryingGemini` retries twice. A spike means a model-side
  issue, so keep watching and consider pinning a model version.

**"Stopping here: this request reached Waypoint's per-question step limit."**
- `limit_hit=true` in the request log. One question used 24 model calls.
- Find the trace and check whether a specialist looped. Fix the prompt or the tool.
- Raise `WAYPOINT_MAX_MODEL_CALLS_PER_REQUEST` only with a reason.

**Latency P95 up**
- Check `model_calls` per request on the dashboard. Latency tracks the number of sequential calls (about 2 s
  each).
- Check the timetable MCP (`tool_errors`) and Cloud SQL connections.

**Cost per request up**
- Check `input_tokens` growth. Long conversations: confirm `WAYPOINT_STATE_OPT=true`.
- A budget alert (50/90/100% of the monthly budget) emails billing admins.

**Database errors (`psycopg` / `cloudsql`)**
- `gcloud sql instances describe waypoint-db`. Confirm the instance is RUNNABLE.
- The pool reconnects on its own, and `check_connection` drops dead connections.
- If the instance is down, answers fail closed (errors) and nothing is invented.

**Sign-in fails for everyone**
- Check `WAYPOINT_GOOGLE_CLIENT_ID` on the service and that the URL is an authorised JavaScript origin.
- With no client id the API returns 503 by design (fail closed).

## Routine tasks

**New calendar pages or a new calendar year**
1. Save the pages to `data/raw/calendar/`.
2. `uv run python -m waypoint.ingest parse`, then `pgvector --prune` and `bigquery`. Only changed chunks are
   re-embedded.
3. Re-generate the golden set (`evals/run_eval.py generate --llm openai`).
4. Run `evaluate`, and update the README metrics and `evals/ci_baseline.json`.

**Rotate the DB password**
1. Add a new Secret Manager version.
2. `ALTER USER postgres PASSWORD ...` on `waypoint-db`.
3. Deploy a new revision (secrets are read at start-up).

**Take the service offline fast**

```bash
gcloud run services remove-iam-policy-binding waypoint --region us-central1 \
  --member=allUsers --role=roles/run.invoker
```

**A student asks for their data to be deleted.** Usage events hold only `hash_user(sub)`. Sessions are keyed
by the Google `sub`.
1. Get the student's `sub` from a signed-in request log.
2. Delete their ADK sessions in Firestore.
3. Delete the usage events whose `user` matches the hash.

## Safety

- The safety suite runs in CI (offline guard checks) and before releases with live model checks:
  `WAYPOINT_LLM=vertex WAYPOINT_EMBEDDINGS=vertex uv run python evals/safety_suite.py --live`.
- If someone reports a harmful or wrong answer, find the request by time in Langfuse or Cloud Trace (traces
  carry metrics, not text), reproduce it locally, and add a case to `evals/safety/cases.jsonl`.
