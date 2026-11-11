"""infra/deploy_review.sh with stubbed gcloud / bq / curl (nothing reaches the cloud):
the journal is never switched off, and a deploy never lists or reads API keys;
planner AI drafts go live only when review-web can reach Vertex AI, and the fictional roster
is re-uploaded only when it changed."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "deploy_review.sh"
ROSTER_MD5 = base64.b64encode(hashlib.md5((ROOT / "app/samples/demo_roster.json").read_bytes()).digest()).decode()
SA = "serviceAccount:review-web@example-project.iam.gserviceaccount.com"
WITH_VERTEX = {"bindings": [{"role": "roles/aiplatform.user", "members": [SA]}]}
NO_VERTEX = {"bindings": [{"role": "roles/run.invoker", "members": [SA]},
                          {"role": "roles/aiplatform.user", "members": ["serviceAccount:pipeline@x"]}]}
LIVE = {"spec": {"template": {"spec": {"containers": [{"image": "img@sha256:old", "env": [
    {"name": "JOURNAL_MODE", "value": "bigquery"},
    {"name": "IDP_API_KEY", "valueFrom": {"secretKeyRef": {"name": "review-web-api-key", "key": "latest"}}}]}]}}}}

GCLOUD = r"""#!/usr/bin/env bash
echo "gcloud $*" >> "$CALLS"
case "$*" in
  "projects describe"*) echo 123456789 ;;
  "run services describe"*"--format=json"*) cat "$LIVE_JSON" ;;
  "run services describe"*) echo https://review-x-ue.a.run.app ;;
  "projects get-iam-policy"*) cat "$IAM_JSON" ;;
  "storage objects describe"*) echo "$ROSTER_MD5" ;;
  "auth print-access-token"*) echo tok ;;
  "config get-value account"*) echo someone@example.org ;;
