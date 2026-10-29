#!/usr/bin/env bash
# Hosted review screen - one-time cloud setup (idempotent; safe to re-run).
#   * APIs: Identity Platform (identitytoolkit), API Keys
#   * Identity Platform: initialized; email + password sign-in with self sign-up;
#     password policy (10+ chars); email-enumeration protection; authorized domains
#   * a browser API key limited to Identity Platform + token refresh, and to our sites
#   * that key's value in Secret Manager ($REVIEW_API_KEY_SECRET, label app=goalvera);
#     review-web may read that one secret (Cloud Run injects it as IDP_API_KEY), so
#     deploy_review.sh never lists or reads API keys
#   * service account review-web with prefix-scoped bucket access:
#       read   drafts/ transcripts/ approvals/ audit/ config/demo_roster.json config/review_allowlist.json
#       list   only with prefix drafts/ or audit/
#       create approvals/ audit/   (no overwrite, no delete)
#   * config/review_allowlist.json seeded from infra/review_allowlist.json if missing
# Cost: Identity Platform email sign-in is free up to 50,000 monthly active users;
# the API key and service account are free.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
TOKEN="$(gcloud auth print-access-token)"
api() {  # method url [json]
  curl -fsS -X "$1" -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT_ID" \
    -H "Content-Type: application/json" "$2" ${3:+--data "$3"}
}

say "APIs"
gcloud services enable identitytoolkit.googleapis.com apikeys.googleapis.com run.googleapis.com secretmanager.googleapis.com

say "Identity Platform"
if ! api GET "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT_ID/config" >/dev/null 2>&1; then
  api POST "https://identitytoolkit.googleapis.com/v2/projects/$PROJECT_ID/identityPlatform:initializeAuth" '{}' >/dev/null
fi
RUN_HOST="$(gcloud run services describe "$REVIEW_SERVICE" --region="$REGION" --format='value(status.url)' 2>/dev/null | sed 's#https://##' || true)"
DOMAINS="\"localhost\",\"${PROJECT_ID}.firebaseapp.com\",\"${REVIEW_DOMAIN}\""
[[ -n "$RUN_HOST" ]] && DOMAINS+=",\"${RUN_HOST}\""
# the deterministic URL (review-<project number>.<region>.run.app) is the one deploy_review.sh
# allows as an origin and the API key's referrer pattern matches, so authorize it as well
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DET_HOST="${REVIEW_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
[[ "$RUN_HOST" == "$DET_HOST" ]] || DOMAINS+=",\"${DET_HOST}\""
api PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT_ID/config?updateMask=signIn.email.enabled,signIn.email.passwordRequired,signIn.allowDuplicateEmails,client.permissions.disabledUserSignup,authorizedDomains,passwordPolicyConfig,emailPrivacyConfig.enableImprovedEmailPrivacy" "{
  \"signIn\": {\"email\": {\"enabled\": true, \"passwordRequired\": true}, \"allowDuplicateEmails\": false},
  \"client\": {\"permissions\": {\"disabledUserSignup\": true}},
  \"authorizedDomains\": [${DOMAINS}],
  \"passwordPolicyConfig\": {\"passwordPolicyEnforcementState\": \"ENFORCE\",
     \"passwordPolicyVersions\": [{\"customStrengthOptions\": {\"minPasswordLength\": 10}}]},
  \"emailPrivacyConfig\": {\"enableImprovedEmailPrivacy\": true}
}" >/dev/null
echo "email + password sign-in on; authorized domains: ${DOMAINS}"

say "Browser API key ($REVIEW_API_KEY_NAME)"
KEY_ID="$(gcloud services api-keys list --filter="displayName=$REVIEW_API_KEY_NAME" --format='value(uid)' | head -1)"
# exact hosts: API key referrer patterns only take "*" as a whole leading subdomain label,
# so "review-*.us-east1.run.app" never matches anything
# firebaseapp.com hosts the email-verification and password-reset pages, so it must be allowed too.
REFERRERS="https://${REVIEW_DOMAIN}/*,https://${DET_HOST}/*,https://${PROJECT_ID}.firebaseapp.com/*"
[[ -n "$RUN_HOST" && "$RUN_HOST" != "$DET_HOST" ]] && REFERRERS+=",https://${RUN_HOST}/*"
if [[ -z "$KEY_ID" ]]; then
  gcloud services api-keys create --display-name="$REVIEW_API_KEY_NAME" \
    --api-target=service=identitytoolkit.googleapis.com --api-target=service=securetoken.googleapis.com \
    --allowed-referrers="$REFERRERS" >/dev/null
  KEY_ID="$(gcloud services api-keys list --filter="displayName=$REVIEW_API_KEY_NAME" --format='value(uid)' | head -1)"
