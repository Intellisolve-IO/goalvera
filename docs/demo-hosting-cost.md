# Demo hosting cost

What it costs to run the demo that this repository actually deploys, with synthetic data, in the existing Google Cloud layout. Prices are Google Cloud list prices in USD, read on 2026-10-29. They do not include tax, committed-use discounts, or the free tier, except where a line says the free tier covers it. A self-hosted project in another region can land on Tier 2 Cloud Run prices (higher). This note does not change any deployed resource.

The scripts and the services they create:

| Piece | How the repo sets it | Scales to zero today? |
| --- | --- | --- |
| Cloud Run `upload-api` | `infra/deploy.sh`: 1 vCPU, 512 MiB, concurrency 20, **`--min-instances=0`**, max 2, request-based billing, timeout 60s | Yes. The first upload pays a cold start. |
| Cloud Run `pipeline` | `infra/deploy.sh`: 1 vCPU, 1 GiB, concurrency 4, **`--min-instances=0`**, max 2, timeout 300s, private | Yes. |
| Cloud Run `review` | `infra/deploy_review.sh`: 1 vCPU, 512 MiB, concurrency 40, **`--min-instances=0`**, max 2, timeout 120s | Yes. The public site cold-starts. |
| Eventarc trigger `pipeline-gcs` | `infra/deploy.sh`, one event when an object is finalized in the recordings bucket | n/a |
| Cloud Storage | Recordings bucket, 7-day audio lifecycle, 30-day transcripts (`infra/config.sh`). Firmware bucket for OTA (`OTA_BUCKET`). | n/a |
| Cloud KMS | Software key `recordings` (bucket CMEK) from `infra/setup.sh`. HSM key `recording-wrap` (RSA-OAEP-3072) from `infra/setup_rec_key.sh`. | n/a |
| Speech-to-Text V2 | `pipeline_service/main.py` calls `BatchRecognize` with model `chirp_3`, speaker diarization, and word time offsets. Location `us` (`SPEECH_LOCATION`). Not dynamic batch. | n/a |
| Gemini | `gemini-3.8-flash` for the session draft (`GEMINI_MODEL`, location `us`). The same model for SVG materials. `gemini-3.1-flash-lite-image` only if someone generates a photo-style flashcard. | n/a |
| BigQuery | Datasets `goalvera_journal` and `goalvera_journal_reports` in `us-east1` | n/a |
| Secret Manager | API key, GitHub token, two mailer secrets, OTA signing key | n/a |
| Identity Platform | Email/password and Google sign-in for the review site | n/a |
| Cloud Logging | `_Default` bucket, 7-day retention in `infra/config.sh` | n/a |

The running path stores JSON in the recordings bucket. Firestore is not part of this tree. Firestore is **$0** in this estimate.

A "demo session" here is one synthetic recording that is uploaded, transcribed, and opened in the review site. The recorder writes 16 kHz mono 16-bit WAV. The repository does not fix a length. The numbers below use **30 minutes per session** and **20 sessions in a month**. A 10-minute clip is about one third of the Speech line. A 45-minute clip is about 1.5 times that line.

## List prices used

