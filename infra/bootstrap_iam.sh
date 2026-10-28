#!/usr/bin/env bash
# One-time, run by a project Owner: least-privilege service accounts for Waypoint.
# Review before running. Terraform (infra/terraform) declares the same bindings for Phase 2.
set -euo pipefail
P=${PROJECT:-waypoint-513003}
PN=$(gcloud projects describe "$P" --format='value(projectNumber)')
RUN_SA="waypoint-run@${P}.iam.gserviceaccount.com"
BUILD_SA="${PN}-compute@developer.gserviceaccount.com"   # used by `gcloud run deploy --source` builds

gcloud iam service-accounts describe "$RUN_SA" --project "$P" >/dev/null 2>&1 || \
  gcloud iam service-accounts create waypoint-run --project "$P" --display-name "Waypoint Cloud Run runtime"

# Runtime: call Gemini/embeddings, connect to Cloud SQL, run BigQuery jobs, read the waypoint dataset,
# read/write Firestore sessions, write traces/logs/metrics, read three named secrets.
for role in roles/aiplatform.user roles/cloudsql.client roles/bigquery.jobUser roles/datastore.user \
            roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:$RUN_SA" --role "$role" --condition=None --quiet >/dev/null
done
bq add-iam-policy-binding --member="serviceAccount:$RUN_SA" --role=roles/bigquery.dataEditor "$P:waypoint" >/dev/null
for secret in db-password langfuse-public-key langfuse-secret-key; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$P" --member "serviceAccount:$RUN_SA" \
    --role roles/secretmanager.secretAccessor --quiet >/dev/null
done

# Source deploys: the build runs as the default compute SA, which has no roles in this project.
for role in roles/cloudbuild.builds.builder roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:$BUILD_SA" --role "$role" --condition=None --quiet >/dev/null
done
echo "IAM bootstrap done for $P"