else
  gcloud services api-keys update "$KEY_ID" --allowed-referrers="$REFERRERS" >/dev/null
fi
echo "key id: $KEY_ID (browser key: sent to every visitor by design; limited to sign-in APIs and our sites)"

say "Service account $REVIEW_SA_NAME"
gcloud iam service-accounts describe "$REVIEW_SA" >/dev/null 2>&1 ||
  { gcloud iam service-accounts create "$REVIEW_SA_NAME" --display-name="Goalvera - hosted review screen"; sleep 5; }

say "API key -> Secret Manager ($REVIEW_API_KEY_SECRET)"
# Created once; the value goes straight from the API Keys API into the secret (never printed).
# If the key is ever rotated, add a new version: get-key-string ... | gcloud secrets versions add.
if ! gcloud secrets describe "$REVIEW_API_KEY_SECRET" >/dev/null 2>&1; then
  gcloud secrets create "$REVIEW_API_KEY_SECRET" --replication-policy=user-managed --locations="$REGION" \
    --labels=app=goalvera >/dev/null
fi
if [[ -z "$(gcloud secrets versions list "$REVIEW_API_KEY_SECRET" --filter='state:ENABLED' --format='value(name)' --limit=1 2>/dev/null)" ]]; then
  gcloud services api-keys get-key-string "$KEY_ID" --format='value(keyString)' | tr -d '\n' |
    gcloud secrets versions add "$REVIEW_API_KEY_SECRET" --data-file=- >/dev/null
  echo "secret version added"
else
  echo "secret has a version - left as is"
fi
# review-web reads this one secret only (secret-level binding, nothing project-wide)
gcloud secrets add-iam-policy-binding "$REVIEW_API_KEY_SECRET" --member="serviceAccount:$REVIEW_SA" \
  --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
echo "review-web: secretAccessor on $REVIEW_API_KEY_SECRET only"
obj() { printf 'resource.name.startsWith("projects/_/buckets/%s/objects/%s")' "$BUCKET" "$1"; }
READ="$(obj drafts/) || $(obj transcripts/) || $(obj approvals/) || $(obj audit/) || resource.name == \"projects/_/buckets/${BUCKET}/objects/config/demo_roster.json\" || resource.name == \"projects/_/buckets/${BUCKET}/objects/config/review_allowlist.json\""
LIST="resource.type == \"storage.googleapis.com/Bucket\" && (api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"drafts/\") || api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"audit/\"))"
WRITE="$(obj approvals/) || $(obj audit/)"
bind() {  # role expression title  (condition via file: the expressions contain commas)
  local f; f="$(mktemp)"
  python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "title": sys.argv[2]}))' "$2" "$3" >"$f"
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$REVIEW_SA" \
    --role="$1" --condition-from-file="$f" >/dev/null
  rm -f "$f"
}
bind roles/storage.objectViewer "$READ" review-read
bind roles/storage.objectViewer "$LIST" review-list-drafts-audit
bind roles/storage.objectCreator "$WRITE" review-create-approvals-audit
# Weekly lesson planner (tasks#83): read, list and create-only under planner/ (never overwrite
# or delete; every save is a new version). Separate bindings so the ones above stay as they are.
PLANNER_LIST="resource.type == \"storage.googleapis.com/Bucket\" && api.getAttribute(\"storage.googleapis.com/objectListPrefix\", \"\").startsWith(\"planner/\")"
bind roles/storage.objectViewer "$(obj planner/)" review-read-planner
bind roles/storage.objectViewer "$PLANNER_LIST" review-list-planner
bind roles/storage.objectCreator "$(obj planner/)" review-create-planner
echo "bucket access: read/list/create as above; nothing else in the project"

say "Allowlist"
if ! gcloud storage objects describe "gs://$BUCKET/config/review_allowlist.json" >/dev/null 2>&1; then
  gcloud storage cp "$HERE/review_allowlist.json" "gs://$BUCKET/config/review_allowlist.json" \
    --if-generation-match=0 --content-type=application/json --quiet
  echo "seeded (David as admin; reviewer placeholder)"
else
  echo "exists - left as is (edit with ./infra/review_allowlist.py)"
fi
echo; echo "Next: ./infra/deploy_review.sh"
