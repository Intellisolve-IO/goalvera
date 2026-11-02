# Recorder OTA (0.8.0)

The Hosyond 3.5" image checks a signed manifest in Google Cloud Storage, downloads a newer image into the inactive app slot, and reboots. The partition table is unchanged: `nvs`, `otadata`, `phy_init`, `ota_0` (6 MB), `ota_1` (6 MB). A blank `otadata` boots `ota_0`.

The CrowPanel uses the same client and a different manifest path (`firmware/crowpanel43/`), so it never installs a Hosyond image.

## When an update runs

- At boot, and again every 6 hours.
- Only while idle: not starting, recording, paused, or saving, and no finished recording is still waiting to upload.
- Not when the Hosyond battery is under 25% and the board is not on external power (charger or USB). At 25% or while charging, the battery does not block. The CrowPanel has no gauge, so this rule does not apply to it.
- The manifest version must be a newer `X.Y.Z` than the running image. The home screen shows that version at the right of Recent activity (`v0.8.0`).
- Wi-Fi passwords are not in the repo. The manifest URL is not a secret. Put Wi-Fi in the environment (`tools/build-with-wifi.sh`) or join from the on-screen picker.

## Manifest

`https://storage.googleapis.com/example-project-firmware/firmware/hosyond35/manifest.json`

```json
{"version":"0.8.0","url":"https://storage.googleapis.com/.../recorder-0.8.0.bin","sha256":"<64 lowercase hex>","signature":"<base64>"}
```

The signature is Ed25519 over the exact bytes

```
APOTA1
<version>
<url>
<sha256>
```

(each line ends with `\n`). The URL must be `https://storage.googleapis.com/<bucket>/<object>` with no query string. The public key is compiled in (`main/ota_pubkey.c`). The private key is not in this repo. Publish reads it from Secret Manager using `PROJECT_ID` and `OTA_SIGNING_SECRET` (the default secret name is `recorder-ota-signing-key`). Do not put the project id or the private key in git.

## Rollback

`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` is on. An image installed by OTA stays pending. It is marked valid only after that boot has Wi-Fi and the upload API answers `POST /v1/device/ota` (any HTTP status, including 401). If that does not happen within 10 minutes, the device rolls back to the previous slot. A USB flash is not pending, so it does not roll back for lack of Wi-Fi.

The upload API call needs the device token (NVS, or `/sd/config/upload_token.txt` on first boot). Events (`check`, `blocked`, `rejected`, `download`, `staged`, `confirmed`, `rollback`, `failed`) are stored at `control/ota-log/<device>/` in the recordings bucket. No student data is in them.

OTA does not update the bootloader. The first image that should be able to roll back has to be flashed over USB, including the bootloader (the `flash.sh` bundle does that).

## One-time Google Cloud setup

`PROJECT_ID` and `OTA_BUCKET` come from the environment or `infra/deploy.env`. The manifest URLs in `sdkconfig.defaults` are build-time placeholders, not a live bucket. These commands are not run by the firmware build. They create the bucket. The signing secret already exists for the project you set in `PROJECT_ID`; the commands below only show how to create one in a new project.

The firmware bucket is world-readable so a recorder with no Google credential can fetch the manifest and the image. It must not be world-writable. Do not grant `objectAdmin`, `objectCreator`, `objectUser`, or any legacy writer role to `allUsers` or `allAuthenticatedUsers`.

```bash
: "${PROJECT_ID:?set PROJECT_ID}"
: "${OTA_SIGNING_SECRET:=recorder-ota-signing-key}"
REGION="${REGION:-us-east1}"
OTA_BUCKET="${OTA_BUCKET:-${PROJECT_ID}-firmware}"

gcloud storage buckets create "gs://${OTA_BUCKET}" \
  --project="${PROJECT_ID}" --location="${REGION}" --uniform-bucket-level-access

# Public read only. There is no public write binding.
gcloud storage buckets add-iam-policy-binding "gs://${OTA_BUCKET}" \
  --member=allUsers --role=roles/storage.objectViewer

# Whoever publishes (a person, or a release service account). Not allUsers.
gcloud storage buckets add-iam-policy-binding "gs://${OTA_BUCKET}" \
  --member="user:EMAIL" --role=roles/storage.objectAdmin

# Confirm the public member has objectViewer and nothing that can write.
gcloud storage buckets get-iam-policy "gs://${OTA_BUCKET}"
```

If an organization policy blocks `allUsers`, the binding above fails and devices cannot fetch the manifest anonymously. Leave the bucket private in that case and do not publish until there is another way for the device to read it. Do not turn public access prevention off in order to grant a writer role.

Signing key (Ed25519). The public half is already in `main/ota_pubkey.c`. The private half is only in Secret Manager. `publish_ota.py` reads it with `PROJECT_ID` and `OTA_SIGNING_SECRET` (default `recorder-ota-signing-key`). To rotate the key in a project, generate a new pair outside the repo, store the private half, then embed the public half and rebuild:

```bash
: "${PROJECT_ID:?set PROJECT_ID}"
: "${OTA_SIGNING_SECRET:=recorder-ota-signing-key}"
umask 077
openssl genpkey -algorithm ED25519 -out /tmp/recorder-ota-private.pem
openssl pkey -in /tmp/recorder-ota-private.pem -pubout -out /tmp/recorder-ota-public.pem

gcloud secrets create "${OTA_SIGNING_SECRET}" \
  --project="${PROJECT_ID}" --replication-policy=automatic
gcloud secrets versions add "${OTA_SIGNING_SECRET}" \
  --project="${PROJECT_ID}" --data-file=/tmp/recorder-ota-private.pem

# Public half into the firmware, then rebuild. The script refuses a path inside the repo.
python3 firmware/recorder-idf/tools/embed_ota_pubkey.py /tmp/recorder-ota-public.pem

shred -u /tmp/recorder-ota-private.pem /tmp/recorder-ota-public.pem 2>/dev/null || \
  rm -f /tmp/recorder-ota-private.pem /tmp/recorder-ota-public.pem
```

Grant `secretAccessor` on `recorder-ota-signing-key` only to the person or service account that runs the release script. Do not grant it to `upload-api` or to `allUsers`.

OTA log objects are written by upload-api into the recordings bucket (not the firmware bucket). upload-api can already read `control/`. It also needs to create objects under `control/ota-log/` only:

```bash
source infra/config.sh
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${UPLOAD_SA}" \
  --role=roles/storage.objectCreator \
  --condition="expression=resource.name.startsWith(\"projects/_/buckets/${BUCKET}/objects/control/ota-log/\"),title=ota-log-create"
```

Deploy upload-api from `main` after this change is merged so `POST /v1/device/ota` exists. An image installed by USB does not need that route. The first OTA does: if the route is missing, the new image cannot confirm and rolls back.

## Publishing

From `firmware/recorder-idf`, after the image is built with the matching public key:

```bash
# Key from Secret Manager. Nothing is uploaded until the IAM check passes.
python3 tools/publish_ota.py --bin build-hosyond35/recorder.bin --board hosyond35

# Print the manifest and stop.
python3 tools/publish_ota.py --bin build-hosyond35/recorder.bin --dry-run
```

The script reads the private key from Secret Manager (`gcloud secrets versions access`) or from `OTA_SIGNING_KEY` (a PEM file outside the repo). It refuses a key path inside the repo, refuses the all-zero placeholder, refuses to upload if `allUsers` or `allAuthenticatedUsers` can write, and does not create the bucket. The image object is uploaded before the manifest, so a device never sees a manifest for a missing file.

`publish_ota.py` does not print the key. Do not pass the key as an argument.
