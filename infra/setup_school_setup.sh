#!/usr/bin/env bash
# Domains + Classes tabs (David, Oct 10 2026; tasks#103) - one-time cloud setup for the review service.
# Idempotent; safe to re-run. review-web may read, list and create-only under school_setup/ in the
# bucket (same pattern as school_setup/ and materials/: never overwrite or delete; every admin change is a
# new record and the newest wins). Without it the tabs show the seed from app/samples/school-setup
# but saving a change fails.
#
#   ./infra/setup_school_setup.sh           set up
#   ./infra/setup_school_setup.sh --check   read-only: is it there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

if [[ "$CHECK" == 1 ]]; then
  say "Domains + Classes setup in $PROJECT_ID (read-only check)"
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-school-setup"'; then
    echo "  bucket access for school_setup/: yes"
  else
    echo "  bucket access for school_setup/: NO (the seed shows; saving a change fails)"
  fi
  exit 0
fi

say "Bucket access for school_setup/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"school_setup/\")"
bind roles/storage.objectViewer "$(obj school_setup/)" review-read-school-setup
bind roles/storage.objectViewer "$LIST" review-list-school-setup
bind roles/storage.objectCreator "$(obj school_setup/)" review-create-school-setup
echo "review-web: read / list / create-only under school_setup/"
