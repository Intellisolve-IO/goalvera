# Open Classroom Recorder

Open-source edge-to-cloud toolkit for **special education** classrooms: ambient or dictated session audio → speech-to-text → Gemini drafts (**IEP goal evaluations**, **ABC incident** notes, session summary) → **teacher human-in-the-loop (HITL)** web review → per-student **weekly summary + draft parent report** (never sent) and a weekly lesson planner.

> **MVP status (slice 1):** clickable teacher HITL path on **synthetic fixtures**. No live parent email send. No real student PII in this repo. Hardware / Terraform / live STT land in later phases — see [`../docs/mvp-plan.md`](../docs/mvp-plan.md) and [`docs/design-spec.md`](../docs/design-spec.md).

---

## Why this isn’t “just an AI use case”

| Spine | How it shows up |
|---|---|
| Ugly input → structure | Classroom audio / transcript → structured PLOP-style progress + ABC JSON |
| Confidence + cites | Evidence quotes from the transcript per IEP goal; ABC tied to incident flag timestamps |
| Human approve | Teacher edits + **Approve** before any parent-facing artifact leaves |
| SoR-shaped output | Audit-ready note shapes (ABC / goal progress / weekly parent draft) |
| Accountable | Admin Activity Log (Journal): who approved, exported or viewed an IEP, and when |

---

## Explicit MVP rails (read before demos)

- **Sample / synthetic only** until a school's data agreement and parent consent are in place.
- **HITL required** — UI Approve moves `pending_review` → `approved` (queued). It does **not** send email.
- **No auto-send** of parent email in early-Oct demo.
- **No real student PII in git** — fixtures use fictional first names + last initials.
- **No model training on school data**.
- **FERPA-minded path (planned):** device AES-CTR at rest → school-tenant GCP + KMS decrypt → STT → Gemini → Firestore; raw audio lifecycle purge (7-day stub). Live path gated by the school's legal review.
- Do **not** claim Partner / ACE badges from this repo.

---

## Repo layout

```
open-classroom-recorder/
├── firmware/                 # ESP-IDF (post-BOM; out of demo path)
├── cloud-terraform/          # GCS / KMS / Cloud Run / Firestore stubs (later)
├── backend-pipeline/
│   ├── decrypter/
│   ├── speech_transcriber/
│   └── gemini_analyzer/      # Prompt pack + schema + offline fixture runner ✅
├── frontend-web/             # Review site: Recordings, Students, Classes, Lesson planner, Domains, Teachers, Activity Log, Settings ✅
├── samples/                  # Synthetic fixtures only ✅
└── docs/
    ├── design-spec.md
    └── demo-script.md        # 5-minute Goalvera walkthrough
```

---

## Quickstart — teacher HITL UI (fixtures)

```bash
cd frontend-web
npm install
npm run dev
```

Open the Vite URL (default `http://localhost:5173`).

You should see the **Recordings** tab: recordings grouped by classroom, plus a **Samples**
group (fictional Marcus A. and Sophia L., week of Sept 21–25, 2026, each marked "Sample").
Click **Review** on a session → editable goal evals / ABC / session summary → **Save Draft** / **Approve** (nothing is emailed).
The **Students** tab lists the two sample students; their pages show the IEP, the Mon–Fri week
and a sample "Generate draft weekly parent report". The other tabs need the review service
(see "Tabs" below).

UI state persists in `localStorage` for rehearsal; an admin can use **Settings → Reset demo state** to start clean. Data is loaded from `samples/ui_fixtures.json` via a thin store shaped like future Firestore collections (`/teachers`, `/students`, `/recordings`).

---

## Quickstart — Gemini analyzer (offline default)

```bash
cd backend-pipeline/gemini_analyzer
python3 run_fixture.py --validate-only
python3 run_fixture.py --mode offline
```

