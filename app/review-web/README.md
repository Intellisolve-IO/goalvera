# Hosted review screen

`https://review.example.org` (Cloud Run service `review`, us-east1).
One container: the built React UI (`app/frontend-web`) and the review API
(`app/backend-pipeline/review_api/server.py --auth identity`).

- Sign-in: Google Cloud Identity Platform, email + password, self sign-up,
  email verification required. The server checks the ID token, the verified email
  and the allowlist (`config/review_allowlist.json` in the bucket) on every API call.
  Not approved yet -> "waiting for access" page, no data.
- Session list: everyone approved (admin, teacher, RBT) reads every classroom.
  A teacher's list opens filtered to their own classrooms (roster
  `teachers.*.emails`); an RBT's to `rbts.*.class_codes`. Filter choices come from
  the list the server returned.
- RBT (`rbt` on the allowlist) is read-only: no edit, approve, PDF export or
  classroom change; the server refuses them with 403 `READ_ONLY_ROLE`.
- Parent emails (the weekly parent email draft) are sent only to that student's
  teachers and admins; everyone else gets the session without them.
- Change classroom: the session's teacher (or an admin; any teacher for a session
  with no classroom yet) can move it to another roster classroom. Each move is an
  audit record (who, when, from, to); the session then belongs to that
  classroom's teacher. Not after approval.
- Approve / Download PDF: only the session's teacher or the student's own teacher
  (roster `teachers.*.emails`, `students.<id>.teacher_ids`) or a demo admin
  (allowlist role `admin`). Approval and audit records carry the
  signed-in uid and email; Cloud Logging gets only a one-way hash of the uid.
- Runs as its own service account `review-web` with bucket access limited to
  drafts/ transcripts/ approvals/ audit/ and two config files; it can create
  approvals and audit records but not overwrite or delete anything.
- Incidents: numbered in order within each recording, earliest first,
  "Incident 1 · 0:03" (h:mm:ss past an hour); the same number in the list, the
  session PDF and the incident print view (`review_api/incident_print.py`).
  Download PDF on an incident: same people as the session PDF (its teacher, the
  student's teacher or an admin; never an RBT or another teacher). Approved: the
  approved notes. Not approved yet: the stored draft, with a light
  diagonal "DRAFT" watermark on every page, "Draft, not yet approved by the teacher"
  under the title and a DRAFT_ prefix on the title / file name. The server
  (`POST /api/sessions/<id>/incident_print`) logs the click and returns only what may
  be printed: first name + last initial, never a school id, full last name or date of
  birth (scrubbed from the text; anything left makes it refuse with PRINT_BLOCKED).
  The browser makes the PDF: print-ready view, title "Incident-1_0-03_<class>_<date>"
  (= default file name), its own print dialog / Save as PDF. Every page's footer reads
  "Confidential, contains student information". Nothing rendered or stored server-side.
  Each click lands in the journal (`incident_pdf_downloaded`: uid, role, recording id,
  student id, incident number + offset, draft true/false), the bucket audit trail (`audit/<id>/..._incident_pdf_downloaded_*.json`,
  uid + role only, no email; draft flag) and Cloud Logging (hashed uid, recording id, incident number, draft);
  refused if the journal can't take it (503 JOURNAL_UNAVAILABLE).
  Offline click-through: `python app/tools/e2e_incident_pdf.py`.
- Unchanged: no email of notes, PDF rendered on request and never stored, every
  approval and download in the audit trail, 30-day auto-delete.

- Incident names + clock time (`review_api/incident_names.py`,
  `review_api/incident_print.py`). Each incident reads "Incident 1: <name>" with
  "1:02 PM · 12:34 into recording" under it (screen, incident print view, session PDF).
  - Name: 80 characters or fewer, written by Gemini (same Vertex client and PLANNER_AI setting as the
    planner; one small call per session for incidents without a name, on first view:
    `GET /api/sessions/<id>/incident_names`). Only the incident's own notes go, with student names ->
    "the student", staff -> "the teacher", emails / ids / DOB removed; never the transcript. privacy rule in
    the prompt: observed behavior only, no diagnoses, intent or judgment words; a blocked word
    (defiant, aggressive, angry, disruptive, manipulative, ... see BLOCKED) -> one regeneration -> still
    blocked -> plain "Incident N". The same name scrub as the report (student / staff names, emails,
    school ids, DOB, long numbers) -> plain "Incident N" if it fails. Stored create-only:
    `incident_names/<recording>/<content key>.json` (setup: `./infra/setup_incident_names.sh`).
    Teachers edit the name before approval ("Incident name" box; `name_source: teacher`, same
    approval / notes_edited log as other fields); a teacher's name is never overwritten, and goes
    through the same checks at approval (422 INCIDENT_NAME_NOT_ALLOWED).
  - Clock time: recorder start time + offset, shown in Eastern (America/New_York, DST-correct). Start
    time: the recorder's sidecar `start_time` (with its UTC offset; firmware strftime %z) when the
    pipeline kept it, else the file name's YYYYMMDD_HHMMSS (the firmware writes it in Eastern, only when
    its clock is set; otherwise NOTIME_... which the pipeline never accepts). Missing, `time_valid`
    false, before 2020 or inside the spring-forward gap -> offset only ("12:34 into recording").
    Incidents have a single time (no start/end), so no "lasted" length.
  - File name: `Incident-1_1-02PM_<class>_<date>` (offset instead of the clock without a start time),
    DRAFT_ prefix for drafts. Never the AI name.
