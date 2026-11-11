#!/usr/bin/env bash
# My profile + admin role / email changes - one-time cloud setup for the review service.
# Idempotent; safe to re-run. review-web may read, list and create-only under people/ in the
# bucket (same pattern as school_setup/ and materials/: never overwrite or delete; every change is a
# new record and the highest number wins). The allowlist file (config/) stays read-only; admins' role / email
# changes live in people/ on top of it (review_api/people.py). Without it profiles show but saving fails.
#
#   ./infra/setup_people.sh           set up
#   ./infra/setup_people.sh --check   read-only: is it there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

if [[ "$CHECK" == 1 ]]; then
  say "My profile setup in $PROJECT_ID (read-only check)"
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-people"'; then
    echo "  bucket access for people/: yes"
  else
    echo "  bucket access for people/: NO (profiles show; saving fails)"
  fi
  exit 0
fi

say "Bucket access for people/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"people/\")"
bind roles/storage.objectViewer "$(obj people/)" review-read-people
bind roles/storage.objectViewer "$LIST" review-list-people
bind roles/storage.objectCreator "$(obj people/)" review-create-people
echo "review-web: read / list / create-only under people/"
