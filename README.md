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
