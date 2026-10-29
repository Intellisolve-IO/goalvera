#!/usr/bin/env bash
# tasks#22: create the recording key (asymmetric decrypt, HSM) and let only the
# pipeline service account use it. DRY RUN by default: prints the commands.
#   APPLY=1 infra/setup_rec_key.sh     (needs David's OK; creates billable KMS key ~ $2.50/mo HSM)
set -euo pipefail
cd -- "$(dirname -- "$0")"
. ./config.sh
run() { if [[ "${APPLY:-0}" == 1 ]]; then "$@"; else printf 'DRY RUN: %q ' "$@"; echo; fi; }
run gcloud kms keys create "$REC_KMS_KEY" --location "$REC_KMS_LOCATION" --keyring "$REC_KMS_KEYRING" \
  --purpose asymmetric-encryption --default-algorithm rsa-decrypt-oaep-3072-sha256 --protection-level hsm
run gcloud kms keys add-iam-policy-binding "$REC_KMS_KEY" --location "$REC_KMS_LOCATION" --keyring "$REC_KMS_KEYRING" \
  --member "serviceAccount:${PIPELINE_SA}" --role roles/cloudkms.cryptoKeyDecrypter
run gcloud run services update "$PIPELINE_SERVICE" --region "$REGION" --update-env-vars "REC_KMS_KEYRING_PATH=${REC_KMS_KEYRING_PATH}"
