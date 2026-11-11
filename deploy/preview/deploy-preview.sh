#!/usr/bin/env bash
# Build one image per service from this commit and deploy those digests to the
# Goalvera preview project only (PREVIEW_PROJECT_ID, which must end in
# "-preview"). Never deploys the landing site or any live demo project.
# Promote / rollback is out of scope for now.
#
#   PREVIEW_PROJECT_ID=... PREVIEW_PROJECT_NUMBER=... ./deploy/preview/deploy-preview.sh
#   ... ./deploy/preview/deploy-preview.sh --sha SHA
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
# shellcheck source=env.sh
source "${HERE}/env.sh"

SHA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:?--sha needs a commit}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "${SHA}" ]]; then
  SHA="$(git -C "${ROOT}" rev-parse HEAD)"
fi
SHORT="$(git -C "${ROOT}" rev-parse --short=12 "${SHA}")"
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"
: "${CLOUDSDK_CORE_PROJECT:?}"
[[ "${PROJECT_ID}" == *-preview ]] || { echo "refusing: PROJECT_ID does not end in -preview" >&2; exit 2; }

GCLOUD=(gcloud --project="${PROJECT_ID}")
if [[ -n "${CLOUDSDK_CORE_ACCOUNT:-}" ]]; then
  GCLOUD+=(--account="${CLOUDSDK_CORE_ACCOUNT}")
fi
if [[ -n "${CLOUDSDK_BILLING_QUOTA_PROJECT:-}" ]]; then
  GCLOUD+=(--billing-project="${CLOUDSDK_BILLING_QUOTA_PROJECT}")
fi

echo "Building and deploying commit ${SHA} (${SHORT}) to ${PROJECT_ID} (${REGION})"
echo "All Cloud Run services use an explicit runtime SA; builds use ${BUILD_SA}."

build_image() {
  local name="$1" context="$2" dockerfile="$3"
  local tag="${AR_REPO}/${name}:${SHORT}"
  echo "== build ${name} -> ${tag}" >&2
  "${GCLOUD[@]}" builds submit "${context}" \
    --tag="${tag}" \
    --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
    --gcs-log-dir="gs://run-sources-${PROJECT_ID}-${REGION}/logs" \
    --gcs-source-staging-dir="gs://run-sources-${PROJECT_ID}-${REGION}/source" \
    --timeout=1200s \
    --quiet >&2
  local digest
  digest="$("${GCLOUD[@]}" artifacts docker images describe "${tag}" --format='value(image_summary.digest)')"
  echo "${AR_REPO}/${name}@${digest}"
}

stage_review_context() {
  local ctx
  ctx="$(mktemp -d)"
  mkdir -p "${ctx}/frontend-web" "${ctx}/backend-pipeline" "${ctx}/review-web" "${ctx}/samples"
  ( cd "${ROOT}/app/frontend-web" && tar --exclude=node_modules --exclude=dist -cf - . ) | tar -xf - -C "${ctx}/frontend-web"
  ( cd "${ROOT}/app/backend-pipeline" && tar --exclude=__pycache__ --exclude=tests -cf - common gemini_analyzer pipeline_service review_api ) | tar -xf - -C "${ctx}/backend-pipeline"
  ( cd "${ROOT}/app" && tar --exclude='*.wav' -cf - samples ) | tar -xf - -C "${ctx}"
  cp "${ROOT}/app/review-web/Dockerfile" "${ROOT}/app/review-web/requirements.txt" "${ctx}/review-web/"
  cp "${ROOT}/app/review-web/Dockerfile" "${ctx}/Dockerfile"
  cp "${ROOT}/product.json" "${ctx}/product.json"
  printf '{"commit": "%s"}\n' "${SHORT}" >"${ctx}/backend-pipeline/review_api/build_info.json"
  echo "${ctx}"
}

