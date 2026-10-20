#!/usr/bin/env bash
# Admin activity journal (): one-time, idempotent setup. NOT run yet - run it
# after `gcloud auth login` (the box's sign-in had expired when this was written).
#
#   $JOURNAL_DATASET            this school's own dataset, us-east1, encrypted with the
#                               recordings key (CMEK), no table/partition expiration: kept
#                               while the school is a customer; at contract end the school
#                               gets a hand-over export, then the dataset is deleted.
#   $JOURNAL_DATASET.activity   day partitions on occurred_at, clustered by action,
#                               actor_id, student_id. Internal ids only (review_api/journal.py).
#   $JOURNAL_REPORTS_DATASET    one authorized view (activity) over the table; reports read here.
#   review-web SA               custom role journalAppender = tables.updateData + tables.get,
#                               on the activity TABLE only: it can add rows, never read, change
#                               or delete them, and has no BigQuery job rights anywhere.
#   journal-reader SA           bigquery.jobUser (project) + READER on the reports dataset only.
#                               review-web may mint its short-lived tokens for the admin pages.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
source "$HERE/config.sh"
say() { printf '\n== %s\n' "$*"; }
retry() {  # new roles / service accounts take a little while to be usable in IAM bindings
  local i
  for i in 1 2 3 4 5 6 7 8; do "$@" && return 0; echo "  (not ready yet, retry $i in 10s)" >&2; sleep 10; done
  "$@"
}
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DS="$PROJECT_ID:$JOURNAL_DATASET"
RDS="$PROJECT_ID:$JOURNAL_REPORTS_DATASET"
TBL="$DS.$JOURNAL_TABLE"
VIEW="$RDS.$JOURNAL_TABLE"

check_review_sa() {  # fails if the app account could reach the journal through a project role
  say "Check: $REVIEW_SA has no BigQuery role at project level (no jobs.create, no reads)"
  local roles
  roles="$(gcloud projects get-iam-policy "$PROJECT_ID" --flatten='bindings[].members' \
    --filter="bindings.members:serviceAccount:$REVIEW_SA" --format='value(bindings.role)')" \
    || { echo "FAIL: could not read the project IAM policy" >&2; exit 1; }
  BAD="$(grep -E '^roles/(bigquery\.|owner$|editor$|viewer$)' <<<"$roles" || true)"
  if [[ -n "$BAD" ]]; then
    echo "FAIL: $REVIEW_SA has project roles that reach the journal: $BAD" >&2
    exit 1
  fi
  echo "ok"
}

say "Pre-check (before creating anything)"
check_review_sa

say "APIs"
gcloud services enable bigquery.googleapis.com iamcredentials.googleapis.com --project="$PROJECT_ID"

say "CMEK: BigQuery may use $KMS_KEY"
BQ_SA="$(bq show --encryption_service_account --project_id="$PROJECT_ID" --format=json | python3 -c 'import json,sys; print(json.load(sys.stdin)["ServiceAccountID"])')"
gcloud kms keys add-iam-policy-binding "$KEY" --keyring="$KEYRING" --location="$REGION" \
  --member="serviceAccount:$BQ_SA" --role=roles/cloudkms.cryptoKeyEncrypterDecrypter --quiet >/dev/null

say "Dataset $DS ($REGION)"
if ! bq show --format=none "$DS" >/dev/null 2>&1; then
  bq --location="$REGION" mk --dataset --default_kms_key="$KMS_KEY" \
    --label=school:default --label=data:activity-journal --label=retention:contract-term \
    --description="Goalvera admin activity journal. Internal ids only. Add-only for the app." "$DS"
fi

say "Table $TBL"
PYTHONPATH="$ROOT/app/backend-pipeline" python3 -c \
  'import json; from review_api.journal import SCHEMA; print(json.dumps(SCHEMA))' > "$TMP/schema.json"
