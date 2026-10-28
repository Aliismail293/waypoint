#!/usr/bin/env bash
# Read-only preflight for `gcloud run deploy --source`, called by infra/deploy.sh and fdekit-demo/deploy.sh.
# Source deploys build as the default compute SA. Without push access, Cloud Build's docker step succeeds
# and the build then fails at PUSH with FAILED_PRECONDITION and no step logs. Grants nothing; prints the fix.
set -euo pipefail
P=${1:-waypoint-513003}
PN=$(gcloud projects describe "$P" --format='value(projectNumber)')
BUILD_SA="${PN}-compute@developer.gserviceaccount.com"
RUN_SA="waypoint-run@${P}.iam.gserviceaccount.com"

roles=$(gcloud projects get-iam-policy "$P" --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:$BUILD_SA" --format='value(bindings.role)')
missing=()
# Push to Artifact Registry (any of these roles is enough).
grep -qxE 'roles/(artifactregistry\.(writer|createOnPushWriter|repoAdmin|admin)|run\.builder|editor|owner)' \
  <<<"$roles" || missing+=(roles/artifactregistry.writer)
# Step logs (the build uses CLOUD_LOGGING_ONLY, so without this the logs are empty).
grep -qxE 'roles/(logging\.(logWriter|admin)|run\.builder|editor|owner)' <<<"$roles" || missing+=(roles/logging.logWriter)

if ! gcloud iam service-accounts describe "$RUN_SA" --project "$P" >/dev/null 2>&1; then
  echo "Missing runtime service account $RUN_SA. Run infra/bootstrap_iam.sh first." >&2
  exit 1
fi
if ((${#missing[@]})); then
  echo "The build service account $BUILD_SA is missing: ${missing[*]}" >&2
  echo "Grant it (project Owner), then rerun the deploy:" >&2
  for role in "${missing[@]}"; do
    echo "  gcloud projects add-iam-policy-binding $P --member=serviceAccount:$BUILD_SA --role=$role --condition=None" >&2
  done
  exit 1
fi
echo "Deploy IAM preflight OK for $P"
