#!/usr/bin/env bash
# Deploy the hosted review screen to Cloud Run (service "review", us-east1) and map
# https://review.example.org to it (Cloud Run domain mapping: free,
# no load balancer). Run ./infra/setup_review.sh first.
#
# Access: the service accepts requests from anyone on the internet (a browser has to
# reach the sign-in page), but every /api call is refused unless it carries a valid
# Identity Platform token for a verified, allowlisted email (review_api/auth.py).
# Scales to zero (min instances 0): idle cost $0.
#
# Never lists or reads API keys: the browser key's value is the Secret Manager secret
# $REVIEW_API_KEY_SECRET, which Cloud Run injects as IDP_API_KEY at runtime (the
# frontend fetches it from /api/auth-config; the build never sees it).
# The admin activity journal () is always on: if the journal table can't be
# confirmed, the deploy stops instead of turning the journal off.
# Planner AI drafts (): PLANNER_AI=live (config.sh default) sets Vertex Gemini with the
# pipeline's model and location; if review-web has no Vertex AI access the deploy stops instead
# of shipping AI buttons that can't answer (PLANNER_AI=off ./infra/deploy_review.sh deploys without).
# The fictional roster is uploaded only when it differs from the bucket copy.
# Bug markers (): the service gets the NAME of the GitHub token's secret
# (GITHUB_ISSUES_SECRET) and reads it itself at run time; a missing secret or version never stops
# a deploy (reports stay pending). Setup and a read-only check: infra/setup_bug_reports.sh.
#
#   ./infra/deploy_review.sh            deploy
#   ./infra/deploy_review.sh --dry-run  plan only: no writes; shows which steps the active
#                                       gcloud account may run (IAM testIamPermissions)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

journal_check() {  # table metadata only (bigquery.tables.get); no data is read. Prints bq's error, if any.
  bq --project_id="$PROJECT_ID" show --format=none "$PROJECT_ID:$JOURNAL_DATASET.$JOURNAL_TABLE" 2>&1
}
JOURNAL_STOP="refusing to deploy: the journal table $PROJECT_ID:$JOURNAL_DATASET.$JOURNAL_TABLE could not be confirmed
(missing, or this account lacks bigquery.tables.get on it). The review service must keep JOURNAL_MODE=bigquery;
run ./infra/setup_journal.sh or deploy as an account that can see the table. Nothing was deployed."

