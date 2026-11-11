# Security policy

Goalvera processes classroom audio and student information, so we take reports seriously.

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab -> "Report a vulnerability") on this repository. Include steps to reproduce and impact.
We aim to acknowledge within 5 business days. Project homepage: https://goalvera.app

## Scope highlights
- Recorder SD encryption (per-recording AES-256-GCM, key wrapped with an RSA public key held in Cloud KMS).
- Upload API device tokens, the review service's sign-in and role checks, and the pipeline's handling of PII.

## Operator responsibilities
Each deployment runs in the operator's own Google Cloud project. Operators own their keys, retention settings,
access lists and compliance (for example FERPA / state student-privacy law). Never put real student data in issues.
