# Waypoint Terraform

Declares the Waypoint GCP footprint in `waypoint-513003`: the same resources, roles and Cloud Run
settings as `infra/bootstrap_iam.sh` and `infra/deploy.sh`. **Nothing here has been applied yet.**
See `PLAN.md` for the latest plan.

| File | What |
|---|---|
| `versions.tf` | Terraform/provider pins (google `8.9.0`), local backend, a `google.billing` provider alias for the Budgets API |
| `imports.tf` | `import {}` blocks adopting the hand-made resources (Cloud SQL instance + database, BigQuery dataset, Firestore, 3 secrets) |
| `data.tf` | Those data stores, with attributes matched to the live resources so the plan is import-only |
| `iam.tf` | `waypoint-run` service account and its least-privilege, additive `*_iam_member` grants |
| `run.tf` | Cloud Run v2 services `waypoint` and `fdekit-demo` and their public invoker binding |
| `budget.tf` | Monthly budget with 50/90/100% alerts (optional: `enable_budget`) |

## Ownership boundaries

- **Images**: deploys still use `infra/deploy.sh` (`gcloud run deploy --source`). Terraform creates the
  services with a placeholder image and ignores image changes afterwards.
- **Secret values**: Terraform declares the secret containers only. Add values with
  `gcloud secrets versions add <id> --data-file=-`, so no secret material ends up in state.
- **BigQuery tables**: created by `uv run python -m waypoint.ingest bigquery`, not Terraform.
- **Data stores** have `prevent_destroy`; the Cloud SQL instance also has `deletion_protection = true`.
  The API-side Cloud SQL deletion protection flag is still off (matches reality). Turning it on is a
  safe in-place follow-up change.

## Usage

```bash
cd infra/terraform
terraform init
terraform plan            # review: imports + additions only, no change/destroy expected
# terraform apply         # by a project Owner, after review
```

Before the first apply, move state to a GCS backend (state is local today) and decide whether to keep
`infra/bootstrap_iam.sh`: after apply, Terraform owns the runtime service account and its grants. The
build-service-account roles in `bootstrap_iam.sh` are intentionally not in Terraform yet.

## Notes

- The budget is in **CAD** because the billing account bills in CAD and the Budgets API requires the
  account currency. `billingbudgets.googleapis.com` is not enabled yet; Terraform enables it.
- The Budgets API needs a quota project with user credentials, hence the `google.billing` alias.
- Both services are public (`allUsers` → `roles/run.invoker`): the app verifies Google Sign-In ID tokens
  itself and fails closed without a client id.