esac
exit 0
"""


def run(tmp_path, bq_ok: bool, args=(), planner_ai=None, iam=WITH_VERTEX, roster_md5=ROSTER_MD5, main_ok=True):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    for name, body in {
        "gcloud": GCLOUD,
        "bq": f'#!/usr/bin/env bash\necho "bq $*" >> "$CALLS"\n'
              + ("exit 0\n" if bq_ok else 'echo "Access Denied: bigquery.tables.get denied" >&2; exit 1\n'),
        "curl": '#!/usr/bin/env bash\necho "curl $*" >> "$CALLS"\necho "{}"\n',
    }.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    (tmp_path / "iam.json").write_text(json.dumps(iam))
    (tmp_path / "live.json").write_text(json.dumps(LIVE))
    env = {**os.environ, "PROJECT_ID": "example-project", "REVIEW_DOMAIN": "review.example.org", "GITHUB_ISSUES_REPO": "your-org/your-issues-repo", "MAILER_SENDER_MAILBOX": "snowwhite@staff.example", "PATH": f"{bin_}:{os.environ['PATH']}", "CALLS": str(calls),
           "IAM_JSON": str(tmp_path / "iam.json"), "LIVE_JSON": str(tmp_path / "live.json"), "ROSTER_MD5": roster_md5}
    env.pop("PLANNER_AI", None)
    env.pop("ALLOW_NON_MAIN", None)
    # deploys only from main: these tests run from a branch checkout, so point the check at HEAD
    env["DEPLOY_MAIN_REF"] = "HEAD" if main_ok else "0" * 40
    if planner_ai is not None:
        env["PLANNER_AI"] = planner_ai
    p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)
    return p, calls.read_text()


def test_journal_check_failure_stops_before_anything_changes(tmp_path):
    p, calls = run(tmp_path, bq_ok=False)
    assert p.returncode == 1
    assert "refusing to deploy" in p.stderr and "JOURNAL_MODE=bigquery" in p.stderr
    assert "run deploy" not in calls and "storage cp" not in calls


def test_refuses_to_deploy_anything_but_main(tmp_path):
    p, calls = run(tmp_path, bq_ok=True, main_ok=False)
    assert p.returncode == 1 and "Deploys only from main" in p.stderr
    assert "run deploy" not in calls and "storage cp" not in calls


def test_deploy_is_stamped_with_the_commit(tmp_path):
    p, calls = run(tmp_path, bq_ok=True)
    assert p.returncode == 0, p.stderr
    deploy = next(line for line in calls.splitlines() if line.startswith("gcloud run deploy"))
    import re as _re
    m = _re.search(r"--update-labels=commit=([0-9a-f]{12}(-dirty)?)", deploy)
    assert m, deploy
    assert f"APP_COMMIT={m.group(1)}|" in deploy


def test_deploy_uses_the_secret_and_keeps_the_journal_on(tmp_path):
    p, calls = run(tmp_path, bq_ok=True)
    assert p.returncode == 0, p.stderr
    deploy = next(line for line in calls.splitlines() if line.startswith("gcloud run deploy"))
    assert "--set-secrets=IDP_API_KEY=review-web-api-key:latest" in deploy
    assert "JOURNAL_MODE=bigquery" in deploy and "JOURNAL_MODE=off" not in deploy
    assert "IDP_API_KEY=" not in deploy.split("--set-env-vars=", 1)[1]
    assert "api-keys" not in calls


def test_dry_run_changes_nothing(tmp_path):
    p, calls = run(tmp_path, bq_ok=False, args=("--dry-run",))
    assert p.returncode == 0, p.stderr
    assert "Nothing was changed" in p.stdout and "[cannot] journal table check" in p.stdout
    for verb in ("run deploy", "storage cp", "api-keys", "secrets"):
        assert verb not in calls


def _deploy_line(calls):
    return next(line for line in calls.splitlines() if line.startswith("gcloud run deploy"))


def test_planner_ai_live_sets_the_pipelines_gemini_settings(tmp_path):
    p, calls = run(tmp_path, bq_ok=True)                       # config.sh default: PLANNER_AI=live
    assert p.returncode == 0, p.stderr
    env = _deploy_line(calls).split("--set-env-vars=", 1)[1]
    for kv in ("PLANNER_AI=live", "VERTEX_AI=1", "GOOGLE_CLOUD_PROJECT=example-project",
               "GEMINI_LOCATION=us", "GEMINI_MODEL=gemini-3.8-flash", "JOURNAL_MODE=bigquery",
               "SCHOOL_PROFILE=everyday-heroes",                 # the demo school (Oct 10)
               "PARENT_EMAIL_REDIRECT_DEFAULT=on",               # parent emails go to the teacher
               "IDP_PROVIDERS=password,google.com|"):            # Google sign-in on (provider enabled Oct 11)
        assert f"|{kv}" in env or f"^{kv}" in env
    assert "add-iam-policy-binding" not in calls                # checks access, never grants it


def test_planner_ai_live_without_vertex_access_stops_before_anything_changes(tmp_path):
    p, calls = run(tmp_path, bq_ok=True, iam=NO_VERTEX)
    assert p.returncode == 1
    assert "refusing to deploy with PLANNER_AI=live" in p.stderr and "roles/aiplatform.user" in p.stderr
    assert "run deploy" not in calls and "storage cp" not in calls and "add-iam-policy-binding" not in calls


def test_planner_ai_off_deploys_without_vertex_settings(tmp_path):
    p, calls = run(tmp_path, bq_ok=True, planner_ai="off", iam=NO_VERTEX)
    assert p.returncode == 0, p.stderr
    env = _deploy_line(calls).split("--set-env-vars=", 1)[1]
    assert "|PLANNER_AI=off" in env and "VERTEX_AI" not in env and "GEMINI_MODEL" not in env


def test_planner_ai_bad_value_refused(tmp_path):
    p, calls = run(tmp_path, bq_ok=True, planner_ai="on")
    assert p.returncode == 2 and "run deploy" not in calls


def test_roster_uploaded_only_when_it_changed(tmp_path):
    p, calls = run(tmp_path, bq_ok=True)
    assert p.returncode == 0 and "storage cp" not in calls and "not re-uploaded" in p.stdout
    p, calls = run(tmp_path, bq_ok=True, roster_md5="different==")
    assert p.returncode == 0 and "storage cp" in calls


def test_dry_run_shows_the_settings_that_would_change(tmp_path):
    p, calls = run(tmp_path, bq_ok=True, args=("--dry-run",), iam=NO_VERTEX)
    assert p.returncode == 0, p.stderr
    assert "PLANNER_AI: (unset) -> live" in p.stdout and "GEMINI_MODEL: (unset) -> gemini-3.8-flash" in p.stdout
    assert "[cannot] planner AI live" in p.stdout
    changes = p.stdout.split("Settings vs the live service", 1)[1].split("unchanged:")[0]
    assert "IDP_API_KEY" not in changes and "JOURNAL_MODE" not in changes   # secret + journal stay as they are
    for verb in ("run deploy", "storage cp", "api-keys", "add-iam-policy-binding"):
        assert verb not in calls


def test_bug_reports_get_the_secret_name_only_and_never_block_a_deploy(tmp_path):
    """: the service gets the GitHub token's secret NAME and reads it itself at run
    time; the deploy never touches Secret Manager, so a missing token can't stop it."""
    p, calls = run(tmp_path, bq_ok=True)
    assert p.returncode == 0, p.stderr
    env = _deploy_line(calls).split("--set-env-vars=", 1)[1]
    assert "|GITHUB_ISSUES_SECRET=github-issues-token" in env and "|GITHUB_ISSUES_REPO=your-org/your-issues-repo" in env
    assert "--set-secrets=IDP_API_KEY=review-web-api-key:latest" in _deploy_line(calls)   # no second secret mounted
    assert "gcloud secrets" not in calls and "build: commit " in p.stdout
