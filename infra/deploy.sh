#!/usr/bin/env bash
# Deploy Waypoint to Cloud Run from source (Cloud Build builds the Dockerfile; no local Docker).
# Prereq once: infra/bootstrap_iam.sh. Scale-to-zero, max 2 instances, secrets from Secret Manager.
set -euo pipefail
P=${PROJECT:-waypoint-513003}
REGION=us-central1
SERVICE=${SERVICE:-waypoint}
SQL_INSTANCE="$P:$REGION:waypoint-db"
CLIENT_ID=${GOOGLE_CLIENT_ID:-48349810745-snc3au6tu4hfgqv8srrqlq0b766lh46h.apps.googleusercontent.com}

gcloud run deploy "$SERVICE" \
  --project "$P" --region "$REGION" --source . \
  --service-account "waypoint-run@$P.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --concurrency 10 \
  --cpu 1 --memory 1Gi --timeout 300 --cpu-boost \
  --add-cloudsql-instances "$SQL_INSTANCE" \
  --set-secrets "WAYPOINT_DB_PASSWORD=db-password:latest,WAYPOINT_LANGFUSE_PUBLIC_KEY=langfuse-public-key:latest,WAYPOINT_LANGFUSE_SECRET_KEY=langfuse-secret-key:latest" \
  --set-env-vars "^@^WAYPOINT_LLM=vertex@WAYPOINT_EMBEDDINGS=vertex@WAYPOINT_STORE=pgvector@WAYPOINT_CATALOG=bigquery@WAYPOINT_SESSIONS=firestore@WAYPOINT_CLOUD_TRACE=true@WAYPOINT_DB_HOST=/cloudsql/$SQL_INSTANCE@WAYPOINT_GOOGLE_CLIENT_ID=$CLIENT_ID@WAYPOINT_GCP_PROJECT=$P"

gcloud run services describe "$SERVICE" --project "$P" --region "$REGION" --format 'value(status.url)'