case "$PLANNER_AI" in live|off|offline) ;; *) echo "PLANNER_AI must be live, off or offline (got '$PLANNER_AI')" >&2; exit 2 ;; esac
vertex_check() {  # does review-web hold Vertex AI access (aiplatform.endpoints.predict)? Reads the project IAM policy only.
  local roles r
  roles="$(gcloud projects get-iam-policy "$PROJECT_ID" --format=json 2>/dev/null | REVIEW_SA="$REVIEW_SA" python3 -c '
import json, os, sys
m = "serviceAccount:" + os.environ["REVIEW_SA"]
for b in json.load(sys.stdin).get("bindings", []):
    if m in b.get("members", []) and not b.get("condition"):
        print(b["role"])')" || return 1
  for r in $roles; do
    case "$r" in
      roles/aiplatform.user|roles/aiplatform.admin|roles/editor|roles/owner) return 0 ;;
      projects/*/roles/*) gcloud iam roles describe "$r" --format='value(includedPermissions)' 2>/dev/null |
                            tr ';,' '\n\n' | grep -qx 'aiplatform.endpoints.predict' && return 0 ;;
    esac
  done
  return 1
}
PLANNER_STOP="refusing to deploy with PLANNER_AI=live: $REVIEW_SA has no Vertex AI access
(aiplatform.endpoints.predict), so the planner's AI draft buttons could not answer. A project owner can grant it:
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$REVIEW_SA --role=roles/aiplatform.user --condition=None
or deploy without AI drafts: PLANNER_AI=off ./infra/deploy_review.sh. Nothing was deployed."
roster_same() {  # local fictional roster byte-identical to the bucket copy? (md5 from object metadata)
  local here there
  here="$(python3 -c 'import base64,hashlib,sys; print(base64.b64encode(hashlib.md5(open(sys.argv[1],"rb").read()).digest()).decode())' "$ROOT/app/samples/demo_roster.json")"
  there="$(gcloud storage objects describe "gs://$BUCKET/config/demo_roster.json" --format='value(md5_hash)' 2>/dev/null || true)"
  [[ -n "$there" && "$here" == "$there" ]]
}
review_env() {  # the full env list for --set-env-vars ("|"-separated); $1 = journal mode
  local e="APP_COMMIT=${COMMIT:-unknown}|BUCKET=$BUCKET|PROJECT_ID=$PROJECT_ID|IDP_AUTH_DOMAIN=${PROJECT_ID}.firebaseapp.com|REVIEW_ALLOWED_ORIGINS=https://${REVIEW_DOMAIN},${RUN_URL}|JOURNAL_MODE=$1|JOURNAL_DATASET=$JOURNAL_DATASET|JOURNAL_TABLE=$JOURNAL_TABLE|JOURNAL_REPORTS_DATASET=$JOURNAL_REPORTS_DATASET|JOURNAL_READER_SA=$JOURNAL_READER_SA|JOURNAL_LOCATION=$REGION|JOURNAL_MAX_BYTES=$JOURNAL_MAX_BYTES|SCHOOL_PROFILE=$SCHOOL_PROFILE"
  e+="|PLANNER_AI=$PLANNER_AI|GITHUB_ISSUES_SECRET=$GITHUB_ISSUES_SECRET|GITHUB_ISSUES_REPO=$GITHUB_ISSUES_REPO"
  [[ "$PLANNER_AI" == live ]] && e+="|VERTEX_AI=1|GOOGLE_CLOUD_PROJECT=$PROJECT_ID|GEMINI_LOCATION=$GEMINI_LOCATION|GEMINI_MODEL=$GEMINI_MODEL"
  # classroom materials (): same AI setting as the planner; email via the mailer secrets
  e+="|REVIEW_URL=https://${REVIEW_DOMAIN}|MATERIALS_EMAIL_DEFAULT=$MATERIALS_EMAIL_DEFAULT|MATERIALS_MAIL_DOMAINS=$MATERIALS_MAIL_DOMAINS|PARENT_EMAIL_REDIRECT_DEFAULT=$PARENT_EMAIL_REDIRECT_DEFAULT"
  e+="|PROFILE_SELF_EDIT_ACCESS=$PROFILE_SELF_EDIT_ACCESS"      # My profile: self-edit of role / email (default off)
  e+="|IDP_PROVIDERS=$IDP_PROVIDERS"      # sign-in providers (): password[,google.com]
  # Phone Record page. Set UPLOAD_PUBLIC_URL to the upload service origin before deploying
  # (https://upload-api-....run.app). Absent until then, so a review deploy does not change
  # the live env just because this script grew a new setting.
  [[ -n "${UPLOAD_PUBLIC_URL:-}" ]] && e+="|UPLOAD_PUBLIC_URL=${UPLOAD_PUBLIC_URL}"
  e+="|MAILER_CLIENT_SECRET=$MAILER_CLIENT_SECRET|MAILER_TOKEN_SECRET=$MAILER_TOKEN_SECRET|MAILER_FROM=$MAILER_FROM|PARENT_EMAIL_REDIRECT_DOMAIN=$PARENT_EMAIL_REDIRECT_DOMAIN|MATERIALS_SVG_MODEL=$MATERIALS_SVG_MODEL|MATERIALS_SVG_LOCATION=$MATERIALS_SVG_LOCATION|MATERIALS_IMAGE_MODEL=$MATERIALS_IMAGE_MODEL|MATERIALS_IMAGE_LOCATION=$MATERIALS_IMAGE_LOCATION"
  printf '%s' "$e"
}

if [[ "$DRY_RUN" == 1 ]]; then
  ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
  TOKEN="$(gcloud auth print-access-token)"
  has() {  # resource-url permission... -> prints the missing ones
    local want got
    want="$(printf '"%s",' "${@:2}")"
    got="$(curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      "$1:testIamPermissions" --data "{\"permissions\": [${want%,}]}" 2>/dev/null || true)"
    for p in "${@:2}"; do grep -q "\"$p\"" <<<"$got" || printf '%s ' "$p"; done
  }
  has_gcs() {  # bucket permission...
    local q got
    q="$(printf 'permissions=%s&' "${@:2}")"
    got="$(curl -sS -H "Authorization: Bearer $TOKEN" \
      "https://storage.googleapis.com/storage/v1/b/$1/iam/testPermissions?${q%&}" 2>/dev/null || true)"
    for p in "${@:2}"; do grep -q "\"$p\"" <<<"$got" || printf '%s ' "$p"; done
  }
  N_OK=0; N_NO=0
  step() {  # name missing-permissions
    if [[ -z "$2" ]]; then printf '  [can]    %s\n' "$1"; N_OK=$((N_OK + 1))
    else printf '  [cannot] %s\n           missing: %s\n' "$1" "$2"; N_NO=$((N_NO + 1)); fi
  }
  P="projects/$PROJECT_ID"
  SA_URL() { echo "https://iam.googleapis.com/v1/$P/serviceAccounts/$1"; }
  say "Plan for $ACCOUNT in $PROJECT_ID (dry run: nothing is changed)"
  if msg="$(journal_check)"; then step "journal table check (JOURNAL_MODE=bigquery)" ""
  else step "journal table check (JOURNAL_MODE=bigquery)" "bigquery.tables.get on $JOURNAL_DATASET.$JOURNAL_TABLE
           bq: $(tr -s '\n ' ' ' <<<"$msg" | cut -c1-240)"; fi
  if roster_same; then printf '  [info]   roster: gs://%s/config/demo_roster.json is byte-identical to app/samples; not re-uploaded\n' "$BUCKET"
  else step "roster copy -> gs://$BUCKET/config/demo_roster.json (differs: would be replaced)" "$(has_gcs "$BUCKET" storage.objects.create storage.objects.delete)"; fi
  if [[ "$PLANNER_AI" == live ]]; then
    if vertex_check; then step "planner AI live: $REVIEW_SA can call Vertex Gemini ($GEMINI_MODEL, $GEMINI_LOCATION)" ""
    else step "planner AI live: $REVIEW_SA can call Vertex Gemini ($GEMINI_MODEL, $GEMINI_LOCATION)" "aiplatform.endpoints.predict for $REVIEW_SA (the deploy would stop; see PLANNER_STOP)"; fi
  else printf '  [info]   planner AI: %s (no Vertex call from the review service)\n' "$PLANNER_AI"; fi
  # incident names () use the same AI setting; saved under incident_names/ (never blocks a deploy)
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-incident-names"'; then
    printf '  [info]   incident names: AI %s; bucket access for incident_names/: yes\n' "$PLANNER_AI"
  else printf '  [info]   incident names: bucket access for incident_names/ missing (run infra/setup_incident_names.sh); incidents show as "Incident N"\n'; fi
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-branding"'; then
    printf '  [info]   school name + logo: bucket access for branding/: yes\n'
  else printf '  [info]   school name + logo: bucket access for branding/ missing (run infra/setup_branding.sh); defaults show, saving fails\n'; fi
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-materials"'; then
    printf '  [info]   classroom materials: bucket access for materials/: yes\n'
  else printf '  [info]   classroom materials: bucket access for materials/ missing (run infra/setup_materials.sh); suggestions are not saved, making fails\n'; fi
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-school-setup"'; then
    printf '  [info]   domains + classes: bucket access for school_setup/: yes\n'
  else printf '  [info]   domains + classes: bucket access for school_setup/ missing (run infra/setup_school_setup.sh); the seed shows, saving fails\n'; fi
  if gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json 2>/dev/null | grep -q '"review-create-people"'; then
    printf '  [info]   my profile + access changes: bucket access for people/: yes (self-edit of role/email: %s)\n' "$PROFILE_SELF_EDIT_ACCESS"
  else printf '  [info]   my profile + access changes: bucket access for people/ missing (run infra/setup_people.sh); profiles show, saving fails\n'; fi
  step "project number (run.app URL)" "$(has "https://cloudresourcemanager.googleapis.com/v1/$P" resourcemanager.projects.get)"
  printf '  [info]   API key: no API Keys call; Cloud Run gives review-web secret %s as IDP_API_KEY\n' "$REVIEW_API_KEY_SECRET"
  step "upload source to gs://run-sources-$PROJECT_ID-$REGION" \
    "$(has_gcs "run-sources-$PROJECT_ID-$REGION" storage.objects.create storage.objects.get)"
  step "start Cloud Build" "$(has "https://cloudresourcemanager.googleapis.com/v1/$P" cloudbuild.builds.create)"
  step "build as $BUILD_SA" "$(has "$(SA_URL "$BUILD_SA")" iam.serviceAccounts.actAs)"
  step "run as $REVIEW_SA" "$(has "$(SA_URL "$REVIEW_SA")" iam.serviceAccounts.actAs)"
  step "deploy revision of $REVIEW_SERVICE" \
    "$(has "https://run.googleapis.com/v2/$P/locations/$REGION/services/$REVIEW_SERVICE" run.services.get run.services.update)"
  step "check domain mapping $REVIEW_DOMAIN" "$(has "https://cloudresourcemanager.googleapis.com/v1/$P" run.domainmappings.get)"
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' 2>/dev/null || true)"
  RUN_URL="https://${REVIEW_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
  say "Settings vs the live service (env + secrets; the image is always new)"
  gcloud run services describe "$REVIEW_SERVICE" --region="$REGION" --format=json 2>/dev/null |
    WANT="$(review_env bigquery)" SECRET="IDP_API_KEY=secret:${REVIEW_API_KEY_SECRET}:latest" python3 -c '
import json, os, sys
try:
    c = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]
except Exception:
    print("  (live service not readable: everything below is new)"); c = {}
have = {}
for e in c.get("env", []):
    ref = (e.get("valueFrom") or {}).get("secretKeyRef")
    have[e["name"]] = "secret:%s:%s" % (ref["name"], ref["key"]) if ref else e.get("value", "")
want = dict(kv.split("=", 1) for kv in os.environ["WANT"].split("|"))
k, v = os.environ["SECRET"].split("=", 1); want[k] = v
for n in sorted(set(have) | set(want)):
    if have.get(n) == want.get(n):
        continue
    print("  %s: %s -> %s" % (n, have.get(n, "(unset)"), want.get(n, "(removed)")))
same = [n for n in want if have.get(n) == want[n]]
print("  unchanged: %d (%s)" % (len(same), ", ".join(sorted(same))))
print("  image: " + c.get("image", "?") + " -> new build of this checkout")'
  echo; echo "$N_OK steps allowed, $N_NO not allowed for $ACCOUNT. Nothing was changed."
  exit 0
fi

# Deploys only from main (repo rule, Oct 9): the checkout must be origin/main exactly, clean.
# ALLOW_NON_MAIN=1 overrides for an emergency; say why in the PR/card. (DEPLOY_MAIN_REF: tests.)
if [[ "${ALLOW_NON_MAIN:-0}" != 1 ]]; then
  MAIN_REF="${DEPLOY_MAIN_REF:-origin/main}"
  [[ -z "${DEPLOY_MAIN_REF:-}" ]] && { git -C "$ROOT" fetch -q origin main 2>/dev/null || true; }
  if [[ "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" != "$(git -C "$ROOT" rev-parse --verify -q "$MAIN_REF" 2>/dev/null)" ]] \
     || { [[ -z "${DEPLOY_MAIN_REF:-}" ]] && ! git -C "$ROOT" diff --quiet HEAD -- app infra 2>/dev/null; }; then
    echo "refusing to deploy: this checkout is not a clean origin/main. Deploys only from main. Nothing was deployed." >&2
    exit 1
  fi
fi
COMMIT="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || true)"
[[ -n "$COMMIT" ]] && ! git -C "$ROOT" diff --quiet HEAD -- app infra 2>/dev/null && COMMIT+="-dirty"

# Journal (): always on. No silent fallback to off.
if ! msg="$(journal_check)"; then
  echo "$msg" >&2
  echo "$JOURNAL_STOP" >&2
  exit 1
fi
JOURNAL_MODE=bigquery
echo "journal: $JOURNAL_MODE ($JOURNAL_DATASET.$JOURNAL_TABLE found)"

# Planner AI (): live only when review-web can reach Vertex AI. No silent fallback.
if [[ "$PLANNER_AI" == live ]] && ! vertex_check; then
  echo "$PLANNER_STOP" >&2
  exit 1
fi
echo "planner AI: $PLANNER_AI$([[ "$PLANNER_AI" == live ]] && echo " (Vertex Gemini $GEMINI_MODEL, $GEMINI_LOCATION)")"

say "Roster (fictional) -> gs://$BUCKET/config/demo_roster.json"
if roster_same; then
  echo "byte-identical to the bucket copy - not re-uploaded"
else
  gcloud storage cp "$ROOT/app/samples/demo_roster.json" "gs://$BUCKET/config/demo_roster.json" --quiet
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUN_URL="https://${REVIEW_SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"


say "Build context"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/frontend-web" "$CTX/backend-pipeline" "$CTX/review-web"
( cd "$ROOT/app/frontend-web" && tar --exclude=node_modules --exclude=dist -cf - . ) | tar -xf - -C "$CTX/frontend-web"
( cd "$ROOT/app/backend-pipeline" && tar --exclude=__pycache__ --exclude=tests -cf - common gemini_analyzer pipeline_service review_api ) | tar -xf - -C "$CTX/backend-pipeline"
( cd "$ROOT/app" && tar --exclude='*.wav' -cf - samples ) | tar -xf - -C "$CTX"
cp "$ROOT/app/review-web/Dockerfile" "$ROOT/app/review-web/requirements.txt" "$CTX/review-web/"
# the commit this build came from, for teachers' Bug reports (; Cloud Run adds K_REVISION),
# also set as APP_COMMIT (shown on Settings) and as the revision label commit=<sha>
printf '{"commit": "%s"}\n' "$COMMIT" >"$CTX/backend-pipeline/review_api/build_info.json"
echo "build: commit ${COMMIT:-unknown}"
cp "$ROOT/app/review-web/Dockerfile" "$CTX/Dockerfile"
du -sh "$CTX" | cut -f1

say "Deploy $REVIEW_SERVICE"
gcloud run deploy "$REVIEW_SERVICE" --source="$CTX" --region="$REGION" \
  --service-account="$REVIEW_SA" --build-service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" \
  --no-invoker-iam-check --ingress=all \
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --concurrency=40 --timeout=120 \
  --set-secrets="IDP_API_KEY=${REVIEW_API_KEY_SECRET}:latest" \
  --set-env-vars="^|^$(review_env "$JOURNAL_MODE")" \
  --update-labels="commit=${COMMIT:-unknown}" \
  --quiet
URL="$(gcloud run services describe "$REVIEW_SERVICE" --region="$REGION" --format='value(status.url)')"
echo "service URL: $URL"

say "Custom domain $REVIEW_DOMAIN"
# Cloud Run Admin API v1 directly (the same call "gcloud beta run domain-mappings" makes),
# so the script doesn't need the gcloud beta component installed.
TOKEN="$(gcloud auth print-access-token)"
DM="https://${REGION}-run.googleapis.com/apis/domains.cloudrun.com/v1/namespaces/${PROJECT_ID}/domainmappings"
dm() { curl -fsS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }
if dm "$DM/$REVIEW_DOMAIN" >/dev/null 2>&1; then
  echo "mapping exists"
elif gcloud domains list-user-verified --format='value(id)' | grep -qx "staff.example"; then
  dm -X POST "$DM" --data "{\"apiVersion\": \"domains.cloudrun.com/v1\", \"kind\": \"DomainMapping\",
    \"metadata\": {\"name\": \"$REVIEW_DOMAIN\", \"namespace\": \"$PROJECT_ID\"},
    \"spec\": {\"routeName\": \"$REVIEW_SERVICE\"}}" >/dev/null
  echo "mapping created"; sleep 10
else
  echo "staff.example is not verified for $(gcloud config get-value account 2>/dev/null) in Search Console."
  echo "Verify it (gcloud domains verify staff.example), then re-run this script."
fi
dm "$DM/$REVIEW_DOMAIN" 2>/dev/null | python3 -c '
import json, sys
st = json.load(sys.stdin).get("status", {})
for r in st.get("resourceRecords", []):
    print("  record:", r.get("name", ""), r.get("type"), r.get("rrdata"))
for c in st.get("conditions", []):
    print("  " + c.get("type", "") + ":", c.get("status"), c.get("message", ""))
' || true
echo
echo "DNS (at your DNS provider): add"
echo "  review   CNAME   ghs.googlehosted.com."
echo "The HTTPS certificate is issued automatically once the record resolves (usually 15-60 min)."
