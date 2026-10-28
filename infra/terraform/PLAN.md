# Terraform plan: 2026-10-28

Run from `infra/terraform/` with application-default credentials (user `authorized_user`, quota project
`waypoint-513003`). Terraform v1.17.0 on darwin_arm64, provider `hashicorp/google` 8.9.0. Local backend;
no apply, no CLI import, no state writes. No plan file was saved.

## Commands

```bash
terraform init -input=false
terraform fmt                 # reformatted run.tf (alignment only)
terraform validate
terraform plan -input=false -lock=false
```

Read-only commands used to match the config to the live resources:

```bash
gcloud sql instances describe waypoint-db --project waypoint-513003
gcloud sql databases list --instance waypoint-db --project waypoint-513003
bq show --format=prettyjson waypoint-513003:waypoint
gcloud firestore databases describe --database='(default)' --project waypoint-513003
gcloud secrets describe {db-password,langfuse-public-key,langfuse-secret-key} --project waypoint-513003   # metadata only
gcloud billing projects describe waypoint-513003
gcloud billing accounts describe <billing account>        # currency: CAD
gcloud services list --enabled --project waypoint-513003   # billingbudgets API not enabled
gcloud run services list --project waypoint-513003 --region us-central1   # none exist
gcloud iam service-accounts list --project waypoint-513003                # waypoint-run does not exist
gcloud resource-manager org-policies describe iam.allowedPolicyMemberDomains --project waypoint-513003 --effective   # allows allUsers
```

## Validate

```
Success! The configuration is valid.
```

## Plan summary

```
Plan: 7 to import, 18 to add, 0 to change, 0 to destroy.
```

All 7 imported resources import with **no attribute changes** on the first plan, so no tuning was needed.

| Resource | Action |
|---|---|
| `google_sql_database_instance.waypoint` | import |
| `google_sql_database.waypoint` | import |
| `google_bigquery_dataset.waypoint` | import |
| `google_firestore_database.default` | import |
| `google_secret_manager_secret.app["db-password"]` | import |
| `google_secret_manager_secret.app["langfuse-public-key"]` | import |
| `google_secret_manager_secret.app["langfuse-secret-key"]` | import |
| `google_service_account.run` (waypoint-run) | create |
| `google_project_iam_member.run["roles/aiplatform.user"]` | create |
| `google_project_iam_member.run["roles/cloudsql.client"]` | create |
| `google_project_iam_member.run["roles/bigquery.jobUser"]` | create |
| `google_project_iam_member.run["roles/bigquery.dataEditor"]` (project-level; dataset-level IAM needs allowlisting) | create |
| `google_project_iam_member.run["roles/datastore.user"]` | create |
| `google_project_iam_member.run["roles/cloudtrace.agent"]` | create |
| `google_project_iam_member.run["roles/logging.logWriter"]` | create |
| `google_project_iam_member.run["roles/monitoring.metricWriter"]` | create |
| `google_secret_manager_secret_iam_member.run_accessor["db-password"]` | create |
| `google_secret_manager_secret_iam_member.run_accessor["langfuse-public-key"]` | create |
| `google_secret_manager_secret_iam_member.run_accessor["langfuse-secret-key"]` | create |
| `google_cloud_run_v2_service.app["waypoint"]` | create |
| `google_cloud_run_v2_service.app["fdekit-demo"]` | create |
| `google_cloud_run_v2_service_iam_member.public["waypoint"]` (allUsers invoker) | create |
| `google_cloud_run_v2_service_iam_member.public["fdekit-demo"]` (allUsers invoker) | create |
| `google_project_service.billingbudgets[0]` | create |
| `google_billing_budget.monthly[0]` (25 CAD/month, 50/90/100%) | create |

## Things to know before applying

- **Re-planned on 2026-10-28 after the first deploy:** still 7 to import, 24 to add, 0 to change, 0 to
  destroy. The plan still *creates* `google_service_account.run` and both `google_cloud_run_v2_service.app`
  services, but `infra/bootstrap_iam.sh` and the deploy scripts have since created them. An apply as-is
  would fail with "already exists". Add `import {}` blocks for them first. The project IAM members are
  additive, so creating ones that already exist is harmless.

- **Budget currency is CAD**, not USD: the billing account bills in CAD and the Budgets API rejects a
  budget in another currency. 25 CAD is about 18 USD; raise `budget_amount` if you meant 25 USD.
- **Budget permissions were not tested by this plan.** A to-be-created budget makes no API call at plan
  time, so the plan cannot prove the caller has `billing.budgets.create` on the billing account. If apply
  fails with 403, set `enable_budget = false`.
- `billingbudgets.googleapis.com` is not enabled; apply enables it first (`disable_on_destroy = false`).
- Cloud SQL API-side deletion protection (`settings.deletion_protection_enabled`) is `false` in reality and
  kept `false` so this plan is import-only. Terraform-side `deletion_protection = true` and
  `prevent_destroy` are set.
- Cloud Run services get the placeholder image `us-docker.pkg.dev/cloudrun/container/hello` on create;
  run `infra/deploy.sh` afterwards. Terraform ignores image changes.
- Cloud Run v2 `deletion_protection` defaults to `true` in this provider, so `terraform destroy` of a
  service needs that flag turned off first.
- State is local. Move it to a GCS backend before the first apply.

## Update after review

- `billing_account` no longer has a default (the id stays out of the repo). Set it in a git-ignored
  `terraform.tfvars`; when it is empty the budget is skipped, so a plan without tfvars shows 17 to add.
  The 18-to-add figure above was planned with the billing account set.
- Added `monitoring.tf` (6 log-based distribution metrics + the request dashboard). `terraform plan` without a
  `terraform.tfvars` now reports: **Plan: 7 to import, 24 to add, 0 to change, 0 to destroy** (17 resources above
  minus the budget, plus 7 for monitoring). Still never applied.
