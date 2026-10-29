#!/usr/bin/env bash
# Sign in with Google for the review site (tasks#108) - READ-ONLY status check. Changes nothing.
#
# The app side is in main (review_api/auth.py, AuthGate.jsx): the button shows only when the
# server's IDP_PROVIDERS (infra/config.sh) lists "google.com", and the server refuses tokens from
# any provider not listed. Ops' checks run on every request for every provider: verified email,
# exact (case-insensitive) match with a staff/allowlist address, access not removed.
#
# Switching it on needs two console clicks by a project owner (no API creates the OAuth web client):
#   1. Firebase console -> project example-project -> Build -> Authentication ->
#      Sign-in method -> Add new provider -> Google -> Enable; Project support email:
#      admin@example.com -> Save. (This creates the "Web client (auto created by Google
#      Service)" OAuth client with the right redirect URI and fills in its ID and secret.)
#   2. Then: set IDP_PROVIDERS="password,google.com" in infra/config.sh (PR) and ./infra/deploy_review.sh.
# Both done Oct 11 (David enabled the provider; PR #45).
# New Google users: self sign-up stays OFF in Identity Platform (client.permissions.disabledUserSignup;
# David, Oct 11), so a Google account with no Identity Platform account is refused before it reaches
# the app (auth/admin-restricted-operation). An admin creates the account first:
# ./infra/review_allowlist.py add <email> or ./infra/signin_accounts.py create <email> (email only,
# no password); Sign in with Google then attaches to it.
#
#   ./infra/setup_google_signin.sh --check
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
case "${1:-}" in --check) ;; *) echo "usage: $0 --check   (read-only; see the comments for the console steps)" >&2; exit 2 ;; esac
say() { printf '\n== %s\n' "$*"; }
TOKEN="$(gcloud auth print-access-token)"
api() { curl -sS -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT_ID" "$1"; }
IDT="https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT_ID"

say "Sign in with Google in $PROJECT_ID (read-only check)"
api "$IDT/defaultSupportedIdpConfigs/google.com" | python3 -c '
import json, sys
d = json.load(sys.stdin)
on = bool(d.get("enabled")) and "error" not in d
print("  Google provider in Identity Platform:", "enabled" if on else "NOT enabled (console step 1)")
' 
api "$IDT/config" | python3 -c '
import json, sys
d = json.load(sys.stdin); want = sys.argv[1]
doms = d.get("authorizedDomains") or []
print("  authorized domain", want + ":", "yes" if want in doms else "NO")
print("  auth handler domain", sys.argv[2] + ":", "yes" if sys.argv[2] in doms else "NO")
p = (d.get("client") or {}).get("permissions") or {}
print("  self sign-up:", "off (Google users need an existing account)" if p.get("disabledUserSignup") else "on")
' "$REVIEW_DOMAIN" "${PROJECT_ID}.firebaseapp.com"
live="$(gcloud run services describe "$REVIEW_SERVICE" --region "$REGION" --format=json 2>/dev/null | python3 -c '
import json, sys
c = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]
print(next((e.get("value") for e in c.get("env", []) if e["name"] == "IDP_PROVIDERS"), "password (not set)"))' || echo "?")"
echo "  live IDP_PROVIDERS on $REVIEW_SERVICE: $live    (config.sh: $IDP_PROVIDERS)"