stage_upload_context() {
  local ctx
  ctx="$(mktemp -d)"
  cp "${ROOT}/app/upload-api/Dockerfile" \
     "${ROOT}/app/upload-api/requirements.txt" \
     "${ROOT}/app/upload-api/main.py" \
     "${ROOT}/app/upload-api/media.py" \
     "${ROOT}/app/upload-api/school.py" \
     "${ROOT}/app/upload-api/ota_log.py" \
     "${ROOT}/app/upload-api/idp_auth.py" \
     "${ROOT}/app/upload-api/web_rec.py" \
     "${ROOT}/app/upload-api/upload_page.html" \
     "${ctx}/"
  cp "${ROOT}/app/backend-pipeline/common/safelog.py" "${ctx}/safelog.py"
  echo "${ctx}"
}

ensure_sources_bucket() {
  local src="gs://run-sources-${PROJECT_ID}-${REGION}"
  if ! "${GCLOUD[@]}" storage buckets describe "${src}" >/dev/null 2>&1; then
    "${GCLOUD[@]}" storage buckets create "${src}" --location="${REGION}" \
      --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=0
  fi
  # User-specified Cloud Build SA needs bucket-level storage.admin, not only objectAdmin.
  # The Cloud Build service agents also need access to the staging and log prefixes.
  local sa
  for sa in \
      "${BUILD_SA}" \
      "${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
      "service-${PROJECT_NUMBER}@gcp-sa-cloudbuild.iam.gserviceaccount.com"; do
    "${GCLOUD[@]}" storage buckets add-iam-policy-binding "${src}" \
      --member="serviceAccount:${sa}" --role=roles/storage.admin >/dev/null || true
  done
}

ensure_sources_bucket

UPLOAD_CTX="$(stage_upload_context)"
UPLOAD_IMAGE="$(build_image upload-api "${UPLOAD_CTX}" Dockerfile)"
rm -rf "${UPLOAD_CTX}"

PIPELINE_IMAGE="$(build_image pipeline "${ROOT}/app/backend-pipeline" Dockerfile)"

REVIEW_CTX="$(stage_review_context)"
REVIEW_IMAGE="$(build_image review "${REVIEW_CTX}" Dockerfile)"
rm -rf "${REVIEW_CTX}"

UPLOAD_ENV="BUCKET=${BUCKET},EXPECTED_KMS_KEY=${KMS_KEY},SCHOOL_PROFILE=${SCHOOL_PROFILE},PROJECT_ID=${PROJECT_ID},IDP_PROVIDERS=${IDP_PROVIDERS},WEB_ALLOWED_ORIGINS=https://${REVIEW_DOMAIN}"
PIPELINE_ENV="BUCKET=${BUCKET},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},SPEECH_LOCATION=${SPEECH_LOCATION},VERTEX_AI=1,GEMINI_LOCATION=${GEMINI_LOCATION},GEMINI_MODEL=${GEMINI_MODEL},REC_KMS_KEYRING_PATH=${REC_KMS_KEYRING_PATH}"

echo "== deploy ${UPLOAD_SERVICE} ${UPLOAD_IMAGE}"
"${GCLOUD[@]}" run deploy "${UPLOAD_SERVICE}" --region="${REGION}" \
  --image="${UPLOAD_IMAGE}" \
  --service-account="${UPLOAD_SA}" \
  --no-invoker-iam-check --ingress=all \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --concurrency=20 --timeout=60 \
  --set-env-vars="${UPLOAD_ENV}" \
  --update-labels="app=goalvera,env=preview,commit=${SHORT}" \
  --quiet

echo "== deploy ${PIPELINE_SERVICE} ${PIPELINE_IMAGE}"
"${GCLOUD[@]}" run deploy "${PIPELINE_SERVICE}" --region="${REGION}" \
  --image="${PIPELINE_IMAGE}" \
  --service-account="${PIPELINE_SA}" \
  --no-allow-unauthenticated --ingress=all \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=2Gi --concurrency=4 --timeout=900 \
  --set-env-vars="${PIPELINE_ENV}" \
  --update-labels="app=goalvera,env=preview,commit=${SHORT}" \
  --quiet
"${GCLOUD[@]}" run services add-iam-policy-binding "${PIPELINE_SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${TRIGGER_SA}" --role=roles/run.invoker >/dev/null || true

