#!/usr/bin/env bash
# iPhone web recorder — bucket access, one-day lifecycle under web/, and CORS for the
# review site to PUT a signed URL. Idempotent. Does not deploy a service.
#
#   * upload-api: create + read web/ (signed PUT, confirm, owner.json, session.json, complete.json)
#     and read the review allowlist plus people/ (role check, including "no access")
#   * pipeline:   read and delete web/ (convert, then delete the audio pieces) and create rec/web-*
#   * lifecycle:  delete anything still under web/ after 1 day (the shortest Cloud Storage age)
#   * CORS:       the review site may PUT to storage.googleapis.com with the checksum headers
#
# Run after upload-api and review are deployed, so the origins exist:
#   ./infra/setup_web_recorder.sh
#   ./infra/setup_web_recorder.sh --check
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac

REVIEW_ORIGIN="https://${REVIEW_DOMAIN}"
UPLOAD_ORIGIN="$(gcloud run services describe "$UPLOAD_SERVICE" --region="$REGION" --format='value(status.url)' 2>/dev/null || true)"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUN_REVIEW="https://${REVIEW_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

if [[ "$CHECK" == 1 ]]; then
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json)"
  for t in upload-create-web upload-read-web upload-read-allowlist upload-read-people pipeline-use-web pipeline-create-webrec; do
    grep -q "\"$t\"" <<<"$pol" && echo "  $t: yes" || echo "  $t: NO"
  done
  gcloud storage buckets describe "gs://$BUCKET" --format='json(cors_config,lifecycle_config)'
  exit 0
fi

obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
lst() { printf 'resource.type == "storage.googleapis.com/Bucket" && api.getAttribute("storage.googleapis.com/objectListPrefix", "").startsWith("%s")' "$1"; }
bind() {  # member role expression title
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$3" "$4" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$1" \
    --role="$2" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}

say "Bucket access for web/"
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectCreator "$(obj web/)" upload-create-web
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(obj web/) || $(obj config/review_allowlist.json)" upload-read-web
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(obj people/)" upload-read-people
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(lst people/)" upload-list-people
# pipeline reads the pieces, deletes them after the WAV is stored, and creates only rec/web-*
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectUser "$(obj web/)" pipeline-use-web
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectCreator "$(obj rec/web-)" pipeline-create-webrec
echo "upload-api create/read web/; pipeline read/delete web/ and create rec/web-"

say "Lifecycle: web/ deleted after 1 day (existing rules kept)"
LC="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
python3 "$HERE/lifecycle_merge.py" merge "$LC" web/ 1 >"$LC.new"
if python3 "$HERE/lifecycle_merge.py" show "$LC" web/ 1 >/dev/null; then
  echo "lifecycle: web/ 1-day rule already there"
else
  gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LC.new" >/dev/null
  echo "lifecycle updated"
fi
rm -f "$LC" "$LC.new"

say "CORS: PUT from the review site (existing rules kept)"
CORS="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(cors_config)' >"$CORS.in"
python3 - "$CORS.in" "$REVIEW_ORIGIN" "$RUN_REVIEW" <<'PY'
import json, sys
path, review, run = sys.argv[1:]
doc = json.load(open(path)) or {}
rules = list(doc.get("cors_config") or [])
origins = [o for o in (review, run) if o.startswith("https://")]
headers = ["Content-Type", "Content-MD5", "x-goog-hash", "x-goog-if-generation-match",
           "x-goog-content-length-range"]
# One rule for the review site. Leave every other rule (the phone photo page) as it is.
keep = [r for r in rules if sorted(r.get("origin") or []) != sorted(origins)]
keep.append({"origin": origins, "method": ["PUT"], "responseHeader": headers, "maxAgeSeconds": 600})
json.dump(keep, open(path + ".out", "w"))
PY
gcloud storage buckets update "gs://$BUCKET" --cors-file="$CORS.in.out" >/dev/null
rm -f "$CORS" "$CORS.in" "$CORS.in.out"
echo "done"
echo "Next, when you are ready to deploy (this script did not deploy anything):"
echo "  ALLOW_ENV_CHANGE=1 ./infra/deploy.sh"
echo "  UPLOAD_PUBLIC_URL=<upload service origin> ALLOW_ENV_CHANGE=1 ./infra/deploy_review.sh"