- **offline (default):** loads `samples/expected_gemini_output_student_a_thu.json`, validates JSON Schema (design §6), prints wrapped result. No API key needed — preferred for CI/demo reliability.
- **auto:** calls Gemini if `GOOGLE_API_KEY` / `GEMINI_API_KEY` (or `VERTEX_AI=1` + `GOOGLE_CLOUD_PROJECT`) is set; otherwise offline. Default model `gemini-3.8-flash` (override with `GEMINI_MODEL`); Vertex location `us` (`GEMINI_LOCATION`).
- **live:** always calls Gemini (optional deps in `requirements.txt`).

Prompt version tag: `ocr-gemini-v1-2026-09` (see `prompt.py`).

---

## Cloud pipeline (sample audio only)

Upload service → CMEK bucket → Chirp 3 transcript with speaker separation →
Gemini draft notes (Vertex AI, `gemini-3.8-flash`) → teacher review screen.
See [`../docs/pipeline-demo.md`](../docs/pipeline-demo.md) and
[`../infra/README.md`](../infra/README.md).

---

## Tabs (review site)

Same header on every tab: the school logo (or its badge) and school name on the left, the
signed-in person on the right. Long names end in an ellipsis; the header never changes height.
Tabs in order; the last two are for demo admins only. Classes sits next to Students and Domains next to the Lesson planner.

| Tab | What it shows |
|---|---|
| **Recordings** | Recent recordings grouped by classroom (plus the Samples group). Open one to review, edit, **Save Draft** and **Approve** (nothing is emailed), then Download PDF. Incident names are AI-suggested from a short transcript excerpt after each marker, with roster names replaced. On the transcript timeline the reviewer says who each speaker is: the session's class first (its teachers, RBTs and students as first name + last initial, e.g. "Sneezy D."), then a search over every student and staff member, then Unknown speaker / Other adult / Other student. That choice stays in the reviewer's browser and is never sent to the AI (`review_api/speakers.py`, `GET /api/sessions/<id>/speakers`). |
| **Students** | Every student (teachers see all students and every IEP). A student page shows the IEP (read-only; each open is written to the Journal as "viewed IEP"), one school week Mon–Fri with previous/next and that student's daily summaries, and **Generate draft weekly parent report**: the weekly summary + draft parent email, built here only. The AI sees "the student", never the name; the draft is saved, never sent. |
| **Classes** | Every class: name, classroom, teacher(s), educational domain (from Domains), students and schedule (one or more meeting times: days, start, minutes). Opens on your classroom (admins: All). Admins add, edit and delete; a teacher edits the classes they teach (and stays on them). Each change is in the Activity Log. Everyday Heroes Academy's 32 classes are fictional sample data. |
| **Lesson planner** | Last / this / next week per classroom: goals, objectives, the week's plan, a teacher note per student, and a "Weekly summary & parent report →" link to the student page. |
| **Domains** | Educational methodology by subject, from the methodology reference table: Subject / Domain, Core Educational Methodology, Key Curricular Programs, Recommended Book / Guide, plus how the curriculum is delivered. Admins add, edit and remove (logged); teachers read. |
| **Teachers** | Every staff member (teachers, RBTs, BCBA, therapists, office) with classroom(s), default classroom and the role from their own staff record; RBTs show their supervising BCBA. Admins also see emails and each person's **sign-in email and app role** (Admin / Teacher / RBT / No access) and change them here (**Change**). |
| **Activity Log** | Admins: the Journal (newest first, Eastern time) and **Download report (CSV)**: the activity report by person or by student for a date range. (Before Oct 10 this was the Download button at the top right of every page; that button is gone.) |
| **Settings** | Admins: **School name** and **School logo** (shown in the top bar; the only editable settings), then the configuration (access, retention, classrooms, default classrooms, AI and records), read-only where the app can't change it, and **Reset demo state** (also restores the default name "Everyday Heroes Academy" and its badge). |