if ! "${GCLOUD[@]}" eventarc triggers describe pipeline-gcs --location="${REGION}" >/dev/null 2>&1; then
  echo "== Eventarc trigger pipeline-gcs"
  if ! "${GCLOUD[@]}" eventarc triggers create pipeline-gcs --location="${REGION}" \
      --destination-run-service="${PIPELINE_SERVICE}" --destination-run-region="${REGION}" \
      --event-filters="type=google.cloud.storage.object.v1.finalized" \
      --event-filters="bucket=${BUCKET}" \
      --service-account="${TRIGGER_SA}"; then
    echo "Eventarc trigger was not created. The Eventarc service agent likely needs roles/iam.serviceAccountUser on ${TRIGGER_SA}. A project owner must grant it. See docs/PREVIEW.md."
  fi
else
  echo "Eventarc trigger pipeline-gcs already exists"
fi

echo "== deploy ${REVIEW_SERVICE} ${REVIEW_IMAGE}"
# The review API key secret is required. If Identity Platform has not been enabled yet, skip review until it is enabled and the secret exists.
if "${GCLOUD[@]}" secrets describe "${REVIEW_API_KEY_SECRET}" >/dev/null 2>&1; then
  "${GCLOUD[@]}" run deploy "${REVIEW_SERVICE}" --region="${REGION}" \
    --image="${REVIEW_IMAGE}" \
    --service-account="${REVIEW_SA}" \
    --no-invoker-iam-check --ingress=all \
    --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --concurrency=40 --timeout=120 \
    --set-secrets="IDP_API_KEY=${REVIEW_API_KEY_SECRET}:latest" \
    --set-env-vars="^|^APP_COMMIT=${SHORT}|BUCKET=${BUCKET}|PROJECT_ID=${PROJECT_ID}|IDP_AUTH_DOMAIN=${PROJECT_ID}.firebaseapp.com|REVIEW_ALLOWED_ORIGINS=https://${REVIEW_DOMAIN}|JOURNAL_MODE=bigquery|JOURNAL_DATASET=${JOURNAL_DATASET}|JOURNAL_TABLE=${JOURNAL_TABLE}|JOURNAL_REPORTS_DATASET=${JOURNAL_REPORTS_DATASET}|JOURNAL_READER_SA=${JOURNAL_READER_SA}|JOURNAL_LOCATION=${REGION}|JOURNAL_MAX_BYTES=1073741824|SCHOOL_PROFILE=${SCHOOL_PROFILE}|PLANNER_AI=${PLANNER_AI}|VERTEX_AI=1|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|GEMINI_LOCATION=${GEMINI_LOCATION}|GEMINI_MODEL=${GEMINI_MODEL}|REVIEW_URL=https://${REVIEW_DOMAIN}|MATERIALS_EMAIL_DEFAULT=on|MATERIALS_MAIL_DOMAINS=staff.example|PARENT_EMAIL_REDIRECT_DEFAULT=on|PROFILE_SELF_EDIT_ACCESS=off|IDP_PROVIDERS=${IDP_PROVIDERS}" \
    --update-labels="app=goalvera,env=preview,commit=${SHORT}" \
    --quiet
else
  echo "Secret ${REVIEW_API_KEY_SECRET} is missing. Skipping review deploy. Enable Identity Platform in the preview project and create the secret first. See docs/PREVIEW.md."
fi

UPLOAD_URL="$("${GCLOUD[@]}" run services describe "${UPLOAD_SERVICE}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
PIPELINE_URL="$("${GCLOUD[@]}" run services describe "${PIPELINE_SERVICE}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
REVIEW_URL="$("${GCLOUD[@]}" run services describe "${REVIEW_SERVICE}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
echo
echo "Preview deploy complete for ${SHORT}"
echo "review:    ${REVIEW_URL:-not deployed (Identity Platform / API key secret pending)}"
echo "upload-api:${UPLOAD_URL:-not deployed}"
echo "pipeline:  ${PIPELINE_URL:-not deployed} (private; Eventarc invoker only)"
echo "Promote to a live demo is out of scope for now."
