#!/usr/bin/env bash
# Teacher Notes photos / videos from a phone — idempotent cloud setup for any
# configured project (PROJECT_ID from the environment or infra/deploy.env).
# Creates no project, bucket, key or SA (run infra/setup.sh first).
#
#   * upload-api: create-only + read under media/ (uploads, their session link) and media_tokens/
#     (one-time QR tokens, stored as SHA-256 only); it cannot list, overwrite or delete
#   * pipeline:   read + list media/ (photos into the session analysis)
#   * review-web: read media/ (the teacher sees the photo / video in review)
#   * bucket CORS: browsers may PUT to signed URLs from the upload-api origins (both run.app URLs);
#     other CORS rules (for example the iPhone web recorder) are kept
#   * retention: media/ and media_tokens/ deleted after $AUDIO_RETENTION_DAYS days (default 7;
#     other rules kept). After a demo, infra/delete_demo_media.sh deletes them at once.
#
# Called from infra/setup.sh. Safe to re-run after upload-api and review are deployed
# (CORS picks up both Cloud Run URLs; review-web is granted once that SA exists).
# Without this, POST /v1/media/token returns 500 (upload-api cannot write media_tokens/).
# upload-api no longer needs branding/ access (the recorder school-name fetch was removed in 0.8.2).
#
#   ./infra/setup_media.sh           set up
#   ./infra/setup_media.sh --check   read-only
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
ORIGIN="$(gcloud run services describe "$UPLOAD_SERVICE" --region="$REGION" --format='value(status.url)' 2>/dev/null || true)"
# Cloud Run serves the same service at two URLs; the firmware (and so the QR link) uses the deterministic one
ORIGIN2="https://${UPLOAD_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
lst() { printf 'resource.type == "storage.googleapis.com/Bucket" && api.getAttribute("storage.googleapis.com/objectListPrefix", "").startsWith("%s")' "$1"; }

if [[ "$CHECK" == 1 ]]; then
  say "Teacher Notes media setup in $PROJECT_ID (read-only check, gs://$BUCKET)"
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json)"
  missing=0
  for t in upload-create-media upload-read-media pipeline-read-media pipeline-list-media; do
    if grep -q "\"$t\"" <<<"$pol"; then echo "  $t: yes"; else echo "  $t: NO"; missing=1; fi
  done
  REVIEW_SA="$REVIEW_SA" python3 -c 'import json,os,sys; p=json.load(sys.stdin); m="serviceAccount:"+os.environ["REVIEW_SA"]
ok=any(m in b.get("members",[]) and b["role"]=="roles/storage.objectViewer" and "/objects/media/" in b.get("condition",{}).get("expression","") for b in p.get("bindings") or [])
print("  review-read-media:", "yes" if ok else "NO")
sys.exit(0 if ok else 1)' <<<"$pol" || missing=1
  LC="$(mktemp)"
  gcloud storage buckets describe "gs://$BUCKET" --format='json(cors_config,lifecycle_config)' >"$LC"
  python3 - "$LC" "$AUDIO_RETENTION_DAYS" "$ORIGIN" "$ORIGIN2" <<'PY' || missing=1
import json, sys
path, days = sys.argv[1], int(sys.argv[2])
origins = [o for o in sys.argv[3:] if o.startswith("https://")]
doc = json.load(open(path)) or {}
rules = ((doc.get("lifecycle_config") or {}).get("rule")) or []
want = {"media/", "media_tokens/"}

def covers(rule, prefixes):
    c = rule.get("condition") or {}
    return ((rule.get("action") or {}).get("type") == "Delete"
            and c.get("age") == days
            and prefixes <= set(c.get("matchesPrefix") or []))

ok_life = any(covers(r, want) for r in rules) or all(
    any(covers(r, {p}) for r in rules) for p in want)
print("  lifecycle media/ + media_tokens/ %s-day delete: %s" % (days, "yes" if ok_life else "NO"))
have = set()
for rule in (doc.get("cors_config") or []):
    if "PUT" in (rule.get("method") or []):
        have.update(rule.get("origin") or [])
missing_origins = [o for o in origins if o not in have]
if origins:
    print("  CORS PUT from upload-api: %s" % ("yes" if not missing_origins else "NO (missing %s)" % ", ".join(missing_origins)))
else:
    print("  CORS PUT from upload-api: skipped (upload-api not deployed; no origins to check)")
