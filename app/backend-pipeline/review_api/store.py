"""Object store used by the review service: the CMEK bucket in the demo
project, or a local folder with the same layout for offline demo runs/tests.

Only JSON records live here. PDFs are never written to either store.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,300}$")


class Conflict(Exception):
    """create_only write and the object already exists."""


def _check(name: str) -> str:
    if not NAME_RE.match(name) or ".." in name or name.startswith("/"):
        raise ValueError("bad object name")
    return name


class LocalStore:
    kind = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, name: str) -> Path:
        return self.root / _check(name)

    def read_json(self, name: str) -> dict | None:
        p = self._p(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def read_bytes(self, name: str) -> bytes | None:
        p = self._p(name)
        return p.read_bytes() if p.is_file() else None

    def generation(self, name: str) -> int | None:
        p = self._p(name)
        return p.stat().st_mtime_ns if p.is_file() else None

    def write_json(self, name: str, data: dict, *, create_only: bool = False,
                   custom_time: dt.datetime | None = None, if_generation: int | None = None) -> None:
        p = self._p(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        if if_generation is not None and self.generation(name) != if_generation:
            raise Conflict(name)
        if create_only:
            try:
                with open(p, "x", encoding="utf-8") as f:
                    f.write(payload)
            except FileExistsError as exc:
                raise Conflict(name) from exc
        else:
            p.write_text(payload, encoding="utf-8")

    def list(self, prefix: str) -> list[str]:
        base = self.root
        return sorted(
            str(p.relative_to(base)).replace(os.sep, "/")
            for p in base.rglob("*") if p.is_file()
            and str(p.relative_to(base)).replace(os.sep, "/").startswith(prefix)
        )

    def list_times(self, prefix: str) -> dict:
        """name -> when it was written, from the listing alone (no object read)."""
        return {n: self.created(n) for n in self.list(prefix)}

    def created(self, name: str) -> dt.datetime | None:
        p = self._p(name)
        return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc) if p.is_file() else None


class GcsStore:
    kind = "gcs"

    def __init__(self, bucket: str, project: str):
        if not os.environ.get("K_SERVICE"):
            # Laptop / user ADC needs a quota project. On Cloud Run the service account
            # bills to its own project; forcing x-goog-user-project there would require
            # serviceusage.services.use, which review-web deliberately doesn't have (403).
            os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", project)
        from google.cloud import storage
        self._b = storage.Client(project=project).bucket(bucket)
        self.bucket = bucket

    def read_json(self, name: str) -> dict | None:
        from google.api_core.exceptions import NotFound
        try:
            return json.loads(self._b.blob(_check(name)).download_as_bytes())
        except NotFound:
            return None

    def read_bytes(self, name: str) -> bytes | None:
        """Teacher Notes photos / videos under media/."""
        from google.api_core.exceptions import NotFound
        try:
            return self._b.blob(_check(name)).download_as_bytes()
        except NotFound:
            return None

    def generation(self, name: str) -> int | None:
        blob = self._b.get_blob(_check(name))
        return blob.generation if blob else None

    def write_json(self, name: str, data: dict, *, create_only: bool = False,
                   custom_time: dt.datetime | None = None, if_generation: int | None = None) -> None:
        from google.api_core.exceptions import PreconditionFailed
        blob = self._b.blob(_check(name))
        if custom_time is not None:
            # Lifecycle "daysSinceCustomTime" then deletes this record together with the
            # draft it belongs to (same 30-day rule), not 30 days after approval.
            blob.custom_time = custom_time
        try:
            blob.upload_from_string(json.dumps(data, indent=2, ensure_ascii=False),
                                    content_type="application/json",
                                    if_generation_match=0 if create_only else if_generation)
        except PreconditionFailed as exc:
            raise Conflict(name) from exc

    def list(self, prefix: str) -> list[str]:
        return sorted(b.name for b in self._b.list_blobs(prefix=prefix))

    def list_times(self, prefix: str) -> dict:
        """name -> creation time, from the listing alone: needs list access only, never object read
        (review-web may list rec/ but not read the audio)."""
        return {b.name: b.time_created for b in self._b.list_blobs(prefix=prefix)}

    def created(self, name: str) -> dt.datetime | None:
        """When the record was FIRST written: its customTime if a rewrite kept it, else creation time."""
        blob = self._b.get_blob(_check(name))
        return (blob.custom_time or blob.time_created) if blob else None


class GcloudCliStore:
    """Same bucket, through the gcloud CLI and your normal `gcloud auth login`
    (for when the application-default login has expired). Slower: one gcloud
    call per object."""
    kind = "gcloud-cli"

    def __init__(self, bucket: str, project: str, runner=None):
        import subprocess
        self.bucket, self.project = bucket, project
        self._run = runner or (lambda args, **kw: subprocess.run(args, capture_output=True, text=True, **kw))

    def _url(self, name: str) -> str:
        return f"gs://{self.bucket}/{_check(name)}"

    def _gcloud(self, *args, input_text: str | None = None):
        return self._run(["gcloud", "storage", *args, f"--project={self.project}"], input=input_text)

    @staticmethod
    def _missing(r) -> bool:
        err = (r.stderr or "")
        return r.returncode != 0 and ("matched no objects" in err or "No URLs matched" in err or "404" in err)

    def read_json(self, name: str) -> dict | None:
        r = self._gcloud("cat", self._url(name))
        if self._missing(r):
            return None
        if r.returncode != 0:
            raise RuntimeError(f"gcloud storage cat failed ({r.returncode})")
        return json.loads(r.stdout)

    def _describe(self, name: str) -> dict | None:
        r = self._gcloud("objects", "describe", self._url(name), "--format=json")
        if self._missing(r):
            return None
        if r.returncode != 0:
            raise RuntimeError(f"gcloud storage objects describe failed ({r.returncode})")
        return json.loads(r.stdout)

    def generation(self, name: str) -> int | None:
        d = self._describe(name)
        return int(d["generation"]) if d else None

    def created(self, name: str) -> dt.datetime | None:
        """When the record was FIRST written: its customTime if a rewrite kept it, else creation time."""
        d = self._describe(name)
        if not d:
            return None
        ts = d.get("custom_time") or d.get("customTime") or d["creation_time"]
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

    def write_json(self, name: str, data: dict, *, create_only: bool = False,
                   custom_time: dt.datetime | None = None, if_generation: int | None = None) -> None:
        args = ["cp", "-", self._url(name), "--content-type=application/json"]
        gen = 0 if create_only else if_generation
        if gen is not None:
            args.append(f"--if-generation-match={gen}")
        if custom_time is not None:
            args.append(f"--custom-time={custom_time.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        r = self._gcloud(*args, input_text=json.dumps(data, indent=2, ensure_ascii=False))
        if r.returncode != 0:
            if "412" in (r.stderr or "") or "Precondition" in (r.stderr or ""):
                raise Conflict(name)
            raise RuntimeError(f"gcloud storage cp failed ({r.returncode})")

    def list_times(self, prefix: str) -> dict:
        return {n: self.created(n) for n in self.list(prefix)}

    def list(self, prefix: str) -> list[str]:
        r = self._gcloud("ls", f"gs://{self.bucket}/{_check(prefix)}**")
        if r.returncode != 0:
            if "matched no objects" in (r.stderr or "") or "No URLs matched" in (r.stderr or ""):
                return []
            raise RuntimeError(f"gcloud storage ls failed ({r.returncode})")
        base = f"gs://{self.bucket}/"
        return sorted(line[len(base):] for line in r.stdout.splitlines() if line.startswith(base) and not line.endswith("/"))
