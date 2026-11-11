#!/usr/bin/env bash
# School name + logo - one-time cloud setup for the review service.
# Idempotent; safe to re-run. review-web may read, list and create-only under branding/ in the
# bucket (same pattern as planner/, bug_reports/ and incident_names/: never overwrite or delete).
# Without it the top bar still shows the defaults (Demo School + DS badge) but admins can't save.
#
#   ./infra/setup_branding.sh           set up
#   ./infra/setup_branding.sh --check   read-only: is it there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

if [[ "$CHECK" == 1 ]]; then
  say "School branding setup in $PROJECT_ID (read-only check)"
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-branding"'; then
    echo "  bucket access for branding/: yes"
  else
    echo "  bucket access for branding/: NO (the defaults show; admins can't save a name or logo)"
  fi
  exit 0
fi

say "Bucket access for branding/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"branding/\")"
bind roles/storage.objectViewer "$(obj branding/)" review-read-branding
bind roles/storage.objectViewer "$LIST" review-list-branding
bind roles/storage.objectCreator "$(obj branding/)" review-create-branding
echo "review-web: read / list / create-only under branding/"
