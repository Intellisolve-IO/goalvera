# Shared deployment settings. Every project-specific value comes from the environment
# or from infra/deploy.env (copy infra/deploy.env.example; that file is gitignored).
# Override any value by exporting it before running a script, e.g.
#   PROJECT_ID=my-school-project ./infra/setup.sh
HERE_CFG="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE_CFG/deploy.env" ] && . "$HERE_CFG/deploy.env"
: "${PROJECT_ID:?set PROJECT_ID (your Google Cloud project id), e.g. in infra/deploy.env}"
: "${PRODUCT_NAME:=Goalvera}"
: "${PRODUCT_HOMEPAGE:=https://goalvera.app}"

: "${REGION:=us-east1}"                 # bucket, KMS key, Cloud Run, Eventarc
: "${SPEECH_LOCATION:=us}"              # Chirp 3 runs only in the "us"/"eu" multi-regions
: "${GEMINI_LOCATION:=us}"              # Gemini 3.x Flash is served from the "us" multi-region
: "${GEMINI_MODEL:=gemini-3.8-flash}"
: "${KEYRING:=goalvera}"
: "${KEY:=recordings}"
: "${BUCKET:=${PROJECT_ID}-recordings}"
: "${AUDIO_RETENTION_DAYS:=7}"          # checklist row 33
: "${DERIVED_RETENTION_DAYS:=30}"       # transcripts + drafts; placeholder until the school's data agreement sets it
: "${LOG_RETENTION_DAYS:=7}"            # Cloud Logging _Default bucket
: "${UPLOAD_SA_NAME:=upload-api}"
: "${PIPELINE_SA_NAME:=pipeline}"
: "${TRIGGER_SA_NAME:=pipeline-trigger}"
: "${BUILD_SA_NAME:=builder}"
: "${UPLOAD_SERVICE:=upload-api}"
: "${PIPELINE_SERVICE:=pipeline}"

