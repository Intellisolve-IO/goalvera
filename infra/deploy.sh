#!/usr/bin/env bash
# Deploy the two Cloud Run services + the Eventarc trigger ( steps 2-6).
# Both services scale to zero (min instances 0, request-based billing), so
# idle cost is $0. Run ./infra/setup.sh first.
#
#   ./infra/deploy.sh             roster + upload-api + pipeline + trigger (as before)
#   ./infra/deploy.sh pipeline    roster + pipeline + trigger only (upload-api untouched)
#   ./infra/deploy.sh upload-api  roster + upload-api only
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
# --set-env-vars replaces a service's whole env. Stop if the live service has settings this
# script would drop or change, so nothing flips silently (ALLOW_ENV_CHANGE=1 to accept the diff).
check_env() {  # service "K=V,K=V"
  local live
  live="$(gcloud run services describe "$1" --region="$REGION" --format=json 2>/dev/null || true)"
  [[ -n "$live" ]] || { echo "$1: not deployed yet"; return 0; }
  if ! LIVE="$live" python3 - "$1" "$2" <<'PY'
import json, os, sys
svc, want = sys.argv[1], dict(kv.split("=", 1) for kv in sys.argv[2].split(","))
env = json.loads(os.environ["LIVE"])["spec"]["template"]["spec"]["containers"][0].get("env", [])
have = {e["name"]: e.get("value", "<from secret>") for e in env}
diff = [f"  {k}: {have.get(k, '<unset>')!r} -> {want.get(k, '<removed>')!r}"
        for k in sorted(set(have) | set(want)) if have.get(k) != want.get(k)]
if diff:
    print(f"{svc}: this deploy would change live settings:", *diff, sep="\n", file=sys.stderr)
    sys.exit(1)
print(f"{svc}: live settings match ({len(want)} env vars)")
PY
  then
    [[ "${ALLOW_ENV_CHANGE:-0}" == 1 ]] || { echo "stopping; nothing deployed for $1 (ALLOW_ENV_CHANGE=1 to accept)" >&2; exit 1; }
    echo "ALLOW_ENV_CHANGE=1: going ahead with the change above"
  fi
}
UPLOAD_ENV="BUCKET=$BUCKET,EXPECTED_KMS_KEY=$KMS_KEY,SCHOOL_PROFILE=$SCHOOL_PROFILE"
PIPELINE_ENV="BUCKET=$BUCKET,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,SPEECH_LOCATION=$SPEECH_LOCATION,VERTEX_AI=1,GEMINI_LOCATION=$GEMINI_LOCATION,GEMINI_MODEL=$GEMINI_MODEL,REC_KMS_KEYRING_PATH=$REC_KMS_KEYRING_PATH"
ONLY="${1:-all}"
case "$ONLY" in
  all|pipeline|upload-api) ;;
  *) echo "usage: $0 [all|pipeline|upload-api]" >&2; exit 2 ;;
esac

say "Live settings check"
[[ "$ONLY" == pipeline ]] || check_env "$UPLOAD_SERVICE" "$UPLOAD_ENV"
[[ "$ONLY" == upload-api ]] || check_env "$PIPELINE_SERVICE" "$PIPELINE_ENV"

say "Demo roster (fictional) -> gs://$BUCKET/config/demo_roster.json"
gcloud storage cp "$ROOT/app/samples/demo_roster.json" "gs://$BUCKET/config/demo_roster.json" --quiet

if [[ "$ONLY" != pipeline ]]; then
say "Upload service ($UPLOAD_SERVICE)"
cp "$ROOT/app/backend-pipeline/common/safelog.py" "$ROOT/app/upload-api/safelog.py.new"
{ echo "# Copy of app/backend-pipeline/common/safelog.py (kept in sync by infra/deploy.sh)."; cat "$ROOT/app/upload-api/safelog.py.new"; } > "$ROOT/app/upload-api/safelog.py"
rm -f "$ROOT/app/upload-api/safelog.py.new"
gcloud run deploy "$UPLOAD_SERVICE" --source="$ROOT/app/upload-api" --region="$REGION" \
  --service-account="$UPLOAD_SA" --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --no-invoker-iam-check --ingress=all \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --concurrency=20 --timeout=60 \
  --set-env-vars="$UPLOAD_ENV" --quiet
fi

if [[ "$ONLY" != upload-api ]]; then
say "Pipeline service ($PIPELINE_SERVICE, private)"
gcloud run deploy "$PIPELINE_SERVICE" --source="$ROOT/app/backend-pipeline" --region="$REGION" \
  --service-account="$PIPELINE_SA" --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --no-allow-unauthenticated --ingress=all \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=1Gi --concurrency=4 --timeout=300 \
  --set-env-vars="$PIPELINE_ENV" \
  --quiet
gcloud run services add-iam-policy-binding "$PIPELINE_SERVICE" --region="$REGION" \
  --member="serviceAccount:$TRIGGER_SA" --role=roles/run.invoker >/dev/null

say "Eventarc trigger (object finalized in gs://$BUCKET -> $PIPELINE_SERVICE)"
if ! gcloud eventarc triggers describe pipeline-gcs --location="$REGION" >/dev/null 2>&1; then
  gcloud eventarc triggers create pipeline-gcs --location="$REGION" \
    --destination-run-service="$PIPELINE_SERVICE" --destination-run-region="$REGION" \
    --event-filters="type=google.cloud.storage.object.v1.finalized" \
    --event-filters="bucket=$BUCKET" \
    --service-account="$TRIGGER_SA"
fi
fi

URL="$(gcloud run services describe "$UPLOAD_SERVICE" --region="$REGION" --format='value(status.url)')"
say "Done"
echo "Upload service URL: $URL"
echo "Create a device token: ./infra/device_token.py create <device-id>"
