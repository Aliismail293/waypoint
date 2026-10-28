#!/usr/bin/env bash
# Deploy the DEMO service `fdekit-demo` to Cloud Run (scales to zero). Needs infra/bootstrap_iam.sh run once.
# Reuses the Waypoint runtime service account and the existing Cloud SQL instance (schema fdekit_demo).
set -euo pipefail
PROJECT=${PROJECT:-waypoint-513003}
REGION=${REGION:-us-central1}
INSTANCE="$PROJECT:$REGION:waypoint-db"
CLIENT_ID=${CLIENT_ID:-48349810745-snc3au6tu4hfgqv8srrqlq0b766lh46h.apps.googleusercontent.com}
ROOT=$(cd "$(dirname "$0")/.." && pwd)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
rsync -a --exclude .venv --exclude dist --exclude __pycache__ "$ROOT/fde-kit" "$STAGE/"
rsync -a --exclude .venv --exclude __pycache__ --exclude eval_results.json "$ROOT/fdekit-demo" "$STAGE/"
cp "$ROOT/fdekit-demo/Dockerfile" "$STAGE/Dockerfile"

gcloud run deploy fdekit-demo --source "$STAGE" --project "$PROJECT" --region "$REGION" \
  --service-account "waypoint-run@$PROJECT.iam.gserviceaccount.com" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --concurrency 10 \
  --cpu 1 --memory 512Mi --timeout 120 \
  --add-cloudsql-instances "$INSTANCE" \
  --set-secrets PARKS_DB_PASSWORD=db-password:latest \
  --set-env-vars "PARKS_LLM=vertex,PARKS_SOURCE=postgres,PARKS_DB_HOST=/cloudsql/$INSTANCE,PARKS_GCP_PROJECT=$PROJECT,PARKS_GOOGLE_CLIENT_ID=$CLIENT_ID" \
  --labels "app=fdekit-demo,purpose=demo"

URL=$(gcloud run services describe fdekit-demo --project "$PROJECT" --region "$REGION" --format='value(status.url)')
echo "Deployed: $URL  (add it as an authorised JavaScript origin on the OAuth client)"