- No per-session draft parent email: not on the session screen, not in the session PDF,
  not generated by the analyzer (prompt v3). Older drafts keep theirs in storage, hidden (approval
  copies it unchanged). The draft parent email is now part of the weekly student summary in the
  Lesson planner (AI draft + teacher edit + Save & approve; never sent; visible only to the
  student's teachers and admins; stored with the first name as "the student"; same transcript-quote check).
- Recorder Bug markers (firmware 0.6.7 writes `{"type": "bug", "offset_ms", "wall_time"}`): never a
  marker (hidden in any case: Bug / bug / BUG), never an incident or in a PDF. Each becomes a pending
  bug report once (`bug_reports/<recording>/bug_<start>_<hash>.json`, made when the session is
  listed / opened): note "Reported from recorder", screen "recorder", the recorder's device id,
  firmware version, recording start time and the clock time of the press (its wall_time, else start
  + offset). Shown as "Bug reported from recorder" to the session's teacher(s) and admins.

- Bug markers (`review_api/bug_reports.py`): "Bug" in the session's marker type
  dropdown (Markers section; teachers and admins, never RBTs). A note box with "Don't include
  student names; this goes to our support team." under it, the time in the recording and where on
  the screen. On submit the service files an issue in your-org/your-issues-repo (labels `bug` +
  `teacher-feedback`, created if missing; Team Board / Project 1 with Project = Goalvera).
  - The issue holds only: the note, the time, the screen, the app revision (+ commit) and recorder
    firmware (if the recording has one), the internal recording id and the reporter's role. Roster
    student first/last names in the note become `[student]` (staff names `[staff]`, emails `[email]`,
    school ids / DOB `[removed]`, 6+ digit numbers `[number]`) before anything is stored; a note that
    repeats 8+ words of the transcript is refused (422 QUOTES_TRANSCRIPT); if a forbidden value were
    still in the title or body nothing is filed (BUG_REPORT_BLOCKED).
  - Never a session marker: not in the markers line or timeline, the incident list or its
    numbering, the session PDF or the incident print view. The session shows a small "Bug reported"
    item (link to the issue once filed) to the reporter and admins only.
  - Stored in the bucket, create-only: `bug_reports/<recording>/<report>.json` (scrubbed report;
    pending), `.claim-*.json` (an instance is filing it), `.filed.json` (issue number + link).
    Not on the 30-day rule; removed with the rest of the school's data.
  - Journal: `bug_reported` (report id, `issue_status` pending, draft true/false; required, 503 if it
    can't be journaled) and `bug_report_filed` (issue number). Cloud Logging: `bug_reported` /
    `bug_report_filed` with ids only.
  - GitHub access: a fine-grained token in Secret Manager `github-issues-token` (the service gets
    the secret's name in `GITHUB_ISSUES_SECRET` and reads the latest version itself; never the PSA
    token, never anyone's gh login). Without a version the teacher still sees "Thanks, sent to our
    team." and the report stays pending; pending reports are filed on the next submission (the
    secret is re-checked at most once a minute) and on every instance start. No redeploy needed.
  - Setup (bucket access for `bug_reports/`, the empty secret, review-web may read it):
    `./infra/setup_bug_reports.sh`; read-only status: `./infra/setup_bug_reports.sh --check`.
  - The token (admin): github.com -> your picture -> Settings -> Developer settings -> Personal access
    tokens -> Fine-grained tokens -> Generate new token. Name `goalvera-review-bug-reports`;
    Resource owner **your org**; an expiry (e.g. 90 days); Repository access: Only select
    repositories -> your issues repo; Repository permissions: **Issues: Read and write** (Metadata: Read-only is
    added automatically); Organization permissions: **Projects: Read and write**. Nothing else. Then
    Google Cloud console -> project example-project -> Secret Manager ->
    `github-issues-token` -> New version -> paste the token -> Add new version.
  Offline click-through: `python app/tools/e2e_bug_marker.py`.

- Admin activity journal (`review_api/journal.py`): sign-ins, every
  list/session/audit view (an RBT's view is refused with 503 if it can't be
  journaled), edits, approvals, PDF downloads, classroom changes, refused requests,
  journal views and report downloads. Internal ids only (uid, recording id,
  internal student id); names are looked up only when an admin builds a report.
  Stored in this school's own BigQuery dataset (`goalvera_journal`, us-east1,
  CMEK); the app's account can only add rows. `JOURNAL_MODE` = off (default) /
  memory / bigquery; deploy turns it on once the table exists.
  - `GET /api/admin/journal?from=&to=&actor=&action=&student=&limit=` (admin only)
  - `GET /api/admin/reports/activity.csv?by=person|student&from=&to=` (admin only):
    first name + last initial only; the download is refused if it would contain an
    email, school id, full last name or date of birth; every download is journaled
    with the file's sha256.

Set up and deploy: `./infra/setup_review.sh` then `./infra/deploy_review.sh`.
Journal: `./infra/setup_journal.sh`, then `./infra/journal_backfill.py [--yes]`, then deploy.
Allowlist: `./infra/review_allowlist.py list|add|remove`.
Offline click-through: `python app/tools/e2e_hosted_review.py` (Firebase Auth emulator).
