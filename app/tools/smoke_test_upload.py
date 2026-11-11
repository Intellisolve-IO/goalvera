#!/usr/bin/env python3
"""Negative-path smoke test for the upload service (the checks step 7's bench
tests will rely on). Uses small synthetic .json sidecar files only, so the
transcription pipeline is not triggered and nothing is billed beyond fractions
of a cent. Creates a throwaway device token and revokes it at the end.

  GOALVERA_UPLOAD_URL=https://upload-api-....run.app python app/tools/smoke_test_upload.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from upload_recording import call, fingerprint, put, upload_one  # noqa: E402

ROOT = HERE.parents[1]
BASE = os.environ["GOALVERA_UPLOAD_URL"].rstrip("/")
DEVICE = "smoke-test-" + time.strftime("%H%M%S")
PY = sys.executable
results = []


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tok_file = os.path.join(d, "t")
        subprocess.run([PY, str(ROOT / "infra/device_token.py"), "create", DEVICE, "--out", tok_file], check=True)
        token = open(tok_file).read().strip()
        time.sleep(31)  # let the service's token cache pick up the new device

        stem = time.strftime("%Y%m%d_%H%M%S") + "_SMOKE"
        good = os.path.join(d, stem + ".json")
        Path(good).write_text(json.dumps({"synthetic": True, "smoke": stem}))
        fp = fingerprint(good)

        st, _ = call(f"{BASE}/v1/uploads", "wrong-token", {"device_id": DEVICE, "filename": stem + ".json", **fp})
        check("wrong token is refused (401)", st == 401)

        st, _ = call(f"{BASE}/v1/uploads", token, {"device_id": DEVICE, "filename": "../../etc/passwd", **fp})
        check("path outside rec/<device>/ is refused (400)", st == 400)

        # Corrupted copy: URL is bound to the original MD5/CRC32C, so Google must reject the bytes.
        st, grant = call(f"{BASE}/v1/uploads", token, {"device_id": DEVICE, "filename": stem + ".json", **fp})
        bad = os.path.join(d, "bad.json")
        data = bytearray(Path(good).read_bytes()); data[5] ^= 0x01
        Path(bad).write_bytes(bytes(data))
        st_put = put(grant["upload_url"], grant["headers"], bad)
        check("corrupted bytes rejected by Google on PUT (400)", st_put == 400)
        st, conf = call(f"{BASE}/v1/confirm", token, {"device_id": DEVICE, "object": grant["object"], **fp})
        check("confirm after corrupted upload is NOT verified (device keeps its copy)", not conf.get("verified"))

        check("good upload verified with valid receipt", upload_one(BASE, DEVICE, token, good))

        st_put = put(grant["upload_url"], grant["headers"], good)
        check("same name cannot be uploaded twice (412, create-only)", st_put == 412)

        st, conf = call(f"{BASE}/v1/confirm", token, {"device_id": DEVICE, "object": grant["object"],
                                                       **{**fp, "md5": base64.b64encode(hashlib.md5(b'x').digest()).decode()}})
        check("confirm with a different checksum is NOT verified", conf.get("verified") is False)

        subprocess.run([PY, str(ROOT / "infra/device_token.py"), "revoke", DEVICE], check=True)
        time.sleep(31)
        st, _ = call(f"{BASE}/v1/uploads", token, {"device_id": DEVICE, "filename": stem + "b.json", **fp})
        check("revoked device is refused (401)", st == 401)

    # clean the smoke object
    subprocess.run(["gcloud", "storage", "rm", f"gs://{os.environ.get('BUCKET', 'example-project-recordings')}/rec/{DEVICE}/**",
                    "--project", os.environ.get("PROJECT_ID", "example-project"), "--quiet"],
                   capture_output=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
