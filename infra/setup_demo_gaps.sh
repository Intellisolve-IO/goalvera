#!/usr/bin/env bash
# Demo gaps - one-time cloud setup for the review service.
# Idempotent; safe to re-run. Grants review-web:
#   * read work/<rid>/manifest.json only (the pipeline's status + error code; no transcripts, no audio)
#     and list work/ and rec/ (file names and upload times): the Recordings "Uploading / processing" rows.
#     It still cannot read rec/ (the audio) or anything else under work/.
#   * read / list / create-only under parent_email/ (the demo redirect setting and the send log for the
#     weekly parent email; same pattern as materials/: never overwrite or delete, newest record wins).
# Students in this session are audit records (audit/), which review-web can already create.
# Retention: objects under parent_email/ are deleted after $PARENT_EMAIL_RETENTION_DAYS (365)
# days, by one bucket lifecycle rule MERGED into the existing ones (infra/lifecycle_merge.py: every
# existing rule is kept exactly as it is; nothing is added if the rule is already there).
# Without it: no processing rows (the list just shows finished recordings) and the parent email
# redirect stays at its default (on); sending works but its log record can't be written.
#
#   ./infra/setup_demo_gaps.sh           set up
#   ./infra/setup_demo_gaps.sh --check   read-only: is it there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

if [[ "$CHECK" == 1 ]]; then
  say "Demo gaps setup in $PROJECT_ID (read-only check)"
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null || true)"
  for t in review-read-demo-gaps review-list-demo-gaps review-create-parent-email; do
    if grep -q "\"$t\"" <<<"$pol"; then echo "  $t: yes"; else echo "  $t: NO"; fi
  done
  say "Bucket lifecycle rules (live)"
  LC="$(mktemp)"
  gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
  python3 "$HERE/lifecycle_merge.py" show "$LC" parent_email/ "$PARENT_EMAIL_RETENTION_DAYS" || true
  rm -f "$LC"
  exit 0
fi

say "Bucket access for processing rows and the parent email log ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
list() { printf 'resource.type == "storage.googleapis.com/Bucket" && api.getAttribute("storage.googleapis.com/objectListPrefix", "").startsWith("%s")' "$1"; }
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
# Two objectViewer bindings only: GCS allows at most 20 conditional bindings per role and member, and
# review-web already had 16 (Oct 10). Each joins its conditions with ||.
bind roles/storage.objectViewer "($(obj work/) && resource.name.endsWith(\"/manifest.json\")) || $(obj parent_email/)" review-read-demo-gaps
bind roles/storage.objectViewer "($(list work/)) || ($(list rec/)) || ($(list parent_email/))" review-list-demo-gaps
bind roles/storage.objectCreator "$(obj parent_email/)" review-create-parent-email
echo "review-web: read work/*/manifest.json; list work/ and rec/; read / list / create-only under parent_email/"

say "Retention: parent_email/ deleted after ${PARENT_EMAIL_RETENTION_DAYS} days (existing rules kept)"
LC="$(mktemp)"; NEW="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
python3 "$HERE/lifecycle_merge.py" merge "$LC" parent_email/ "$PARENT_EMAIL_RETENTION_DAYS" >"$NEW"
if python3 "$HERE/lifecycle_merge.py" show "$LC" parent_email/ "$PARENT_EMAIL_RETENTION_DAYS" >/dev/null; then
  echo "lifecycle: rule already there, nothing changed"
else
  gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$NEW" >/dev/null
  echo "lifecycle updated"
fi
rm -f "$LC" "$NEW"
