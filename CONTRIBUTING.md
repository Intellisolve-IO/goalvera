# Contributing to Goalvera

Thanks for helping. Goalvera handles classroom audio and student records, so privacy rules come first.

## Ground rules
- **No real data, ever.** No recordings, transcripts, student or staff names, rosters, device IDs, Wi-Fi names or
  school identifiers in code, tests, fixtures, issues or screenshots. Use the fictional sample school in `app/samples/`.
- **No secrets.** Keys, tokens, `.pem` files, `infra/deploy.env` and service-account JSON stay out of git.
  Deploy settings come from the environment (`infra/deploy.env.example`).
- Keep the teacher in the loop: nothing the AI drafts reaches a parent or a record without teacher approval.

## Workflow
1. Fork and branch from `main`.
2. Run the tests:
   - Backend: `pip install -r app/backend-pipeline/requirements-pipeline.txt -r app/review-web/requirements.txt -r app/backend-pipeline/review_api/requirements.txt pytest && python -m pytest app/backend-pipeline/tests -q`
   - Frontend: `cd app/frontend-web && npm ci && npm test`
   - Firmware host tests: see `firmware/recorder-idf/README.md`.
3. Open a pull request describing what changed and how you tested it.

By contributing you agree your contribution is licensed under the Apache License 2.0 (see `LICENSE`).
