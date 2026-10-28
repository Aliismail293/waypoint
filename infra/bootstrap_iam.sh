#!/usr/bin/env bash
# One-time, run by a project Owner: service accounts and roles for Waypoint and fdekit-demo.
# Review before running. Safe to re-run: every grant is additive. Terraform (infra/terraform) declares the
# same runtime bindings. Every command's errors are shown, and any failure stops the script with the
# failing command, so "IAM bootstrap done" means every grant succeeded.
set -euo pipefail
trap 'echo "FAILED (exit $?): $BASH_COMMAND" >&2; echo "IAM bootstrap NOT complete." >&2' ERR

P=${PROJECT:-waypoint-513003}
PN=$(gcloud projects describe "$P" --format='value(projectNumber)')
RUN_SA="waypoint-run@${P}.iam.gserviceaccount.com"
BUILD_SA="${PN}-compute@developer.gserviceaccount.com"   # used by `gcloud run deploy --source` builds

grant_project() {  # grant_project MEMBER ROLE
  gcloud projects add-iam-policy-binding "$P" --member "$1" --role "$2" --condition=None --quiet --format=none
  echo "  granted $2 to $1"
}

if gcloud iam service-accounts describe "$RUN_SA" --project "$P" --format=none 2>/dev/null; then
  echo "Runtime service account exists: $RUN_SA"
else
  gcloud iam service-accounts create waypoint-run --project "$P" --display-name "Waypoint Cloud Run runtime"
fi

# Runtime: call Gemini/embeddings, connect to Cloud SQL, run BigQuery jobs and read/write tables, read/write
# Firestore sessions, write traces/logs/metrics, read three named secrets.
# BigQuery data access is project-level: dataset-level grants (`bq add-iam-policy-binding`) need allowlisting
# on this project. The project has one dataset (`waypoint`), so the effective scope is the same today.
echo "Runtime roles for $RUN_SA:"
for role in roles/aiplatform.user roles/cloudsql.client roles/bigquery.jobUser roles/bigquery.dataEditor \
            roles/datastore.user roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter; do
  grant_project "serviceAccount:$RUN_SA" "$role"
done
for secret in db-password langfuse-public-key langfuse-secret-key; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$P" --member "serviceAccount:$RUN_SA" \
    --role roles/secretmanager.secretAccessor --quiet --format=none
  echo "  granted roles/secretmanager.secretAccessor on secret $secret"
done

# Source deploys: the build runs as the default compute SA, which has no roles in this project.
echo "Build roles for $BUILD_SA:"
for role in roles/cloudbuild.builds.builder roles/artifactregistry.writer roles/logging.logWriter \
            roles/storage.objectViewer; do
  grant_project "serviceAccount:$BUILD_SA" "$role"
done

echo "IAM bootstrap done for $P"
