# Runtime identity for both Cloud Run services. Mirrors infra/bootstrap_iam.sh.
# Every grant is an additive *_iam_member: Terraform never rewrites a whole policy, so bindings
# made outside Terraform (owners, Google-managed service agents) are left alone.

resource "google_service_account" "run" {
  account_id   = "waypoint-run"
  display_name = "Waypoint Cloud Run runtime"
}

locals {
  run_sa_member = "serviceAccount:${google_service_account.run.email}"

  # Gemini/embeddings, Cloud SQL connector, BigQuery jobs, Firestore sessions, and telemetry.
  run_project_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudsql.client",
    "roles/bigquery.jobUser",
    "roles/datastore.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
}

resource "google_project_iam_member" "run" {
  for_each = local.run_project_roles
  project  = var.project_id
  role     = each.key
  member   = local.run_sa_member
}

# Data access is scoped to the one dataset rather than granted project-wide.
resource "google_bigquery_dataset_iam_member" "run_data_editor" {
  dataset_id = google_bigquery_dataset.waypoint.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.run_sa_member
}

# Per-secret access, so a future secret in the project is not readable by default.
resource "google_secret_manager_secret_iam_member" "run_accessor" {
  for_each  = google_secret_manager_secret.app
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.run_sa_member
}
