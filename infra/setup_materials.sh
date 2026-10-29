#!/usr/bin/env bash
# Classroom materials from Teacher Notes (tasks#99) - one-time cloud setup for the review service.
# Idempotent; safe to re-run. Stays inside $PROJECT_ID; creates no project, no key, no OAuth client.
#
#   * bucket: review-web may read, list and create-only under materials/ (made materials, suggestions,
#     email attempts) and materials_settings/ (admin email on/off); materials/ is deleted after
#     $DERIVED_RETENTION_DAYS days like transcripts and drafts (lifecycle rule added, others kept)
#   * Gmail API enabled (the app sends with users.messages.send; nothing else)
#   * two Secret Manager secrets, empty until infra/mailer_consent.py fills them:
#       $MAILER_CLIENT_SECRET  the OAuth client (made once in the Cloud console, "Desktop app")
#       $MAILER_TOKEN_SECRET   the sender mailbox's refresh token (scope gmail.send only)
#     review-web may read these two secrets only.
#
#   ./infra/setup_materials.sh           set up
#   ./infra/setup_materials.sh --check   read-only: what is there (no changes)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "usage: $0 [--check]" >&2; exit 2 ;; esac
MEMBER="serviceAccount:$REVIEW_SA"

has_versions() { [[ -n "$(gcloud secrets versions list "$1" --filter=state=enabled --limit=1 --format='value(name)' 2>/dev/null)" ]]; }

if [[ "$CHECK" == 1 ]]; then
  say "Classroom materials setup in $PROJECT_ID (read-only check)"
  pol="$(gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null || true)"
  grep -q '"review-create-materials"' <<<"$pol" && echo "  bucket access for materials/: yes" || echo "  bucket access for materials/: NO"
  gcloud storage buckets describe "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"materials/"' \
    && echo "  materials/ retention: ${DERIVED_RETENTION_DAYS} days" || echo "  materials/ retention: NOT SET"
  gcloud services list --enabled --format='value(config.name)' 2>/dev/null | grep -qx gmail.googleapis.com \
    && echo "  Gmail API: enabled" || echo "  Gmail API: NOT enabled"
  for s in "$MAILER_CLIENT_SECRET" "$MAILER_TOKEN_SECRET"; do
    if gcloud secrets describe "$s" >/dev/null 2>&1; then
      has_versions "$s" && echo "  secret $s: filled" || echo "  secret $s: empty (run infra/mailer_consent.py)"
    else echo "  secret $s: MISSING"; fi
  done
  exit 0
fi

say "Bucket access for materials/ and materials_settings/ ($REVIEW_SA)"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
lst() { printf 'resource.type == "storage.googleapis.com/Bucket" && api.getAttribute("storage.googleapis.com/objectListPrefix", "").startsWith("%s")' "$1"; }
bind() {  # role expression title
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
bind roles/storage.objectViewer "$(obj materials/)" review-read-materials
bind roles/storage.objectViewer "$(lst materials/)" review-list-materials
bind roles/storage.objectCreator "$(obj materials/)" review-create-materials
bind roles/storage.objectViewer "$(obj materials_settings/)" review-read-materials-settings
bind roles/storage.objectViewer "$(lst materials_settings/)" review-list-materials-settings
bind roles/storage.objectCreator "$(obj materials_settings/)" review-create-materials-settings
echo "review-web: read / list / create-only under materials/ and materials_settings/"

say "Retention: materials/ deleted after ${DERIVED_RETENTION_DAYS} days (existing rules kept)"
LC="$(mktemp)"
gcloud storage buckets describe "gs://$BUCKET" --format='json(lifecycle_config)' >"$LC"
python3 - "$LC" "$DERIVED_RETENTION_DAYS" <<'PY'
import json, sys
path, days = sys.argv[1], int(sys.argv[2])
cfg = (json.load(open(path)) or {}).get("lifecycle_config") or {}
rules = cfg.get("rule") or []
done = False
for r in rules:
    cond = r.get("condition") or {}
    pre = cond.get("matchesPrefix") or []
    if r.get("action", {}).get("type") == "Delete" and cond.get("age") == days and "drafts/" in pre:
        if "materials/" not in pre:
            pre.append("materials/")
        done = True
if not done:
    rules.append({"action": {"type": "Delete"}, "condition": {"age": days, "matchesPrefix": ["materials/"]}})
json.dump({"rule": rules}, open(path, "w"))
PY
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LC" >/dev/null
rm -f "$LC"
echo "lifecycle updated"

say "Gmail API"
gcloud services enable gmail.googleapis.com
echo "gmail.googleapis.com enabled (the app only calls users.messages.send)"

say "Secrets $MAILER_CLIENT_SECRET, $MAILER_TOKEN_SECRET (empty until infra/mailer_consent.py)"
for s in "$MAILER_CLIENT_SECRET" "$MAILER_TOKEN_SECRET"; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || \
    gcloud secrets create "$s" --replication-policy=automatic --labels=app=goalvera,use=materials-email >/dev/null
  gcloud secrets add-iam-policy-binding "$s" --member="$MEMBER" --role=roles/secretmanager.secretAccessor \
    --condition=None >/dev/null
  echo "  $s: exists; $REVIEW_SA may read it"
done

say "Next"
cat <<TXT
  1. Cloud console > Google Auth Platform ($PROJECT_ID): Branding, Audience = Internal, Data access:
     scope https://www.googleapis.com/auth/gmail.send; Clients > Create client > Desktop app.
     Download the client JSON.
  2. In David's 1:1 chat: python3 infra/mailer_consent.py --client-json <file>   (sign in as
     $MAILER_SENDER_MAILBOX, approve "Send email on your behalf"). The script stores both secrets.
TXT
