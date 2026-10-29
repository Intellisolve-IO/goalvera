#!/usr/bin/env bash
# Teacher Notes photos / videos from a phone (tasks#112) - one-time cloud setup. Idempotent.
# Demo project only: stays inside $PROJECT_ID (example-project); creates no project, bucket, key or SA.
#
#   * upload-api: create-only + read under media/ (uploads, their session link) and media_tokens/
#     (one-time QR tokens, stored as SHA-256 only); it cannot list, overwrite or delete
#   * pipeline:   read + list media/ (photos into the session analysis)
#   * review-web: read media/ (the teacher sees the photo / video in review)
#   * bucket CORS: browsers may PUT to signed URLs only from the upload-api origins (both run.app URLs)
#   * retention: media/ and media_tokens/ deleted after $AUDIO_RETENTION_DAYS days (other rules kept);
#     after the demo run infra/delete_demo_media.sh to delete them at once
#
#   ./infra/setup_media.sh           set up
#   ./infra/setup_media.sh --check   read-only
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
[[ "$PROJECT_ID" == example-project ]] || { echo "demo project only (PROJECT_ID=$PROJECT_ID)" >&2; exit 1; }
ORIGIN="$(gcloud run services describe "$UPLOAD_SERVICE" --region="$REGION" --format='value(status.url)')"
# Cloud Run serves the same service at two URLs; the firmware (and so the QR link) uses the deterministic one
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
ORIGIN2="https://${UPLOAD_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
[[ "$ORIGIN" == https://* ]] || { echo "upload-api not deployed" >&2; exit 1; }

if [[ "$CHECK" == 1 ]]; then
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json)"
  for t in upload-create-media upload-read-media upload-read-branding upload-list-branding pipeline-read-media pipeline-list-media; do
    grep -q "\"$t\"" <<<"$pol" && echo "  $t: yes" || echo "  $t: NO"
  done
  # review-web's viewer grants are merged (GCS max 20 conditions per role+member): look for media/ in them
  REVIEW_SA="$REVIEW_SA" python3 -c 'import json,os,sys; p=json.load(sys.stdin); m="serviceAccount:"+os.environ["REVIEW_SA"]
ok=any(m in b["members"] and b["role"]=="roles/storage.objectViewer" and "/objects/media/\")" in b.get("condition",{}).get("expression","") for b in p["bindings"])
print("  review-read-media:", "yes" if ok else "NO")' <<<"$pol"
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
say "Bucket access"
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectCreator "$(obj media/) || $(obj media_tokens/)" upload-create-media
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(obj media/) || $(obj media_tokens/)" upload-read-media
# recorder banner school name (app/upload-api/school.py): read + list the review app's branding/ records
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(obj branding/)" upload-read-branding
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(lst branding/)" upload-list-branding
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectViewer "$(obj media/)" pipeline-read-media
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectViewer "$(lst media/)" pipeline-list-media
# review-web already holds ~20 conditional viewer grants (GCS limit), so media/ is added by merging them
python3 "$HERE/consolidate_review_iam.py" "$BUCKET" "serviceAccount:$REVIEW_SA" --add-read media/
echo "upload-api create/read media/ + media_tokens/; pipeline read/list media/; review-web read media/"

say "CORS: PUT from $ORIGIN and $ORIGIN2 only"
CORS="$(mktemp)"
python3 -c 'import json,sys; print(json.dumps([{"origin":sorted(set(sys.argv[1:3])),"method":["PUT"],
  "responseHeader":["Content-Type","x-goog-if-generation-match","x-goog-content-length-range"],"maxAgeSeconds":600}]))' \
  "$ORIGIN" "$ORIGIN2" >"$CORS"
gcloud storage buckets update "gs://$BUCKET" --cors-file="$CORS" >/dev/null
rm -f "$CORS"

say "Retention: media/ and media_tokens/ deleted after ${AUDIO_RETENTION_DAYS} days (existing rules kept)"
LC="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
python3 - "$LC" "$AUDIO_RETENTION_DAYS" <<'PY'
import json, sys
path, days = sys.argv[1], int(sys.argv[2])
cfg = (json.load(open(path)) or {}).get("lifecycle_config") or {}
rules = cfg.get("rule") or []
if not any((r.get("condition") or {}).get("matchesPrefix") == ["media/", "media_tokens/"] for r in rules):
    rules.append({"action": {"type": "Delete"}, "condition": {"age": days, "matchesPrefix": ["media/", "media_tokens/"]}})
json.dump({"rule": rules}, open(path, "w"))
PY
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LC" >/dev/null
rm -f "$LC"
echo "done"
