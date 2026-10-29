# Cloud setup

The demo backend (tasks#35) is scripted with `gcloud` in [`../../infra/`](../../infra/README.md)
(`setup.sh`, `deploy.sh`). If we move to Terraform for the school's project,
translate those scripts 1:1 — they list every resource and IAM binding.
