# Open-source readiness

This repository is the public Goalvera tree. Google Cloud project ids, bucket names, hostnames, mailboxes, and signing keys are configuration. They are not product names, and this tree does not default them to a live project.

Set them in the environment or in `infra/deploy.env` (start from `infra/deploy.env.example`). `PROJECT_ID` and `REVIEW_DOMAIN` are required. Cloud Run services in `infra/deploy.sh` and `infra/deploy_review.sh` use a minimum of 0 instances.

Do not commit recordings, student data, device tokens, Wi-Fi passwords, private keys, or service-account JSON. The recorder's recording public key is a build-time input (`RECORDING_PUBKEY_PEM`) and is gitignored. The OTA signing private key belongs in Secret Manager. `firmware/recorder-idf/main/ota_pubkey.c` ships as an all-zero placeholder until an operator embeds the public half and rebuilds.

The fictional demo school in the sample roster is Everyday Heroes Academy. The placeholder school profile is Demo School (badge DS). A stored branding id `atlanta-prep` still loads as Demo School so older bucket records keep working. New writes use `demo-school`.

Fixtures under `app/samples/` are synthetic. This tree does not include commercial reading-program curriculum text, call audio, or transcripts of real sessions.

Report vulnerabilities as described in `SECURITY.md`. Community standards are in `CODE_OF_CONDUCT.md`.
