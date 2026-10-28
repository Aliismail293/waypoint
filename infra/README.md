# infra

| File | What |
|---|---|
| `bootstrap_iam.sh` | One-time, Owner-run: creates the `waypoint-run` runtime service account with least-privilege roles, and gives the build service account what source deploys need. |
| `deploy.sh` | `gcloud run deploy --source .` with scale-to-zero, max 2 instances, Cloud SQL socket, secrets from Secret Manager. |

Runtime service account roles: `aiplatform.user`, `cloudsql.client`, `bigquery.jobUser` (+ `bigquery.dataEditor` on the
`waypoint` dataset only), `datastore.user`, `cloudtrace.agent`, `logging.logWriter`, `monitoring.metricWriter`, and
`secretmanager.secretAccessor` on exactly three secrets. No project-wide editor or owner.