sys.exit(0 if ok_life and not missing_origins else 1)
PY
  rm -f "$LC"
  if [[ "$missing" != 0 ]]; then
    echo "Teacher Notes media setup is incomplete (that is the 500 on /v1/media/token)." >&2
    exit 1
  fi
  echo "ok"
  exit 0
fi

bind() {  # member role expression title
  local pol
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json)"
  if TITLE="$4" python3 -c 'import json,os,sys; p=json.load(sys.stdin); t=os.environ["TITLE"]
sys.exit(0 if any((b.get("condition") or {}).get("title")==t for b in p.get("bindings") or []) else 1)' <<<"$pol"; then
    echo "  $4: already there"
    return 0
  fi
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$3" "$4" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$1" \
    --role="$2" --condition-from-file="$f" >/dev/null
  rm -f "$f"
  echo "  $4: added"
}

say "Bucket access"
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectCreator "$(obj media/) || $(obj media_tokens/)" upload-create-media
bind "serviceAccount:$UPLOAD_SA" roles/storage.objectViewer "$(obj media/) || $(obj media_tokens/)" upload-read-media
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectViewer "$(obj media/)" pipeline-read-media
bind "serviceAccount:$PIPELINE_SA" roles/storage.objectViewer "$(lst media/)" pipeline-list-media
if gcloud iam service-accounts describe "$REVIEW_SA" >/dev/null 2>&1; then
  # review-web already holds ~20 conditional viewer grants (GCS limit), so media/ is added by merging them
  python3 "$HERE/consolidate_review_iam.py" "$BUCKET" "serviceAccount:$REVIEW_SA" --add-read media/
else
  echo "  review-web SA $REVIEW_SA not created yet; re-run $0 after ./infra/setup_review.sh"
fi
echo "upload-api create/read media/ + media_tokens/; pipeline read/list media/; review-web read media/ (when that SA exists)"

say "CORS: PUT from upload-api (existing rules kept)"
origins=()
[[ "$ORIGIN" == https://* ]] && origins+=("$ORIGIN")
origins+=("$ORIGIN2")
if [[ "$ORIGIN" != https://* ]]; then
  echo "  upload-api not deployed yet; setting CORS for $ORIGIN2 only (re-run $0 after ./infra/deploy.sh to add the second URL)"
fi
CORS_IN="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(cors_config)' >"$CORS_IN"
python3 - "$CORS_IN" "${origins[@]}" <<'PY'
import json, sys
path, origins = sys.argv[1], sys.argv[2:]
doc = json.load(open(path)) or {}
rules = list(doc.get("cors_config") or [])
def is_upload_api_rule(rule):
    orgs = rule.get("origin") or []
    return bool(orgs) and all("upload-api" in o and "run.app" in o for o in orgs)
keep = [r for r in rules if not is_upload_api_rule(r)]
keep.append({"origin": sorted(set(origins)), "method": ["PUT"],
  "responseHeader": ["Content-Type", "x-goog-if-generation-match", "x-goog-content-length-range"],
  "maxAgeSeconds": 600})
json.dump(keep, open(path + ".out", "w"))
PY
gcloud storage buckets update "gs://$BUCKET" --cors-file="$CORS_IN.out" >/dev/null
rm -f "$CORS_IN" "$CORS_IN.out"
echo "  PUT from ${origins[*]}"

say "Retention: media/ and media_tokens/ deleted after ${AUDIO_RETENTION_DAYS} days (existing rules kept)"
LC="$(mktemp)"
STAMP="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
python3 - "$LC" "$STAMP" "$AUDIO_RETENTION_DAYS" <<'PY'
import json, sys
path, stamp, days = sys.argv[1], sys.argv[2], int(sys.argv[3])
cfg = (json.load(open(path)) or {}).get("lifecycle_config") or {}
rules = list(cfg.get("rule") or [])
want = ["media/", "media_tokens/"]
already = any((r.get("action") or {}).get("type") == "Delete"
              and (r.get("condition") or {}).get("age") == days
              and (r.get("condition") or {}).get("matchesPrefix") == want for r in rules)
if not already:
    rules.append({"action": {"type": "Delete"}, "condition": {"age": days, "matchesPrefix": want}})
json.dump({"rule": rules}, open(path, "w"))
open(stamp, "w").write("already" if already else "add")
PY
if [[ "$(cat "$STAMP")" == already ]]; then
  echo "lifecycle: rule already there, nothing changed"
else
  gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LC" >/dev/null
  echo "lifecycle updated"
fi
rm -f "$LC" "$STAMP"
echo "done"
