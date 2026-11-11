#!/usr/bin/env bash
# Incident names - one-time cloud setup for the review service.
# Idempotent; safe to re-run. review-web may read, list and create-only under incident_names/ in
# the bucket (same pattern as planner/ and bug_reports/: never overwrite or delete). Without it the
# review screen still works: incidents just show as "Incident N" (names aren't saved).
#
#   ./infra/setup_incident_names.sh           set up
#   ./infra/setup_incident_names.sh --check   read-only: is it there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

if [[ "$CHECK" == 1 ]]; then
  say "Incident names setup in $PROJECT_ID (read-only check)"
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-incident-names"'; then
    echo "  bucket access for incident_names/: yes"
  else
    echo "  bucket access for incident_names/: NO (names can't be saved; incidents show as \"Incident N\")"
  fi
  exit 0
fi

say "Bucket access for incident_names/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"incident_names/\")"
bind roles/storage.objectViewer "$(obj incident_names/)" review-read-incident-names
bind roles/storage.objectViewer "$LIST" review-list-incident-names
bind roles/storage.objectCreator "$(obj incident_names/)" review-create-incident-names
echo "review-web: read / list / create-only under incident_names/"
