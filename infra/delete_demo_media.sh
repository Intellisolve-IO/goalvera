#!/usr/bin/env bash
# After the demo (tasks#112): delete every Teacher Notes photo / video and QR token in the DEMO bucket,
# and the demo recordings' drafts/transcripts if asked. Demo project only.
#
#   ./infra/delete_demo_media.sh            list what would be deleted (no changes)
#   ./infra/delete_demo_media.sh --delete   delete media/ and media_tokens/
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"
# PROJECT_ID and BUCKET come from the environment or infra/deploy.env.
# --delete removes Teacher Notes media in that bucket.
n="$(gcloud storage ls "gs://$BUCKET/media/**" "gs://$BUCKET/media_tokens/**" 2>/dev/null | wc -l | tr -d ' ')"
echo "gs://$BUCKET: $n object(s) under media/ and media_tokens/"
if [[ "${1:-}" != --delete ]]; then
  gcloud storage ls "gs://$BUCKET/media/**" 2>/dev/null | sed 's#.*/media/#  media/#' || true
  echo "dry run; re-run with --delete to delete them"; exit 0
fi
[[ "$n" == 0 ]] || gcloud storage rm "gs://$BUCKET/media/**" "gs://$BUCKET/media_tokens/**" --quiet 2>/dev/null || true
left="$(gcloud storage ls "gs://$BUCKET/media/**" "gs://$BUCKET/media_tokens/**" 2>/dev/null | wc -l | tr -d ' ')"
echo "deleted; $left left (soft delete is off on this bucket, so they are gone)"
echo "Sessions recorded for the demo: delete with infra/delete_session.py <recording_id> (see docs/deletion-runbook.md)."
