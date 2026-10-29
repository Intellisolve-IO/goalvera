# Testing notes for recorder 0.8.0

Host checks (no board, no cloud):

- `test/host/test_ota_policy.c` covers the rollback decision (confirm only after boot + Wi-Fi + an upload-API response; otherwise wait, then roll back) and the blocking rules (recording, upload pending, battery under 25% and not charging). It also checks the manifest signature against a test key. That key is not the firmware key.
- `tools/test_publish_ota.py` checks the signed bytes match the firmware, that a public writer binding stops a publish, and that the compiled-in key is still the placeholder (no private key in the tree).
- `test/host/test_pilot_guard.c` and `test/host/test_rec_crypt.c` cover the 0.7.9 wipe and the SD encryption that came in with this release.
- Upload-API `POST /v1/device/ota` is covered by `app/backend-pipeline/tests/test_upload_ota.py` with Google libraries stubbed.

Not run here: flashing a Hosyond, a real Wi-Fi join, a download from the firmware bucket, or a rollback on the bench. Those need the board and the bucket from `docs/ota.md`. The placeholder public key means a bench unit flashed from this tree will show `v0.8.0` and will not install an OTA image until the real public key is embedded and the image is rebuilt.

Friday USB flash uses the normal Hosyond bundle (`tools/build-hosyond35.sh` or `RECORDER_BOARD=hosyond35 tools/build-with-wifi.sh`), not `sdkconfig.defaults.secure`. The secure file turns on flash encryption and burns eFuses on first boot. Wi-Fi passwords stay in the environment or the on-screen picker, not in git. The flash bundle writes the bootloader, partition table, `otadata`, and the app. If the screen stays black, unplug USB for about 10 seconds.
