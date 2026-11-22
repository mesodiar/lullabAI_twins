#!/usr/bin/env bash
# Provision the private Cloud Storage → Eventarc → Cloud Run review pipeline.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-lullabai-twins}"
ACCOUNT="${GCP_ACCOUNT:-mils.neon@gmail.com}"
REGION="${GCP_LOCATION:-asia-southeast1}"
DATABASE="${GCP_FIRESTORE_DATABASE:-lullabai-events}"
BUCKET="${GCS_EVENTS_BUCKET:-${PROJECT_ID}-lullabai-events}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
VERTEX_MODEL="${VERTEX_MODEL_ID:-gemini-3.1-flash-lite}"
SERVICE="lullabai-analyzer"
TRIGGER="${SERVICE}-gcs-trigger"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../analyzer-service" && pwd)"

gcp() {
  gcloud "$@" --project="$PROJECT_ID" --account="$ACCOUNT" --quiet
}

if [[ "$(gcp billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" != True ]]; then
  echo "Enable billing for $PROJECT_ID before provisioning." >&2
  exit 1
fi

gcp services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  eventarc.googleapis.com eventarcpublishing.googleapis.com pubsub.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com storage.googleapis.com iamcredentials.googleapis.com
gcp beta services identity create --service=eventarc.googleapis.com

if ! gcp storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcp storage buckets create "gs://$BUCKET" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
lifecycle="$(mktemp)"
trap 'rm -f "$lifecycle"' EXIT
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > "$lifecycle"
gcp storage buckets update "gs://$BUCKET" --public-access-prevention --uniform-bucket-level-access \
  --soft-delete-duration=0 --lifecycle-file="$lifecycle"

if ! gcp firestore databases describe --database="$DATABASE" >/dev/null 2>&1; then
  gcp firestore databases create --database="$DATABASE" --location="$REGION" --type=firestore-native
fi
gcp firestore fields ttls update expires_at --collection-group=events --database="$DATABASE" --enable-ttl --async

for service_account in lullabai-analyzer lullabai-edge lullabai-eventarc lullabai-build; do
  email="${service_account}@${PROJECT_ID}.iam.gserviceaccount.com"
  if ! gcp iam service-accounts describe "$email" >/dev/null 2>&1; then
    gcp iam service-accounts create "$service_account"
  fi
done
ANALYZER="lullabai-analyzer@${PROJECT_ID}.iam.gserviceaccount.com"
EDGE="lullabai-edge@${PROJECT_ID}.iam.gserviceaccount.com"
EVENTARC="lullabai-eventarc@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD="lullabai-build@${PROJECT_ID}.iam.gserviceaccount.com"

project_role() {
  gcp projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" \
    --role="$2" --condition="${3:-None}" >/dev/null
}
database_condition="expression=resource.name==\"projects/$PROJECT_ID/databases/$DATABASE\",title=lullabai-events-only"
project_role "$ANALYZER" roles/datastore.user "$database_condition"
project_role "$EDGE" roles/datastore.user "$database_condition"
project_role "$ANALYZER" roles/aiplatform.user
project_role "$EVENTARC" roles/eventarc.eventReceiver
project_role "$BUILD" roles/run.builder
project_role "$(gcp storage service-agent)" roles/pubsub.publisher
gcp iam service-accounts add-iam-policy-binding "$EDGE" --member="user:$ACCOUNT" \
  --role=roles/iam.serviceAccountTokenCreator --condition=None >/dev/null

gcp storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$EDGE" --role=roles/storage.objectCreator >/dev/null
gcp storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$ANALYZER" --role=roles/storage.objectViewer >/dev/null

gcp run deploy "$SERVICE" --source="$SOURCE_DIR" --region="$REGION" --service-account="$ANALYZER" \
  --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD" \
  --no-allow-unauthenticated --invoker-iam-check --min-instances=0 --max=1 --concurrency=1 --timeout=90s \
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID,GCP_FIRESTORE_DATABASE=$DATABASE,GCS_EVENTS_BUCKET=$BUCKET,VERTEX_LOCATION=$VERTEX_LOCATION,VERTEX_MODEL_ID=$VERTEX_MODEL"

gcp run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="serviceAccount:$EVENTARC" --role=roles/run.invoker --condition=None >/dev/null
if ! gcp eventarc triggers describe "$TRIGGER" --location="$REGION" >/dev/null 2>&1; then
  for attempt in {1..10}; do
    if output="$(gcp eventarc triggers create "$TRIGGER" --location="$REGION" \
      --destination-run-service="$SERVICE" --destination-run-region="$REGION" \
      --event-filters="type=google.cloud.storage.object.v1.finalized" --event-filters="bucket=$BUCKET" \
      --service-account="$EVENTARC" 2>&1)"; then
      printf '%s\n' "$output"
      break
    fi
    printf '%s\n' "$output" >&2
    if [[ "$output" != *"Permission denied while using the Eventarc Service Agent"* || "$attempt" == 10 ]]; then
      exit 1
    fi
    sleep 15
  done
fi
echo "Cloud review provisioned for $PROJECT_ID in $REGION."