**School name and logo** (`review_api/branding.py`). Admins only; the server refuses anyone else
(403 `ADMIN_ONLY`) and teachers only see the result. The logo can be PNG, JPEG or SVG, up to 1 MB.
An SVG is cleaned and rendered to a PNG on the server (resvg), and the SVG itself is discarded, so
no script in an SVG can ever reach a browser. PNG and JPEG are decoded and re-encoded without any
metadata (EXIF/GPS/ICC/text). The logo is served as `image/png` or `image/jpeg` with `nosniff`.
Both are stored in the demo bucket under `branding/`, one create-only record per change (the
newest is current; review-web can't overwrite or delete; one-time IAM: `infra/setup_branding.sh`). Each change is a
`settings_changed` row in the Activity Log with the admin and the field (`school_name`,
`school_logo`, `school_logo_removed`, `reset`), never the file or the new text.
`app/tools/e2e_nav_stable.py` checks that a long name and a tall logo don't move the top bar.

**Domains and Classes** (`review_api/school_setup.py`). The seed is in the repo
(`app/samples/school-setup/domains.json` from the "Educational Methodology" reference table, and
`classes.json`, the 32 fictional OneRoster classes of `sis-demo/`), both rebuilt and checked by
`app/tools/build_school_setup.py --check`. Admin changes are saved in the bucket under `school_setup/`
(one create-only record per change; the newest is current; one-time IAM:
`infra/setup_school_setup.sh`); a save on top of a newer one is refused (409 `STALE_VERSION`).
A domain still used by a class can't be removed. Activity Log rows carry ids and counts only
(`domain_added/edited/removed`, `class_added/edited/removed`, `session_class_assigned`).
A recording's session page shows its **class**: suggested from the recording's classroom and the time
it started (Eastern) against the class schedules; the teacher (or an admin) confirms or picks another
class of that classroom. Classroom materials then tell the AI the class's **domain and methodology**
(e.g. "Reading & Phonics: Orton-Gillingham (OG) Approach ...") and nothing else new: no class name,
programs, book, schedule or student data. `app/tools/e2e_school_setup.py` screenshots both tabs.

**Students in this session** (`review_api/session_students.py`). Under the class on a
recording's page: the class's roster (the picked class, else the suggested one; else the classroom's
students) is offered ticked and marked "not confirmed"; nothing is linked until the teacher (or an
admin) clicks **Confirm N students** / **Save students**. Stored as an audit record
(`audit/<rid>/..._session_students_set_...`, ids only, latest wins) and journaled
(`session_students_set`: count, was it the suggestion). A linked session shows on each student's week
(Students tab, Lesson planner) and feeds the weekly summary, the weekly IEP check and the parent report.
Classmates' names are masked as "another student" in every planner AI call.

**Uploading / processing rows** (`review_api/uploads_status.py`). `GET /api/uploads/status`
lists recent uploads under `rec/` that have no notes yet, with the stage from the pipeline's
`work/<rid>/manifest.json` (Uploaded, Checking the audio, Transcribing, Drafting notes) or a clear
failure (not a WAV, unreadable audio, transcription failed, drafting failed, didn't start). The
Recordings tab shows them at the top and polls every 15 s while one is processing (every minute
otherwise); when one finishes the list reloads. One-time IAM: `infra/setup_demo_gaps.sh` (read the
manifests only, list `work/` and `rec/`; never the audio).

**Weekly parent email** (`review_api/parent_email.py`; a new outbound flow for the school's data agreement
amendment list). Recipients come from `app/samples/contacts/demo_guardians.json` (fictional, built by
`app/tools/build_demo_roster.py`, kept out of the roster): only the student's guardians with
"receives reports" on; the rest (e.g. non-custodial parents) are shown as excluded. After the teacher
approves the weekly report, the student page shows the recipients and exactly what will be sent;
**Send weekly email** sends it, one click per send, never automatically. **Demo redirect** (Settings,
admins, on by default, logged): every parent email goes to the teacher's own staff.example address
with "Demo: would have gone to <first name> at <masked address>" at the top; any other address is
refused. Sends are logged under `parent_email/sends/` (ids, address hashes) and in the journal
(`parent_email_sent` / `parent_email_send_failed`). Everything under `parent_email/` (send log and the
redirect setting records) is deleted after 12 months by a bucket lifecycle rule (age 365;
merged into the existing rules by `infra/setup_demo_gaps.sh`, which `--check` lists). If the redirect
setting record ages out, the setting falls back to its default (on).

**My profile** (`review_api/people.py`). The signed-in name at the top right opens My
profile for everyone (teacher, RBT, admin): display name, preferred name, phone, default classroom (only
classrooms they may use: a teacher's own, an RBT's roster classrooms, any for an admin), title and
credential type. Role and email are shown read-only with "Ask an admin to change this"; one flag,
`PROFILE_SELF_EDIT_ACCESS` (infra/config.sh, default `off`), lets people change their own role / email
later. Admins change anyone's app role and sign-in email on the Teachers tab. The email is the sign-in
key, so the allowlist entry moves with it: the old address loses access and the new one gets the same
role in the same record as the staff record's email change; the last admin can't be removed. The
roster file and `config/review_allowlist.json` stay as deployed / as `infra/review_allowlist.py` left
them; every change is one create-only record `people/state-<n>.json` with the whole state (record n+1
is created only once, so two saves can't both win; one-time IAM: `infra/setup_people.sh`), applied on
top by the allowlist, the email -> person mapping and the app's roster from the same snapshot (other
instances within 30 s). Activity Log: `profile_updated` (person id + changed field names, never values) and
`access_changed`: who made the change, the person id and the account's sign-in address,
`email` / `role`, the old and new role, and on a sign-in email change both the old and the new address
(the same values go into the `people/` record's `change`). Only `access_changed` may carry an address. A display or
preferred name joins the never-to-the-AI name lists. `app/tools/e2e_my_profile.py` checks all three roles
and screenshots My profile.

**Sign-in** (`review_api/auth.py`, `AuthGate.jsx`). Email + password, and **Sign in with Google**
(; on since Oct 11: the Google provider is enabled in Identity Platform and `IDP_PROVIDERS` in
`infra/config.sh` is `password,google.com`; set it back to `password` and redeploy to hide the button -
the server refuses tokens from any provider not listed). The server's checks, on the server, for every request and every provider: the account's
email must be verified; the full address must match a staff / allowlist address exactly
(case-insensitive; never a domain or partial match; entries that aren't full addresses match nobody);
access removed by an admin (Teachers tab, No access) or taken off the allowlist file is refused within
30 s. Sign-ins are journaled as `sign_in` with `sign_in_provider`. Status and the console steps:
`./infra/setup_google_signin.sh --check` (read-only).

**Sign-in accounts are created by an admin** (new sign-ups stay off in Identity
Platform). Nobody can create their own account, with a password or with Google; an admin creates it
ahead of time with `./infra/review_allowlist.py add <email>` (adds the person to the staff list and
creates the account if missing) or `./infra/signin_accounts.py create <email> [--name ...]`
(`check <email>` is read-only). Only addresses on the live staff list are accepted, an existing account
is never changed, and the account is email-only: no password, nothing linked, email not yet verified.
When the person uses Sign in with Google, Firebase attaches their Google identity to that same account
(staff.example mail is Google Workspace, so Google is authoritative for the address, and the project
keeps one account per email). For password sign-in instead, send them a reset link (Firebase console ->
Authentication -> Users -> Reset password). Changing a role or sign-in email on the Teachers tab does not
create an account; run `signin_accounts.py create` for the new address (the app's service account has no
permission to create users, on purpose). Alex Morgan (snowwhite@staff.example) has one since Oct 11.

**Activity report downloads** (CSV, Activity Log tab) are logged like PDF downloads: a
required journal entry (`report_downloaded`, rows, SHA-256, bytes) and a create-only audit record
`audit/_reports/<time>_report_downloaded_<id>.json` (who, staff email, sign-in provider, report, period,
rows, SHA-256, bytes). If either can't be written, the file isn't sent.

Every classroom list opens on the signed-in teacher's own (default) classroom, from the roster;
any other classroom or All can be picked. Admins start on All.

---

## Demo school: Everyday Heroes Academy

The hosted demo is a **fictional sample school**, Everyday Heroes Academy (no real student data;
sign-in only; nothing public). Every student, staff and IEP record carries the label
"Fictional sample data", shown as a small badge on each student page, each IEP and the Students
and Teachers lists.

| What | Where it comes from |
|---|---|
| 87 students (K–5), 8 classrooms (Rooms 101–108), 8 teachers, 5 RBTs, 10 other staff, 78 IEPs, 5 Section 504 plans | `samples/sis-demo/` (SIS import layout; `tools/build_sis_demo.py`, checked by `tools/validate_sis_demo.py`) |
| The review site's roster | `samples/demo_roster.json`, built from `sis-demo/` by `tools/build_demo_roster.py` (`--check` in the tests). Do not edit it by hand. Staff are one record each with the role on the record (person schema): teachers under `teachers` (classrooms point at them), RBTs and everyone else under `staff` with `role` (`rbt`, `bcba`, `slp`, ...) and `supervisor_id` (RBTs: BCBA Mary Poppins). `common/roster.py` `staff_directory()` reads both this and older rosters that still have a separate `rbts` list. |
| Room 104 | Alex Morgan (lead teacher; her default classroom), RBT Flynn Rider (the demo RBT sign-in), Doc, Grumpy, Happy, Sleepy, Bashful, Sneezy and Dopey Dwarf plus 3 classmates |
| Attendance | Fictional daily attendance Aug 3 – Oct 9, 2026 in `sis-demo/ext_attendance.csv` (codes P / AE / AU / T in `ext_attendance_codes.csv`). The roster keeps only a per-student summary (days, present, absences, tardies, rate), shown as one line under the student's name. Not sent to the AI. Free/reduced lunch is not in the data; health is a flag only. |
| Name and badge | `review_api/branding.py` profile `everyday-heroes`: name, "EH" badge and a built-in shield logo (`review_api/assets/everyday-heroes-badge.png`, drawn by `tools/make_school_badge.py`). Admins can still change both on Settings. |
| Demo School | Kept as a saved profile, not shown: name "Demo School", "DS" badge, any name/logo an admin set before Oct 10, and their old roster in `samples/profiles/demo-school/demo_roster.json`. Switch with `SCHOOL_PROFILE=demo-school` (`infra/config.sh`) and that roster. |

The roster holds only what the site needs: first name, last name (only so the name scrub catches it;
screens show the last initial), grade, classroom, teachers and an IEP / 504 summary. Never birth
dates, school or state ids, guardians, addresses, health flags, **pronouns** (never sent to any
AI request) or staff credential numbers (optional and empty in the sample data).

Many students per classroom (`backend-pipeline/common/roster.py`): `roster.students.<id>` lists
each student's `class_codes`; the old one-student-per-class-code shape (`class_codes.<code>.student_id`)
still works. Recorder class codes match a classroom or one of its `aliases`.

**Old recordings** are read through the new roster, never rewritten: RM12, Reading 3A, Math 2B,
Circle Time, Room B and Hall (the old demo's codes, all the one demo teacher's) show in Room 104 with
Alex Morgan; a student who isn't in the new school (Marcus A.) is shown without a student link. Full
table: [`docs/demo-script.md`](../docs/demo-script.md#recording-remap-oct-10-2026).

AI incident names still never carry a name: every student and staff name in the roster (Doc,
Grumpy, Alex Morgan, Mary Poppins…) is masked in the transcript excerpt and rejects a name that
contains it, and a capitalized word that isn't on the allowlist sends it back once, then to
"Incident N" (`tests/test_sample_school.py`).

---

## Demo script

See [`docs/demo-script.md`](../docs/demo-script.md) for a 5-minute walkthrough aimed at school director rehearsal.

---

## Non-goals (early-Oct)

- Live parent email delivery (SendGrid/Gmail)
- Multi-teacher production roster
- Secure Boot / Flash Encryption production hardening (Phase 3+)
- District IT clearance / real enrollment data

---

## License / publish

Apache License 2.0. See `LICENSE` and `NOTICE`.
