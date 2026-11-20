#!/usr/bin/env bash
# ==============================================================================
# LullabAI Twins — Google Cloud & Firebase Infrastructure Provisioning Script
# Provisions:
#   1. Cloud Storage Bucket with 7-day Auto-Delete Lifecycle Rule
#   2. Cloud Firestore (Native Mode)
#   3. Cloud Run Service (System 2 Analyzer + Vertex AI Gemini)
#   4. Eventarc Trigger (GCS Object Finalized -> Cloud Run)
# ==============================================================================

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Please set GCP_PROJECT_ID}"
REGION="${GCP_LOCATION:-asia-southeast1}"
BUCKET_NAME="${GCS_EVENTS_BUCKET:-${PROJECT_ID}-lullabai-events}"
SERVICE_NAME="lullabai-analyzer"
VERTEX_MODEL="${VERTEX_MODEL_ID:-gemini-2.5-flash}"

echo "==> [1/5] Enabling required Google Cloud APIs on project ${PROJECT_ID}..."
gcloud services enable \
  run.googleapis.com \
  eventarc.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

echo "==> [2/5] Creating Cloud Storage bucket gs://${BUCKET_NAME} with 7-day TTL..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
fi

cat > /tmp/lullabai_gcs_lifecycle.json <<EOF
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 7}
    }
  ]
}
EOF
gcloud storage buckets update "gs://${BUCKET_NAME}" \
  --lifecycle-file=/tmp/lullabai_gcs_lifecycle.json \
  --project="${PROJECT_ID}"

echo "==> [3/5] Ensuring Cloud Firestore Native Database exists..."
gcloud firestore databases create \
  --location="${REGION}" \
  --type=firestore-native \
  --project="${PROJECT_ID}" 2>/dev/null || echo "Firestore database already exists."

echo "==> [4/5] Deploying Cloud Run service (${SERVICE_NAME})..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gcloud run deploy "${SERVICE_NAME}" \
  --source="${SCRIPT_DIR}/../analyzer-service" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${REGION},VERTEX_MODEL_ID=${VERTEX_MODEL}"

echo "==> [5/5] Creating Eventarc Trigger (GCS Object Finalize -> Cloud Run)..."
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
GCS_SA="$(gcloud storage service-agent --project="${PROJECT_ID}")"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GCS_SA}" \
  --role="roles/pubsub.publisher" >/dev/null

gcloud eventarc triggers create "${SERVICE_NAME}-gcs-trigger" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --destination-run-service="${SERVICE_NAME}" \
  --destination-run-region="${REGION}" \
  --event-filters="type=google.cloud.storage.object.v1.finalized" \
  --event-filters="bucket=${BUCKET_NAME}" \
  --service-account="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" || echo "Trigger already configured."

echo "✅ LullabAI Twins Cloud Infrastructure Ready!"

