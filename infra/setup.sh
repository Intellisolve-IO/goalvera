#!/usr/bin/env bash
# Step 1 — cloud foundation for the Goalvera backend.
#
# Creates (idempotently; safe to re-run):
#   * required APIs
#   * Cloud KMS key ring + key in $REGION (CMEK for the bucket, 90-day rotation)
#   * the recordings bucket in $REGION: CMEK default key, uniform access,
#     public access prevention, no soft delete, lifecycle auto-delete
#       rec/          audio + sidecars       -> $AUDIO_RETENTION_DAYS days
#       transcripts/  drafts/                -> $DERIVED_RETENTION_DAYS days
#       approvals/    audit/                 -> $DERIVED_RETENTION_DAYS days, counted from the
#                                               draft's creation (customTime), so they go with it
#       (a rewritten transcript/draft, e.g. roster backfill, keeps its first creation
#        time as customTime, so rewriting never extends retention)
#       work/         temporary chunks/locks -> 1 day
#       parent_email/ parent email send log  -> $PARENT_EMAIL_RETENTION_DAYS days (365)
#   * config/ holds the demo roster (fictional); control/ holds device-token hashes
#   * three minimal service accounts (upload-api, pipeline, pipeline-trigger)
#     plus a build SA, with prefix-scoped bucket IAM (least privilege)
#   * short Cloud Logging retention
#
# Nothing here has an always-on cost except the KMS key (~$0.06/month).
# Usage:  ./infra/setup.sh            (uses infra/config.sh defaults)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

