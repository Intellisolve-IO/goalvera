# Demo backend infrastructure ()

Scripts that build the legacy school demo backend in Google Cloud with plain
`gcloud`. Everything is idempotent and parameterised (`infra/config.sh`), so
the same steps can be replayed in the school's own project at go-live:
`PROJECT_ID=<school-project> ./infra/setup.sh && ./infra/deploy.sh`.

(We used scripts rather than Terraform: no extra tooling to install on the
demo laptop, and each step reads like the runbook. `app/cloud-terraform/`
points here.)

## What gets built

```
 recorder / laptop ──HTTPS──▶ upload-api (Cloud Run, us-east1, scales to 0)
        │  1. POST /v1/uploads  (device token)  ◀── one-time signed PUT URL, 15 min
        │  2. PUT bytes ─────────────────────────▶ gs://<project>-recordings/rec/<device>/<file>
        │                                           (CMEK key in us-east1, 7-day auto-delete)
        │  3. POST /v1/confirm ◀── stored size/MD5/CRC32C + verified + HMAC receipt
        ▼
   delete local copy ONLY if verified            Eventarc "object finalized"
                                                           │
                         pipeline (Cloud Run, private, scales to 0)
                           stage 1: check WAV, split >19 min, start Chirp 3 batch job
                                    (Speech-to-Text v2, "us" multi-region,
                                     speaker separation + word timestamps)
                           stage 2: transcript → Gemini draft notes (Vertex AI, "us")
                                    → transcripts/<id>.json, drafts/<id>.json (30 days)
                                                           │
            review_api (laptop, 127.0.0.1) ◀──────────────┘
                 │  writes only approvals/ + audit/ ; PDF rendered on demand, never stored
                 ▼
            teacher review UI (Vite) — edit, map speakers, Approve (no send)
```

| Piece | Setting |
|---|---|
| KMS | key ring `goalvera`, key `recordings`, `us-east1`, software, 90-day rotation |
| Bucket | `<project>-recordings`, `us-east1`, CMEK default key, uniform access, public access prevention, soft delete **off** (deletes are final) |
| Lifecycle | `rec/` 7 days · `media/`, `media_tokens/` 7 days · `transcripts/`, `drafts/` 30 days (placeholder) · `approvals/`, `audit/` 30 days from the draft's creation (customTime) · `work/` 1 day |
| Logs | `_Default` log bucket retention 7 days; app logs are allow-listed ids/timings/error codes only |
| Service accounts | `upload-api`: create + read `rec/`, create + read `media/` and `media_tokens/` (Teacher Notes QR), read `control/` (token hashes) — no list/overwrite/delete · `pipeline`: read `rec/`,`config/`; read + list `media/`; read/write `transcripts/`,`drafts/`,`work/`; `speech.client`; `aiplatform.user` · `pipeline-trigger`: invoke the pipeline only · `builder`: Cloud Build for deploys |
| Keys | no service-account key files anywhere; upload-api signs URLs through IAM `signBlob` as itself |

## Run it

```bash
./infra/setup.sh                                  # step 1: APIs, KMS, bucket, SAs, IAM, log retention, Teacher Notes media
./infra/deploy.sh                                 # Cloud Run services + Eventarc trigger + demo roster
./infra/setup_media.sh                            # re-run after upload-api and review exist (CORS + review-web); idempotent
./infra/setup_media.sh --check                    # read-only: media/ + media_tokens/ grants, CORS, 7-day retention
./infra/deploy.sh pipeline                        # only the pipeline (+ trigger, roster); upload-api untouched
./infra/device_token.py create laptop-demo --out ~/.config/goalvera/laptop-demo.token
./infra/device_token.py revoke laptop-demo        # lost device
./infra/delete_session.py <recording_id> [--yes]  # delete one session everywhere (dry run by default)
./infra/check_logs.py --hours 3                   # prove no names / transcript text in logs
```

`deploy.sh` first compares each live service's settings with what it would set
and stops, before changing anything, if a deploy would drop or change one
(`ALLOW_ENV_CHANGE=1` to accept the printed diff).

The Python helpers need `google-cloud-storage` (a venv with
`pip install google-cloud-storage` is enough) and your `gcloud auth
application-default login`.

## Class codes and the roster

