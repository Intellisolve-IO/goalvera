#!/usr/bin/env bash
# Write the recording public key from your Cloud KMS recording key.
# Public key only; the private key never leaves KMS. Keep it out of git and pass it
# to the build:  idf.py -D RECORDING_PUBKEY_PEM="$OUT" build
#   tools/fetch-rec-pubkey.sh [key_version] [out_path]   (default 1, local/recording_pubkey.pem)
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
. ../../infra/config.sh
VER="${1:-1}"
OUT="${2:-local/recording_pubkey.pem}"
mkdir -p "$(dirname "$OUT")"
gcloud kms keys versions get-public-key "$VER" --project "$PROJECT_ID" \
  --location "$REC_KMS_LOCATION" --keyring "$REC_KMS_KEYRING" --key "$REC_KMS_KEY" \
  --output-file "$OUT"
openssl pkey -pubin -in "$OUT" -noout -text | head -1
echo "Build with: idf.py -D RECORDING_PUBKEY_PEM=$PWD/$OUT build"