if ! bq show --format=none "$TBL" >/dev/null 2>&1; then
  bq mk --table --time_partitioning_field=occurred_at --time_partitioning_type=DAY \
    --require_partition_filter=true --clustering_fields=action,actor_id,student_id \
    --destination_kms_key="$KMS_KEY" \
    --description="One row per view/edit/approval/download/refusal. Never updated or deleted by the app." \
    "$TBL" "$TMP/schema.json"
fi

say "Reports dataset $RDS + view"
if ! bq show --format=none "$RDS" >/dev/null 2>&1; then
  bq --location="$REGION" mk --dataset --default_kms_key="$KMS_KEY" --label=school:default \
    --description="Read side of the activity journal: authorized view only." "$RDS"
fi
COLS="$(python3 -c 'import json,sys; print(", ".join(c["name"] for c in json.load(open(sys.argv[1]))))' "$TMP/schema.json")"
if ! bq show --format=none "$VIEW" >/dev/null 2>&1; then
  bq mk --use_legacy_sql=false --description="Journal rows for admin reports (dedup happens in the report query)." \
    --view="SELECT $COLS FROM \`$PROJECT_ID.$JOURNAL_DATASET.$JOURNAL_TABLE\`" "$VIEW"
fi

# dataset access entries: add one if missing (bq has no "add access entry" verb)
add_access() {  # dataset  json-entry
  bq show --format=prettyjson "$1" > "$TMP/ds.json"
  if python3 - "$TMP/ds.json" "$2" <<'PY'
import json, sys
ds, entry = json.load(open(sys.argv[1])), json.loads(sys.argv[2])
acc = ds.setdefault("access", [])
if entry in acc:
    sys.exit(1)
acc.append(entry)
json.dump({"access": acc}, open(sys.argv[1], "w"))
PY
  then bq update --source "$TMP/ds.json" "$1" >/dev/null; echo "  access added on $1"; else echo "  access already on $1"; fi
}

say "Authorize the view on $DS"
add_access "$DS" "{\"view\": {\"projectId\": \"$PROJECT_ID\", \"datasetId\": \"$JOURNAL_REPORTS_DATASET\", \"tableId\": \"$JOURNAL_TABLE\"}}"

say "Add-only role for $REVIEW_SA (table only)"
if ! gcloud iam roles describe "$JOURNAL_APPEND_ROLE" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam roles create "$JOURNAL_APPEND_ROLE" --project="$PROJECT_ID" --title="Journal appender" \
    --description="Stream rows into the activity journal; nothing else ()." \
    --permissions=bigquery.tables.updateData,bigquery.tables.get --stage=GA --quiet
fi
retry bq add-iam-policy-binding --member="serviceAccount:$REVIEW_SA" \
  --role="projects/$PROJECT_ID/roles/$JOURNAL_APPEND_ROLE" "$TBL" >/dev/null

say "Reader account $JOURNAL_READER_SA"
gcloud iam service-accounts describe "$JOURNAL_READER_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$JOURNAL_READER_SA_NAME" \
    --display-name="Goalvera journal reports (read the reports view only)"
retry gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$JOURNAL_READER_SA" \
  --role=roles/bigquery.jobUser --condition=None --quiet >/dev/null
add_access "$RDS" "{\"role\": \"READER\", \"userByEmail\": \"$JOURNAL_READER_SA\"}"
retry gcloud iam service-accounts add-iam-policy-binding "$JOURNAL_READER_SA" \
  --member="serviceAccount:$REVIEW_SA" --role=roles/iam.serviceAccountTokenCreator --quiet >/dev/null

check_review_sa
say "Access entries on $DS (expect: project roles + the reports view)"
bq show --format=prettyjson "$DS" | python3 -c 'import json,sys; [print("  ", a) for a in json.load(sys.stdin)["access"]]'

cat <<MSG

Journal ready. Next:
  ./infra/journal_backfill.py            # dry run: how many audit records would be copied
  ./infra/journal_backfill.py --yes      # copy them (as you)
  ./infra/deploy_review.sh               # sees the table and sets JOURNAL_MODE=bigquery
MSG