UPLOAD_SA="${UPLOAD_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PIPELINE_SA="${PIPELINE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TRIGGER_SA="${TRIGGER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="${BUILD_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KMS_KEY="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEY}"

# Never rely on (or change) the machine's default gcloud project.
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"

# Recorder OTA (firmware 0.8.0). These scripts do not create the bucket or the
# signing secret. The private key is Secret Manager secret $OTA_SIGNING_SECRET
# in $PROJECT_ID. The public half is firmware/recorder-idf/main/ota_pubkey.c.
# See firmware/recorder-idf/docs/ota.md. The bucket must not be publicly writable.
# OTA_BUCKET defaults to ${PROJECT_ID}-firmware.
: "${OTA_BUCKET:=${PROJECT_ID}-firmware}"
: "${OTA_SIGNING_SECRET:=recorder-ota-signing-key}"

# School shown on the review site. everyday-heroes is the fictional demo school
# (Everyday Heroes Academy, roster app/samples/demo_roster.json). demo-school is
# the placeholder profile (badge DS; roster app/samples/profiles/demo-school/).
: "${SCHOOL_PROFILE:=everyday-heroes}"

# Hosted review screen (Cloud Run + Identity Platform)
: "${REVIEW_SERVICE:=review}"
: "${REVIEW_SA_NAME:=review-web}"
: "${REVIEW_DOMAIN:?set REVIEW_DOMAIN (hostname of your review site)}"
: "${REVIEW_API_KEY_NAME:=review-web}"
# The browser API key's value lives in Secret Manager (created once by setup_review.sh as a
# project owner). Cloud Run injects it as IDP_API_KEY, so deploys never list or read API keys.
: "${REVIEW_API_KEY_SECRET:=review-web-api-key}"
# Bug markers: the review service files teachers' Bug reports as issues here, with a
# fine-grained GitHub token kept in this Secret Manager secret (read at run time by review-web;
# infra/setup_bug_reports.sh). Never a personal gh login.
: "${GITHUB_ISSUES_SECRET:=github-issues-token}"
: "${GITHUB_ISSUES_REPO:=}"                     # owner/repo; empty = bug reports off
REVIEW_SA="${REVIEW_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
# Weekly lesson planner AI drafts: live = Vertex Gemini in this project, same model and
# location as the pipeline (GEMINI_MODEL / GEMINI_LOCATION above); off = no AI buttons (offline =
# the local stand-in). live needs review-web to hold Vertex AI access: deploy_review.sh checks it
# and stops if it is missing; it never grants it.
: "${PLANNER_AI:=live}"

# Classroom materials from Teacher Notes.
# Email: Gmail API, single-mailbox OAuth consent (gmail.send only) from the sender mailbox below;
# From is MAILER_FROM (defaults to the sender mailbox). The OAuth client
# and the refresh token live in two Secret Manager secrets that only review-web can read
# (infra/setup_materials.sh creates them, infra/mailer_consent.py fills them). No domain-wide delegation.
: "${PROFILE_SELF_EDIT_ACCESS:=off}"           # My profile: on = people may change their own role / email
: "${MATERIALS_EMAIL_DEFAULT:=on}"             # demo school: on until an admin turns it off (logged)
: "${MATERIALS_MAIL_DOMAINS:=staff.example}"  # recipients must be in these domains
# Weekly parent email: demo redirect on by default (every parent
# email goes to the teacher's own inbox); an admin can change it in Settings (logged).
: "${PARENT_EMAIL_REDIRECT_DEFAULT:=on}"
# Parent email records (send log + redirect setting under parent_email/) are deleted after 12 months
# (bucket lifecycle rule, merged into the existing rules by infra/setup_demo_gaps.sh;.
: "${PARENT_EMAIL_RETENTION_DAYS:=365}"
# Sign-in providers for the review site: password + Google.
# Enable the Google provider in Identity Platform first (./infra/setup_google_signin.sh --check).
# The server refuses tokens from any provider not listed, so the button and the server always agree;
# set "password" to switch Google sign-in off again (redeploy).
: "${IDP_PROVIDERS:=password,google.com}"
: "${MAILER_SENDER_MAILBOX:=}"                 # e.g. notifications@your-domain
: "${MAILER_FROM:=notifications@staff.example}"  # From address; an alias of the sender mailbox
: "${PARENT_EMAIL_REDIRECT_DOMAIN:=staff.example}"  # demo redirect may only send to this domain
: "${MAILER_CLIENT_SECRET:=mailer-oauth-client}"
: "${MAILER_TOKEN_SECRET:=mailer-refresh-token}"
: "${MATERIALS_SVG_MODEL:=gemini-3.8-flash}"       # SVG drafts (text model; verified in this project Oct 10)
: "${MATERIALS_SVG_LOCATION:=global}"
: "${MATERIALS_IMAGE_MODEL:=gemini-3.1-flash-lite-image}"   # photo-style flashcards (image model, ~2/min quota; paced)
: "${MATERIALS_IMAGE_LOCATION:=global}"

# Admin activity journal: this school's own BigQuery dataset, add-only for the app
: "${JOURNAL_DATASET:=goalvera_journal}"
: "${JOURNAL_TABLE:=activity}"
: "${JOURNAL_REPORTS_DATASET:=goalvera_journal_reports}"   # authorized view only; reports read here
: "${JOURNAL_READER_SA_NAME:=journal-reader}"
: "${JOURNAL_APPEND_ROLE:=journalAppender}"
: "${JOURNAL_MAX_BYTES:=1073741824}"     # per report query (1 GiB)
JOURNAL_READER_SA="${JOURNAL_READER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

#. Recorders hold
# only the public half; the pipeline SA may decrypt. See infra/setup_rec_key.sh.
: "${REC_KMS_LOCATION:=${REGION}}"
: "${REC_KMS_KEYRING:=${KEYRING}}"
: "${REC_KMS_KEY:=recording-wrap}"   # "recordings" is the bucket CMEK key
REC_KMS_KEYRING_PATH="projects/${PROJECT_ID}/locations/${REC_KMS_LOCATION}/keyRings/${REC_KMS_KEYRING}"
