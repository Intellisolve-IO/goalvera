# Goalvera

**Classroom audio to IEP evidence and progress notes, with the teacher in the loop.**
Homepage: https://goalvera.app

Goalvera is an open-source classroom discussion recorder (ESP32-S3 board with on-board mic, encrypted microSD
recordings) plus a Google Cloud pipeline and a **teacher-review (human-in-the-loop)** web app that turns
session transcripts into draft notes and IEP-goal evidence a teacher edits and approves.

The product name and homepage live in one place: [`product.json`](product.json).

## Data rule

**Never commit recordings, student data or real audio.** All fixtures under `app/samples/` are fictional.
`.gitignore` blocks audio, build output, deploy config and secrets. Check before every push:

```bash
git ls-files | rg -i '\.(wav|pcm|raw|mp3|m4a|flac|ogg|pem)$'   # must print nothing
```

## Deploying your own instance

All project-specific values (Google Cloud project, region, bucket, review domain, mail sender) come from the
environment or `infra/deploy.env` (copy `infra/deploy.env.example`). See [`infra/README.md`](infra/README.md).
The recorder's recording **public** key is a build-time input and is never committed:

```bash
firmware/recorder-idf/tools/fetch-rec-pubkey.sh 1 local/recording_pubkey.pem
idf.py -D RECORDING_PUBKEY_PEM=$PWD/local/recording_pubkey.pem build
```

See [`firmware/recorder-idf/docs/sd-encryption.md`](firmware/recorder-idf/docs/sd-encryption.md).

## Layout

```
firmware/
  recorder-idf/         # ESP-IDF v5 classroom recorder (LVGL UI, WAV + sidecar)
  esp-idf-plan.md       # pointer to firmware/recorder-idf
app/
  backend-pipeline/     # gemini_analyzer (prompt + schema + offline fixture runner); decrypter / STT stubs
  frontend-web/         # teacher weekly matrix + session HITL review + admin strip (Vite/React)
  samples/              # synthetic fixtures only
  cloud-terraform/      # stubs, not applied yet
docs/                   # design spec, agents design, runbooks
```

## Firmware

The classroom-recorder firmware is `firmware/recorder-idf/` (ESP-IDF v5, LVGL 9). Build, flash, the config JSON, and the on-board checklist are in [`firmware/recorder-idf/README.md`](firmware/recorder-idf/README.md). Approved screen mockups are in `docs/ui-mockups/`.

The **Hosyond 3.5" board is the only supported recorder hardware.** Recording on an iPhone is a page in the review web app, not a second board.

## iPhone web recorder

A signed-in **Record** page lets a teacher record a session in Safari on an iPhone. There is no native app. The page uses the same Identity Platform sign-in and roles as the review site.

David's decisions for this version:

- **No encryption on the phone.** The upload is HTTPS. The bucket's existing key encrypts the file at rest. Each piece is deleted from the phone as soon as the server confirms it. Sign-out also deletes anything still on the phone.
- **Only a teacher or an admin can record.** A reviewer, an RBT, a missing role, and any unknown role are read-only: the Record tab is hidden, and both `/v1/web/uploads` and `/v1/web/confirm` return 403.
- **A lock, an app switch, or a call pauses the same session.** The teacher taps Resume. The gap is marked and shown on the review page (`Recording paused 2:14 for 40 s`).
- **On the demo, Record stays on.** The page shows a sample lesson to read aloud and a reminder not to record real students. That reminder is on only when `SCHOOL_PROFILE` is `everyday-heroes` or `demo-school`. A real school must set `SCHOOL_PROFILE` to its own id and `WEB_DEMO_RECORDER=off`. If `SCHOOL_PROFILE` is unset, the reminder stays off.

Safari records AAC in mp4, in pieces of about 60 seconds, with a screen wake lock while the microphone is open. Pieces wait in IndexedDB only until `/v1/web/confirm` says they arrived. The next time the page opens, unsent pieces can be resumed or discarded. Add to Home Screen uses the web app manifest.

`POST /v1/web/uploads` and `POST /v1/web/confirm` on the upload service check the teacher's ID token (the same verifier and allowlist as the review site, including an admin's "no access" change) and sign a create-only upload under `web/<session>/`. The board's `/v1/uploads` path and device tokens are unchanged. When the teacher taps Stop, the service writes `complete.json`. The pipeline's ffmpeg step joins the pieces, writes 16 kHz mono 16-bit WAV to `rec/web-<user>/<YYYYMMDD_HHMMSS_CLASS>.wav`, and deletes the pieces. The pipeline can read and delete under `web/`; it cannot create objects there. It can create only `rec/web-*`. Discarding a session deletes that session's `web/<session>/` objects immediately. Anything still left is deleted by the bucket lifecycle after one day (Cloud Storage's shortest age). `infra/setup_web_recorder.sh` also enforces public access prevention on the bucket.

This change does not deploy anything. Before a phone can upload, someone has to run [`infra/setup_web_recorder.sh`](infra/setup_web_recorder.sh) and then deploy with `ALLOW_ENV_CHANGE=1`, setting `UPLOAD_PUBLIC_URL` to the upload service origin for the review deploy. The manual test plan for a real iPhone is [`docs/iphone-web-recorder.md`](docs/iphone-web-recorder.md).

## App: run locally (fixtures)

Teacher review UI:

```bash
cd app/frontend-web
npm install
npm run dev        # open http://localhost:5173
```

Gemini analyzer (offline by default, no API key needed):

```bash
cd app/backend-pipeline/gemini_analyzer
python3 run_fixture.py --validate-only
python3 run_fixture.py --mode offline
# --mode live / auto use GOOGLE_API_KEY or GEMINI_API_KEY from the environment; never commit keys
```

More detail: [`app/README.md`](app/README.md), [`docs/design-spec.md`](docs/design-spec.md).

## License

Apache License 2.0. See [LICENSE](LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Security: [SECURITY.md](SECURITY.md).