| Product | Price used | Source |
| --- | --- | --- |
| Cloud Run, request-based, Tier 1 (includes `us-east1`) | Active: $0.000024 per vCPU-second, $0.0000025 per GiB-second. Idle minimum instance: $0.0000025 per vCPU-second and per GiB-second. Requests: $0.40 per million. Free tier (active time, per billing account): 180,000 vCPU-seconds, 360,000 GiB-seconds, 2 million requests. | [Cloud Run pricing](https://cloud.google.com/run/pricing) |
| Speech-to-Text V2 standard recognition, first 500,000 minutes | $0.016 per minute. Dynamic batch (not what the code calls): $0.003 per minute. Billed per second of audio successfully processed. The V2 free 60 minutes on the pricing page is a V1 allowance and is not subtracted here. | [Speech-to-Text pricing](https://cloud.google.com/speech-to-text/pricing) |
| Gemini 3.8 Flash, introductory, through 31 Dec 2026 | $0.75 per 1M input tokens, $3.75 per 1M output tokens. From 1 Jan 2027 the same page lists $1.50 / $7.50. | [Agent Platform / Gemini pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing) |
| Gemini 3.1 Flash-Lite Image | 1120 output tokens billed at about $0.034 per 1K image | Same page |
| Cloud KMS | Software AES key version: $0.000082192 per hour (about $0.06 for a 730-hour month). HSM RSA-3072 key version: $0.003424658 per hour (about $2.50 per month). Crypto operations: $0.03 per 10,000. | [Cloud KMS pricing](https://cloud.google.com/kms/pricing). `infra/setup_rec_key.sh` already notes the HSM key at about $2.50/month. |
| Cloud Storage Standard, `us-east1` | About $0.020 to $0.022 per GB-month. Class A/B operations are fractions of a cent at this volume. | [Cloud Storage pricing](https://cloud.google.com/storage/pricing) |
| BigQuery on-demand, `us-east1` | First 1 TiB of queries per month free, then $6.25 per TiB. First 10 GiB of active logical storage free. | [BigQuery pricing](https://cloud.google.com/bigquery/pricing) |
| Secret Manager | First 6 active secret versions and 10,000 access operations per month free. | [Secret Manager pricing](https://cloud.google.com/secret-manager/pricing) |
| Identity Platform, email and Google (Tier 1) | First 50,000 monthly active users free. | [Identity Platform pricing](https://cloud.google.com/identity-platform/pricing) |
| Cloud Logging | First 50 GiB ingested per month free. | [Cloud Logging pricing](https://cloud.google.com/stackdriver/pricing) |

A month is taken as 730 hours (2,628,000 seconds), which is the usual cloud billing month.

Speech diarization and word timestamps are turned on in the `BatchRecognize` request. The Speech-to-Text pricing page does not show a separate diarization adder for V2 standard recognition. This estimate uses the $0.016/minute standard rate only. If a future price sheet bills diarization on top, the Speech line goes up and nothing else in the table does.

## As the repo deploys today

`upload-api`, `pipeline`, and `review` all use `--min-instances=0`. Twenty 30-minute sessions. Idle Cloud Run is $0. The first upload and the first page view pay a cold start of a few seconds.

| Line | Monthly list price | How it was counted |
| --- | --- | --- |
| Cloud Run, all three services at min 0 | **under $0.20** | Even if the pipeline holds a request for 3 minutes per session and the review site spends 30 seconds of server time per session, that is a few thousand vCPU-seconds. |
| Speech-to-Text, 600 minutes | **$9.60** | 20 × 30 × $0.016. Standard batch, which is what `BatchRecognize` does here. |
| Gemini session drafts | **about $0.50** | Assumption, not a measured prompt: 20 calls × 20,000 input tokens × $0.75/1M + 20 × 3,000 output tokens × $3.75/1M. A short synthetic clip like `app/samples/pipeline/gemini_output_rm12.json` is cheaper. A long transcript with a large prompt is still a dollar or two, not tens of dollars. |
| Planner and SVG materials, light use | **about $1** | Allowance for a handful of extra Flash calls. Photo-style flashcards add about $0.034 each and are not in the base number. |
| KMS HSM `recording-wrap` | **$2.50** | $0.003424658 × 730 hours. |
| KMS software `recordings` | **$0.06** | $0.000082192 × 730 hours. |
| KMS decrypt/encrypt operations | **$0** | Tens of operations. The $0.03/10,000 rate does not show up. |
| Cloud Storage | **under $0.10** | About 1.1 GB of WAV ingested (20 × 30 min × 16 kHz × 2 bytes). The 7-day lifecycle means only a fraction of that sits for the whole month. |
| BigQuery | **$0** | Storage and the admin report queries sit inside the free tier at this size. |
| Secret Manager | **$0** | The config names five secrets (`review-web-api-key`, `github-issues-token`, `mailer-oauth-client`, `mailer-refresh-token`, `recorder-ota-signing-key`). Six active versions are free. |
| Identity Platform | **$0** | A handful of demo sign-ins. Tier 1 is free under 50,000 monthly active users. |
| Cloud Logging | **$0** | Far under 50 GiB at 20 sessions. |
| Eventarc | **not a material line** | One trigger, about 20 finalize events. No separate price is set in the scripts. |
| Firestore | **$0** | Not deployed. |
| **Total** | **about $14** | Speech ($9.60) and the HSM key ($2.50) dominate. Cloud Run idle is $0. |

From 1 January 2027 the same Gemini calls at the higher Flash rate are still about $1 to $2. The total stays about $14.

The Cloud Run free tier is described as active-time pricing. It can cover the small active-time lines if the billing account has not already used the allowance. This total does not depend on that.

## Optional warm instances

Keeping a service at `--min-instances=1` avoids a cold start. That is an operator choice. It is not what `deploy.sh` or `deploy_review.sh` do. One always-on 1 vCPU / 512 MiB service is about **$9.86** a month (1 vCPU idle × $0.0000025 × 2,628,000s = $6.57, plus 0.5 GiB × $0.0000025 × 2,628,000s = $3.29).

| Setup | About |
| --- | --- |
| Repo as written (all three at min 0) | **$14 / month** |
| `upload-api` kept warm, review still cold | **$24 / month** |
| `upload-api` and `review` both kept warm | **$34 / month** |

Speech, Gemini, and KMS are the same in each row.

## Cheapest setup that can still run the demo

Cheapest that still does what the code does (encrypted upload, Chirp transcription, Gemini draft, review sign-in), without standing up a second architecture:

1. **Min instances 0** on `upload-api`, `pipeline`, and `review`. This is already what the deploy scripts do. Keeping `upload-api` warm would add about $10, and keeping `review` warm as well would add about $10 more.
2. **Keep both KMS keys.** The HSM wrap key is $2.50 and is the expensive key. Dropping it for a software key would save about $2.44 and would be a different security design than `infra/setup_rec_key.sh`. Not recommended just to save that.
3. **Dynamic batch Speech**, if waiting longer for a transcript is acceptable. The code does not set dynamic batch today. At $0.003/minute, 600 minutes is **$1.80** instead of $9.60. That is a small code change, not a console toggle.
4. Leave Firestore off. Leave the journal on BigQuery; at this size it is inside the free tier.
5. Do not send mail in the cheap demo (`MATERIALS_EMAIL_DEFAULT` off). Gmail API calls are not a meaningful GCP line, but they need the OAuth mailbox.

| Variant | Speech | Warm Cloud Run | KMS | Gemini + the rest | About |
| --- | --- | --- | --- | --- | --- |
| Repo as it deploys now | $9.60 | ~$0 | $2.56 | ~$2 | **$14** |
| Same, but `upload-api` kept warm | $9.60 | $9.86 | $2.56 | ~$2 | **$24** |
| Scale to zero and dynamic-batch Speech | $1.80 | ~$0 | $2.56 | ~$2 | **$6** |
| Same, but 20 × 10-minute clips and standard Speech | $3.20 | ~$0 | $2.56 | ~$2 | **$8** |

**About $6 per month** is the cheapest variant that still runs the real pipeline on 20 half-hour sessions: min instances already at 0, dynamic-batch Chirp, both KMS keys kept, Gemini at the introductory Flash rate. **About $14** is what the repo deploys today with the current Speech call and no warm instances.

None of these totals include a domain name (the Cloud Run mapping itself is a zero-price mapping in `infra/deploy_review.sh`), Cloud Build minutes spent on deploys, or support time.
