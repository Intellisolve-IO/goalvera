# SD-card recording encryption (tasks#22, pilot checklist G5), Hosyond 3.5" only

Since 0.8.0 the Hosyond build writes every recording to the SD card encrypted
(`CONFIG_RECORDER_SD_ENCRYPTION=y`, format **APENC1**, spec in `main/rec_crypt.h`).
CrowPanel builds are unchanged (plain WAV, `"encryption": "none"`).

## Design
- **Per-file key**: 256-bit random AES key per recording (hardware RNG).
- **Wrapped with a public key**: RSA-OAEP-3072/SHA-256 under the recording key's
  public half, compiled into the firmware (`main/recording_pubkey.pem`, from
  `tools/fetch-rec-pubkey.sh`). The private half is an HSM key in Cloud KMS
  (`infra/setup_rec_key.sh`); only the pipeline service account can decrypt.
- **The recorder cannot decrypt its own recordings.** No secret that decrypts
  audio is stored on the device, so a stolen recorder (card pulled, or flash
  dumped) exposes no audio, with or without flash encryption. This replaces the
  earlier "device data key in NVS" idea: an NVS key is only as safe as flash
  encryption, and the cloud would have needed a copy of every device key.
- **Streaming AES-256-GCM** in 4096-byte chunks (one per writer block, no extra
  buffering beyond one chunk). Nonce = 8-byte random prefix || chunk counter.
  Each chunk's AAD binds the header hash, chunk index and a final flag, so
  edits, reordering and truncation are detected. A file cut off by power loss
  decrypts up to its last whole chunk and is flagged `truncated`.
- Plaintext is raw PCM; format fields are in the header and the cloud rebuilds
  the WAV header (no seek-back rewrite on the card).
- Software AES (`CONFIG_MBEDTLS_HARDWARE_AES` stays off, see sdkconfig.defaults:
  the hardware driver's DMA allocations broke TLS under RAM pressure). 32 KB/s of
  audio is far below software AES-GCM speed. RSA uses the hardware MPI.
- 4 KB + 16 B internal RAM (the chunk is encrypted in place).

## What did not change
- File names stay `....wav` on the card and in `rec/<device>/` (upload, cap and
  wipe logic untouched). The bytes are APENC1, so the object in GCS is ciphertext.
- Upload verification (size/MD5/CRC32C/receipt) covers the encrypted bytes.
- Erase after verified upload (0.7.9, zero then unlink) is unchanged.
- The `.json` sidecar (labels, markers, duration) is not encrypted; it now says
  `"encryption": "apenc1"` and the key id.

## Cloud
`pipeline_service` detects the magic, unwraps the key with KMS
(`REC_KMS_KEYRING_PATH`), decrypts in the temp dir, and sends plain WAV to Speech
from `work/<rid>/chunks/`. Older plain WAV files still work.

## Key rotation
New KMS key version, then `tools/fetch-rec-pubkey.sh <ver>` and set
`CONFIG_RECORDER_REC_KEY_ID="recording-wrap/<ver>"`. Keep old versions enabled until
every file wrapped with them has been processed.
