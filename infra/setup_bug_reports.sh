#!/usr/bin/env bash
# Bug markers (tasks#94) - one-time cloud setup for the review service. Idempotent; safe to re-run.
#   * review-web: read, list and create-only under bug_reports/ in the bucket (same pattern as
#     planner/: never overwrite or delete; a report is pending until its .filed.json exists)
#   * Secret Manager secret $GITHUB_ISSUES_SECRET (github-issues-token): created EMPTY if missing
#     (user-managed replication in $REGION, label app=goalvera), and review-web may read that
#     one secret (secret-level binding, nothing project-wide). The value - David's fine-grained
#     GitHub token - is added by David as a new version (docs: app/review-web/README.md, "Bug
#     markers"). This script never adds, reads or prints a secret value.
# Until the secret has a version, Bug reports are saved as pending and filed automatically once
# it does (next submission or next instance start; no redeploy needed).
#
#   ./infra/setup_bug_reports.sh           set up
#   ./infra/setup_bug_reports.sh --check   read-only: what is there (no changes, no secret values)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

status() {
  say "Bug reports setup in $PROJECT_ID (read-only check)"
  if gcloud secrets describe "$GITHUB_ISSUES_SECRET" --format='value(name)' >/dev/null 2>&1; then
    n="$(gcloud secrets versions list "$GITHUB_ISSUES_SECRET" --filter='state:ENABLED' --format='value(name)' 2>/dev/null | wc -l | tr -d ' ')"
    echo "  secret $GITHUB_ISSUES_SECRET: exists, enabled versions: $n$([[ "$n" == 0 ]] && echo '  (no token yet: reports stay pending)')"
    if gcloud secrets get-iam-policy "$GITHUB_ISSUES_SECRET" --format=json 2>/dev/null | grep -q "$MEMBER"; then
      echo "  review-web can read it: yes"
    else
      echo "  review-web can read it: NO"
    fi
  else
    echo "  secret $GITHUB_ISSUES_SECRET: does not exist (reports stay pending)"
  fi
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-bug-reports"'; then
    echo "  bucket access for bug_reports/: yes"
  else
    echo "  bucket access for bug_reports/: NO (Bug reports can't be saved)"
  fi
}

if [[ "$CHECK" == 1 ]]; then status; exit 0; fi

say "Bucket access for bug_reports/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
BUG_LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"bug_reports/\")"
bind roles/storage.objectViewer "$(obj bug_reports/)" review-read-bug-reports
bind roles/storage.objectViewer "$BUG_LIST" review-list-bug-reports
bind roles/storage.objectCreator "$(obj bug_reports/)" review-create-bug-reports
echo "review-web: read / list / create-only under bug_reports/"

say "Secret $GITHUB_ISSUES_SECRET (no value; David adds the token as a version)"
if ! gcloud secrets describe "$GITHUB_ISSUES_SECRET" >/dev/null 2>&1; then
  gcloud secrets create "$GITHUB_ISSUES_SECRET" --replication-policy=user-managed --locations="$REGION" \
    --labels=app=goalvera >/dev/null
  echo "created (empty)"
else
  echo "exists - left as is"
fi
gcloud secrets add-iam-policy-binding "$GITHUB_ISSUES_SECRET" --member="$MEMBER" \
  --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
echo "review-web: secretAccessor on $GITHUB_ISSUES_SECRET only"
status