say() { printf '\n== %s\n' "$*"; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
GCS_AGENT="service-${PROJECT_NUMBER}@gs-project-accounts.iam.gserviceaccount.com"

say "Enabling APIs"
gcloud services enable \
  storage.googleapis.com cloudkms.googleapis.com speech.googleapis.com \
  aiplatform.googleapis.com run.googleapis.com eventarc.googleapis.com \
  pubsub.googleapis.com iamcredentials.googleapis.com iam.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com logging.googleapis.com

say "KMS key ring + key ($REGION)"
gcloud kms keyrings describe "$KEYRING" --location="$REGION" >/dev/null 2>&1 ||
  gcloud kms keyrings create "$KEYRING" --location="$REGION"
if ! gcloud kms keys describe "$KEY" --keyring="$KEYRING" --location="$REGION" >/dev/null 2>&1; then
  gcloud kms keys create "$KEY" --keyring="$KEYRING" --location="$REGION" \
    --purpose=encryption --protection-level=software \
    --rotation-period=90d \
    --next-rotation-time="$(date -u -d '+90 days' +%Y-%m-%dT%H:%M:%SZ)"
fi

say "Let Cloud Storage use the key"
gcloud storage service-agent --project="$PROJECT_ID" >/dev/null   # makes sure the agent exists
gcloud kms keys add-iam-policy-binding "$KEY" --keyring="$KEYRING" --location="$REGION" \
  --member="serviceAccount:${GCS_AGENT}" --role=roles/cloudkms.cryptoKeyEncrypterDecrypter \
  --condition=None >/dev/null

say "Bucket gs://$BUCKET"
LIFECYCLE="$(mktemp)"
cat >"$LIFECYCLE" <<JSON
{"rule": [
  {"action": {"type": "Delete"}, "condition": {"age": ${AUDIO_RETENTION_DAYS}, "matchesPrefix": ["rec/"]}},
  {"action": {"type": "Delete"}, "condition": {"age": ${DERIVED_RETENTION_DAYS}, "matchesPrefix": ["transcripts/", "drafts/", "approvals/", "audit/", "materials/"]}},
  {"action": {"type": "Delete"}, "condition": {"daysSinceCustomTime": ${DERIVED_RETENTION_DAYS}, "matchesPrefix": ["transcripts/", "drafts/", "approvals/", "audit/"]}},
  {"action": {"type": "Delete"}, "condition": {"age": 1, "matchesPrefix": ["work/"]}},
  {"action": {"type": "Delete"}, "condition": {"age": ${PARENT_EMAIL_RETENTION_DAYS}, "matchesPrefix": ["parent_email/"]}}
]}
JSON
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" \
    --default-storage-class=STANDARD --uniform-bucket-level-access \
    --public-access-prevention --default-encryption-key="$KMS_KEY" \
    --soft-delete-duration=0 --lifecycle-file="$LIFECYCLE"
else
  gcloud storage buckets update "gs://$BUCKET" --uniform-bucket-level-access \
    --public-access-prevention --default-encryption-key="$KMS_KEY" \
    --clear-soft-delete --lifecycle-file="$LIFECYCLE"
fi
rm -f "$LIFECYCLE"

say "Service accounts"
mk_sa() {
  gcloud iam service-accounts describe "$1@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$1" --display-name="$2"
}
mk_sa "$UPLOAD_SA_NAME"   "Goalvera - upload URL service"
mk_sa "$PIPELINE_SA_NAME" "Goalvera - transcription + draft pipeline"
mk_sa "$TRIGGER_SA_NAME"  "Goalvera - Eventarc trigger identity"
mk_sa "$BUILD_SA_NAME"    "Goalvera - Cloud Build for Cloud Run deploys"
sleep 5   # IAM propagation for brand-new accounts

say "Bucket IAM (prefix-scoped)"
prefix_bind() {  # member role prefix...
  local member="$1" role="$2"; shift 2
  local expr="" p
  for p in "$@"; do
    [[ -n "$expr" ]] && expr+=" || "
    expr+="resource.name.startsWith(\"projects/_/buckets/${BUCKET}/objects/${p}\")"
  done
  local title
  title="$(echo "${role##*/}-$*" | tr -c 'a-zA-Z0-9-' '-' | cut -c1-60)"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$member" --role="$role" \
    --condition="expression=${expr},title=${title}" >/dev/null
}
# upload-api: may create audio under rec/, read object metadata under rec/ (confirm call)
# and read the device-token registry under control/. It cannot list, overwrite or delete.
prefix_bind "serviceAccount:$UPLOAD_SA" roles/storage.objectCreator rec/
prefix_bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer rec/ control/
# pipeline: read audio, write transcripts/drafts/work (objectUser = get/create/delete, no IAM changes)
prefix_bind "serviceAccount:$PIPELINE_SA" roles/storage.objectViewer rec/ config/
prefix_bind "serviceAccount:$PIPELINE_SA" roles/storage.objectUser transcripts/ drafts/ work/

# Project *viewers* should not be able to read recordings: drop the legacy convenience grants.
gcloud storage buckets remove-iam-policy-binding "gs://$BUCKET" \
  --member="projectViewer:${PROJECT_ID}" --role=roles/storage.legacyObjectReader >/dev/null 2>&1 || true

say "Speech-to-Text service agent (reads audio for batch jobs)"
# (REST call instead of `gcloud beta services identity create`, which needs the beta component)
curl -fsS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  "https://serviceusage.googleapis.com/v1beta1/projects/${PROJECT_ID}/services/speech.googleapis.com:generateServiceIdentity" >/dev/null
SPEECH_AGENT="service-${PROJECT_NUMBER}@gcp-sa-speech.iam.gserviceaccount.com"
prefix_bind "serviceAccount:$SPEECH_AGENT" roles/storage.objectViewer rec/ work/

say "Eventarc service agent"
curl -fsS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  "https://serviceusage.googleapis.com/v1beta1/projects/${PROJECT_ID}/services/eventarc.googleapis.com:generateServiceIdentity" >/dev/null
EVENTARC_AGENT="service-${PROJECT_NUMBER}@gcp-sa-eventarc.iam.gserviceaccount.com"

say "Project IAM"
proj_bind() {
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="$1" --role="$2" \
    --condition=None >/dev/null
}
proj_bind "serviceAccount:$PIPELINE_SA" roles/speech.client
proj_bind "serviceAccount:$PIPELINE_SA" roles/aiplatform.user
proj_bind "serviceAccount:$TRIGGER_SA"  roles/eventarc.eventReceiver
proj_bind "serviceAccount:$GCS_AGENT"   roles/pubsub.publisher     # GCS -> Eventarc
proj_bind "serviceAccount:$EVENTARC_AGENT" roles/eventarc.serviceAgent
proj_bind "serviceAccount:$BUILD_SA"    roles/run.builder          # source deploys only
# upload-api signs URLs through the IAM signBlob API as itself (no key files anywhere).
gcloud iam service-accounts add-iam-policy-binding "$UPLOAD_SA" \
  --member="serviceAccount:$UPLOAD_SA" --role=roles/iam.serviceAccountTokenCreator >/dev/null

say "Short log retention (${LOG_RETENTION_DAYS} days)"
gcloud logging buckets update _Default --location=global --retention-days="$LOG_RETENTION_DAYS" >/dev/null

say "Gemini data caching off (DPA; checked below)"
curl -fsS -X PATCH -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/cacheConfig" \
  -d "{\"name\":\"projects/${PROJECT_ID}/cacheConfig\",\"disableCache\":true}" >/dev/null
for _ in 1 2 3 4 5 6; do
  if curl -fsS -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      "https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/cacheConfig" | grep -q '"disableCache": true'; then
    echo "verified: Gemini caching is off"; break
  fi
  sleep 10
done
# Gemini request/response logging stays off: it is off unless someone runs setPublisherModelConfig.

say "Done"
echo "Bucket:      gs://$BUCKET  (CMEK: $KMS_KEY)"
echo "Next:        ./infra/deploy.sh   then   ./infra/device_token.py create <device-id>"