The recorder writes the class twice: in the file name, sanitized by the firmware
(`Reading-3A`), and in the sidecar, as picked on the device (`Reading 3A`). The
pipeline matches both against `config/demo_roster.json` through one function,
`app/backend-pipeline/common/classcodes.py` (case doesn't matter; spaces, hyphens
and underscores count as the same). The demo roster has the firmware's classes
(`Reading 3A`, `Math 2B`, `Circle Time`) plus `RM12`, all with the demo teacher.

- Teacher: the sidecar's teacher if it's a roster teacher (the recorder's
  "Demo Teacher" label maps to the demo teacher), otherwise the class's roster teacher.
- Unknown class: the session gets `needs_class_assignment`. The review list shows
  that flag and Approve stays blocked until someone assigns a class.
- Fix an existing session without re-running Speech-to-Text or Gemini (no cost):

      ./infra/backfill_session_roster.py <recording_id>                  # dry run
      ./infra/backfill_session_roster.py <recording_id> --yes            # apply
      ./infra/backfill_session_roster.py <recording_id> --class "Math 2B" --yes

  This changes only the class, student and teacher fields. It refuses to touch an
  approved session, deletes nothing, keeps the 30-day retention clock (customTime),
  and writes an ids-only audit record. It uses your `gcloud auth login`, not
  application-default credentials.

## Hosted review screen (https://review.example.org)

Cloud Run service `review` (us-east1) serving the review UI and API, signed in
with Identity Platform (email + password, verified email required) and limited
to the allowlist `config/review_allowlist.json`. Details: `app/review-web/README.md`.

The browser API key (public by design: the sign-in page sends it to every visitor)
is stored once, by `setup_review.sh`, as the Secret Manager secret `review-web-api-key`
(label `app=goalvera`). `review-web` may read that one secret; Cloud Run injects
it as `IDP_API_KEY` and the service hands it to the page at `/api/auth-config`. The
UI build never contains it, and `deploy_review.sh` never lists or reads API keys.

    ./infra/setup_review.sh      # Identity Platform, API key (+ its value in Secret Manager), service account, allowlist seed
    ./infra/deploy_review.sh     # build + deploy, domain mapping, prints the DNS record
    ./infra/deploy_review.sh --dry-run   # plan only: which steps the active account may run
    ./infra/review_allowlist.py add someone@example.org [--admin]
    ./infra/signin_accounts.py check|create someone@example.org   # sign-in account (sign-ups are off; add also creates it)

Add the DNS record at your DNS provider: `review  CNAME  ghs.googlehosted.com.`

## Admin activity journal ()

This school's own BigQuery dataset `goalvera_journal` (us-east1, recordings
key), table `activity` (day partitions, no expiration while the school is a
customer), read through the authorized view in `goalvera_journal_reports`.
`review-web` can only add rows (custom role `journalAppender` on the table);
admin reports run as `journal-reader`.

    ./infra/setup_journal.sh          # dataset, table, view, roles (idempotent)
    ./infra/journal_backfill.py       # dry run; --yes copies existing audit records
    ./infra/deploy_review.sh          # always JOURNAL_MODE=bigquery; stops if the table can't be confirmed

## Weekly lesson planner ()

Plans, weekly summaries and teacher notes are create-only objects under `planner/`
in the recordings bucket (`review-web`: read, list and create there; never overwrite
or delete). Kept until the school data is deleted; removed with `infra/delete_planner.py`.

AI drafts follow `PLANNER_AI` (config.sh, default `live`): Vertex Gemini in this
project with the pipeline's `GEMINI_MODEL` / `GEMINI_LOCATION`. Names are masked
before the call; no transcripts, quotes, IDs, emails or audio are sent. `live` needs
`review-web` to hold Vertex AI access (`roles/aiplatform.user`); `deploy_review.sh`
checks it and stops if it is missing, and never grants it.

    PLANNER_AI=off ./infra/deploy_review.sh   # deploy with no AI buttons
    ./infra/deploy_review.sh --dry-run        # shows the env changes vs the live service

Measured on demo data (Oct 4, 2026): 8-14 s per draft, about 1,000-1,300 input and
1,000-1,800 output+thinking tokens, roughly $0.005-0.009 per draft at the
introductory "us" rate.

## Cost notes

Idle: KMS key version ~$0.06/month, a few MB of storage and container images
(inside free tiers). Cloud Run scales to zero. Per session: Chirp 3 about
$0.016/audio-minute (~$0.72 for 45 min) + one Gemini Flash call (~1 cent).
Budget alert: "the legacy school demo $10" on the project.

## Delete the whole demo later

`gcloud projects delete example-project` removes everything (the KMS
key is scheduled for destruction with it).
