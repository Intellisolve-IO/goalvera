"""infra/deploy.sh with stubbed gcloud (nothing reaches the cloud): it stops before
changing anything if a deploy would drop or change a live setting, and
`deploy.sh pipeline` leaves upload-api alone ()."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "deploy.sh"
P = "example-project"
PIPELINE_ENV = {"BUCKET": f"{P}-recordings", "GOOGLE_CLOUD_PROJECT": P, "SPEECH_LOCATION": "us",
                "VERTEX_AI": "1", "GEMINI_LOCATION": "us", "GEMINI_MODEL": "gemini-3.8-flash"}


def run(tmp_path, live_env: dict, *args, extra_env=None):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    svc = {"spec": {"template": {"spec": {"containers": [
        {"env": [{"name": k, "value": v} for k, v in live_env.items()]}]}}}}
    (tmp_path / "svc.json").write_text(json.dumps(svc))
    (bin_ / "gcloud").write_text(f"""#!/usr/bin/env bash
echo "gcloud $*" >> "$CALLS"
case "$*" in
  "run services describe pipeline"*"--format=json"*) cat {tmp_path / 'svc.json'} ;;
  "run services describe"*) echo https://x.run.app ;;
  "eventarc triggers describe"*) exit 0 ;;
esac
exit 0
""")
    (bin_ / "gcloud").chmod(0o755)
    env = {**os.environ, "PROJECT_ID": "example-project", "REVIEW_DOMAIN": "review.example.org", "GITHUB_ISSUES_REPO": "your-org/your-issues-repo", "MAILER_SENDER_MAILBOX": "snowwhite@staff.example", "PATH": f"{bin_}:{os.environ['PATH']}", "CALLS": str(calls), **(extra_env or {})}
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    return p, calls.read_text()


def test_pipeline_only_with_matching_settings(tmp_path):
    p, calls = run(tmp_path, PIPELINE_ENV, "pipeline")
    assert p.returncode == 0, p.stderr
    assert "pipeline: live settings match" in p.stdout
    assert "gcloud run deploy pipeline" in calls
    assert "run deploy upload-api" not in calls


def test_a_setting_the_script_would_change_stops_everything(tmp_path):
    live = {**PIPELINE_ENV, "GEMINI_MODEL": "gemini-other", "EXTRA_FLAG": "1"}
    p, calls = run(tmp_path, live, "pipeline")
    assert p.returncode == 1
    assert "GEMINI_MODEL: 'gemini-other' -> 'gemini-3.8-flash'" in p.stderr
    assert "EXTRA_FLAG: '1' -> '<removed>'" in p.stderr
    assert "run deploy" not in calls and "storage cp" not in calls


def test_allow_env_change_goes_ahead(tmp_path):
    p, calls = run(tmp_path, {**PIPELINE_ENV, "EXTRA_FLAG": "1"}, "pipeline", extra_env={"ALLOW_ENV_CHANGE": "1"})
    assert p.returncode == 0, p.stderr
    assert "gcloud run deploy pipeline" in calls
