# Changelog

What changed on `main`, newest first. One line per change, with the PR and card. Deploys are
made only from `main`; the deployed commit is shown on the review site's Settings tab.

## 2026-11-12

- **Teacher Notes QR setup**: `infra/setup_media.sh` now works for any configured project (it was locked to a placeholder demo project, so a rebuilt preview never got the `media/` and `media_tokens/` grants and Teacher Notes hit a 500 on `/v1/media/token`). It is called from `infra/setup.sh`, documented in `docs/PREVIEW.md`, keeps 7-day deletion for those prefixes, merges CORS instead of replacing it, and no longer grants upload-api access to `branding/` (the school-name fetch was removed in 0.8.2).
- **Recorder 0.8.2** (Hosyond 3.5"): the header is always the goalvera logo (from the www.goalvera.app homepage) on home and session screens; no school name is shown or fetched (`school_name` / `badge_text` / admin setting / `CONFIG_RECORDER_BRAND_*` ignored or removed). New USB serial console: `status`, `queue`, `queue wipe YES`, `uploads hold|resume`, `screenshot` (`tools/screenshot.py` turns the dump into a PNG). A token compiled into a local build now wins over an older NVS token. `tools/build-with-wifi.sh` takes the upload URL, device id, token file, an optional start-with-uploads-held flag and an SD-encryption switch from env (never committed). The battery log prints the USB charging estimate instead of `-1%`.

## 2026-11-02

- **iPhone web recorder**: a Record page in the review site for teachers and admins only (reviewers, RBTs, and unknown roles are read-only). Discard deletes that session's `web/` pieces. A real school sets `WEB_DEMO_RECORDER=off`. Safari records AAC in about 60-second pieces, holds them in IndexedDB only until the server confirms each one, then deletes them. Uploads are HTTPS only (no encryption on the phone). A lock, app switch, or call pauses the same session and marks the gap. The upload service adds `/v1/web/uploads` and `/v1/web/confirm`. ffmpeg joins the pieces into a 16 kHz mono 16-bit WAV under `rec/web-<user>/`, and the existing transcript path runs. The Hosyond 3.5" board stays the only supported recorder hardware. The demo shows a sample lesson and a reminder not to record real students. Setup is `infra/setup_web_recorder.sh`; nothing here is deployed. Test plan: `docs/iphone-web-recorder.md`.

## 2026-10-29

- **Goalvera**: the product name is Goalvera. The saved school profile is Demo School (badge DS). The demo lead teacher is Alex Morgan; the recorder's sample rooms are Room 104, Room B, and Hall. Recorder sample data is 0.8.1. See `docs/open-source-readiness.md` and `docs/demo-hosting-cost.md`.

## 2026-10-28

- **Recorder 0.8.0** (Hosyond 3.5"): brings the audio-only build and encryption onto main, and adds signed OTA. 0.7.9 wipes the SD copy after a verified upload, caps unsent audio, and can build audio-only (`sdkconfig.defaults.pilot`). Hosyond recordings are encrypted on the card (APENC1; private key in Cloud KMS). Flash and NVS encryption stay opt-in (`sdkconfig.defaults.secure`). OTA uses the existing `ota_0` / `ota_1` / `otadata` slots, checks a signed manifest in the firmware bucket at boot and every 6 hours only while idle, and rolls back unless the new image reaches the upload API. The home screen shows the version. Wi-Fi passwords are not in the image defaults. Setup commands (not run from this change) are in `firmware/recorder-idf/docs/ota.md`.

## 2026-10-19

- **Recorder 0.7.8**: tapping Teacher Notes shows an intro screen first (ask for a worksheet, add to the lesson plan, or note anything out loud; "Add photos/videos" shows the QR, "Done" goes back). The QR link starts fetching in the background the moment Teacher Notes is tapped, so the code is ready when asked for, and is dropped if it is never asked for. (Goalvera demo; PR TBD.)

## 2026-10-14

- **Teacher Notes photo / video from a phone**: recorder 0.7.2 shows a QR code
  on every Teacher Notes tap. It opens a no-sign-in phone page (upload-api `/u/<token>`) for one photo or one
  video up to 30 s; the token is minted with the device's upload key, tied to the current recording, expires
  after 5 minutes and is spent by the first upload. Uploads go to `media/<recording_id>/` in the demo bucket
  (signed create-only PUT). Photos go to the session analysis as images under the same no-names rules
  (prompt v4; roster names scrubbed from the AI photo notes); review shows **Teacher Notes photos and
  videos** with the AI photo notes. Video AI notes not yet; the parent email never includes either.
  One-time setup `infra/setup_media.sh`; cleanup `infra/delete_demo_media.sh`.

## 2026-10-11

- **Sign in with Google switched on**: the Google
  provider is enabled in Identity Platform; `IDP_PROVIDERS` is now `password,google.com`, so the sign-in page shows the
  button and the page allows Google's sign-in popup (CSP). Password sign-in unchanged; new sign-ups stay off
  (an admin creates accounts: `infra/signin_accounts.py`).
- **Sign-in accounts created by an admin**: new sign-ups stay
  off; `infra/signin_accounts.py` (`check` / `create`) creates an email-only Identity Platform account (no
  password) for an address on the live staff list, never changing an existing one, and
  `infra/review_allowlist.py add` now does it too (`--no-signin-account` to skip). Sign in with Google
  attaches to that account. Created for Alex Morgan (snowwhite@staff.example).
- **Sign in with Google, access logging, 12-month parent email retention** : "Sign in with Google" on the sign-in page, shown once the Google provider is enabled
  (`IDP_PROVIDERS`, default `password`; `infra/setup_google_signin.sh --check`); the server accepts only
  verified emails that match a staff / allowlist address exactly (case-insensitive) and refuses removed
  access and providers that aren't switched on; sign-ins journaled with the provider. `access_changed`
  now logs the old and new role, the account's sign-in address and, on an email change, both addresses
  (that action only). Activity-report CSV downloads get an audit record like PDF downloads (plus bytes in
  the journal). Bucket lifecycle: `parent_email/` deleted after 365 days, merged into the existing rules
  (`infra/setup_demo_gaps.sh`, `infra/lifecycle_merge.py`; also in `infra/setup.sh`).

## 2026-10-10

- Fix (infra): `infra/setup_demo_gaps.sh` now adds two combined objectViewer bindings instead of five
  (GCS allows 20 conditional bindings per role and member; review-web was at the limit).
- **Demo gaps**: **Students in this session** on a
  recording's page (class roster suggested, one click confirms; saved as an audit record and journaled;
  linked sessions show on each student's week and feed the weekly summary, IEP check and parent report;
  classmates masked as "another student" for the AI). **Uploading / processing rows** on Recordings,
  polled until the notes are ready, with clear failure states (`infra/setup_demo_gaps.sh`). **Send weekly
  email** on the student's approved weekly report: only guardians with "receives reports" on, the exact
  text shown first, one teacher click per send, never automatic; demo redirect (on by default, admin-only,
  logged) sends every parent email to the teacher's own staff.example inbox with a "would have gone to"
  banner. Fictional guardians in `app/samples/contacts/demo_guardians.json` (two non-custodial contacts
  added to the SIS sample with the flag off).
- **My profile**: the signed-in name at the top right opens My profile, where
  teachers, RBTs and admins edit their own display name, preferred name, phone, default classroom (only
  their own classrooms), title and credential type; role and email are read-only ("Ask an admin to change
  this"; flag `PROFILE_SELF_EDIT_ACCESS`, default off). Admins change anyone's app role / sign-in email on
  the Teachers tab; the allowlist entry moves with the email in one record; the last admin stays.
  Stored as create-only `people/` records over the roster and allowlist (`infra/setup_people.sh`); journaled
  as `profile_updated` / `access_changed` with field names only. **The Download button at the top right is
  gone**: it was the only way to the activity report CSV, which is now "Download report (CSV)" on the
  Activity Log tab.
- Fix (privacy): roster name tokens of 1-2 letters (e.g. "Li" in Shang Li) are now masked
  before every AI call too (materials, incident names, planner and weekly-report drafts, transcript
  analysis) and blocked in bug reports. Short tokens match only as capitalized whole words, case-sensitive
  ("Li", "LI"; not "li", "Lila" or "split"); "A" / "I" / "O" only as initials ("A."). Longer names keep the
  case-blind whole-word match. One shared matcher: `common/namematch.py` (tests: `test_short_names.py`).
- **Speaker selector looks up the roster**: on the transcript timeline, "who is
  this?" now lists the session's class first (its teachers / RBTs and students, first name + last initial),
  then searches every student and staff member, then Unknown speaker / Other adult / Other student
  (replaces the fixed Teacher / Aide / RBT / ... list). The choice still stays in the reviewer's browser and
  never goes to the AI. **RBTs are staff records** with `role: "rbt"` and their supervisor (BCBA Mary
  Poppins), not a separate list; they show on the Teachers tab with "supervised by". RBT sign-in scope is
  unchanged (read-only, own classroom); older rosters with an `rbts` list still work.
- **Domains** and **Classes** tabs: Domains lists the educational methodology
  per subject from the methodology reference table (6 domains: Subject / Domain, Core Educational Methodology, Key
  Curricular Programs, Recommended Book / Guide, plus the 3 delivery formats); Classes lists each class
  with teacher(s), domain, students, classroom and schedule (several meeting times allowed), seeded with
  the 32 fictional Everyday Heroes Academy classes. Admins add / edit / remove (Activity Log); teachers
  read Domains and edit their own classes. A session's class is suggested from its classroom + start
  time and confirmed by the teacher; classroom materials then get the class's domain name and
  methodology (only that; no student data). Seed in `app/samples/school-setup/`
  (`app/tools/build_school_setup.py`), saved changes under `school_setup/` (`infra/setup_school_setup.sh`).
- Fix: generic placeholders ("Student", "Student 2", "Demo Teacher", "Unknown", ...) no longer
  count as names in the bug-report privacy check, so recorder Bug reports on recordings with a
  placeholder student are filed instead of held. A value with any real word ("Student Marcus",
  "Pat Speech") is still a name, and real roster names still block the issue (tests).
- Fix (live smoke): the Recordings list failed (502) on the hosted site after the
  Everyday Heroes switch. An old recording without markers looked for its recorder sidecar under the
  classroom it now shows as ("..._Room 104.json", a name with a space) instead of its file's own code
  ("..._RM12.json"). It now uses the file's code, and an unreadable sidecar name never fails the list.
- Fictional **attendance** for Everyday Heroes Academy: daily records Aug 3 –
  Oct 9, 2026 (school days; Labor Day off) with codes Present / Absent, excused / Absent, unexcused /
  Tardy in `sis-demo/ext_attendance.csv` + `ext_attendance_codes.csv` (4,263 rows, each labelled
  "Fictional sample data", checked by `validate_sis_demo.py`). The roster carries only a per-student
  summary, shown as one line on the student page. Attendance is never sent to the AI.
- Demo school is now **Everyday Heroes Academy** (fictional): top bar name,
  "EH" badge and a built-in shield logo; the review site's roster (`app/samples/demo_roster.json`) is
  built from `sis-demo/` by `app/tools/build_demo_roster.py`: 87 students, Rooms 101-108, 23 staff,
  IEP / 504 summaries, a "Fictional sample data" label on every profile and IEP. Many students per
  classroom on Students, Teachers (all staff), Settings, Lesson planner and default classrooms
  (Room 104 / Alex Morgan). Old recordings are read through the new roster (RM12, Reading 3A, Math 2B,
  Circle Time, Room B, Hall -> Room 104), nothing rewritten. Goalvera's name, badge and roster
  are kept as a saved profile (`SCHOOL_PROFILE=demo-school`). The hosted site no longer shows the
  laptop Samples. New name-safety and no-pronoun AI tests (`tests/test_sample_school.py`).
- Sample data review: staff credential numbers are optional and empty; pronouns stay
  empty and are never sent to the AI; school name set in `orgs.csv`. PR #23.
- Fictional sample school dataset in `app/samples/sis-demo/`: 87 students, 23 staff, 217
  contacts, 78 IEPs (full Georgia-style IEPs for the seven focus students), in the OneRoster 1.2 +
  add-on CSV + IEP JSON/CSV layout from `docs/sis-schema.md`. Generator `app/tools/build_sis_demo.py`,
  validator `app/tools/validate_sis_demo.py`. Stand-alone files; nothing loaded into the app.

## 2026-10-09

- Recorder firmware 0.7.1 (Hosyond only): the top-right chip is now a battery gauge
  with no number (fill bar in proportion to %, green above 25%, yellow 10-25%, red below 10%),
  a lightning bolt over it on USB power (computer connected or charging detected; empty outline
  + bolt with no battery). The low-battery warning moves to below 10%; 5% auto Save & finish is
  unchanged.
- Recorder firmware 0.7.0 (, Hosyond 3.5" only; CrowPanel unchanged apart from the
  version): battery gauge in the top-right chip (battery % from GPIO8, red at 15%, "Charging"
  guessed from the voltage, "USB power" when a computer is connected), battery voltage in the
  serial log every 30 s, 15% warning, auto Save & finish at 5% (`RECORDER_BATTERY_AUTOSAVE`,
  default on), auto sleep after 60 idle minutes (`RECORDER_AUTO_SLEEP_MIN`) with a 30 s "tap to
  stay on" warning, running upload finished first, BOOT button to wake. CI now also builds the
  Hosyond and runs the firmware host tests.
- School name + logo storage: one create-only record per change under `branding/` (review-web
  can't overwrite or delete), with `infra/setup_branding.sh` for its bucket access.
- School name + logo: admins set them on Settings > School; shown in the top bar on
  every tab (default Demo School + DS badge; Reset demo state restores). SVG logos are rendered
  to PNG on the server and the SVG discarded; PNG/JPEG re-encoded without metadata. Changes are
  in the Activity Log. Settings shows the running commit; `deploy_review.sh` deploys only a clean
  `origin/main` and stamps the revision (APP_COMMIT, label `commit=`).
- Repo hygiene: pull request template (tests, docs, firmware version, deploy-from-main,
  student-data question), backend + frontend test workflow on every PR and push to `main`,
  this changelog, and branch protection on `main`.
- Merged the open stack into `main` (#1–#12, #14, #15): recorder firmware 0.6.9 for both boards,
  pipeline backend, hosted review site, speaker timeline, lesson planner, upload cold-start fix,
  Journal read fix, dictation, incident numbering and PDF, Bug markers, AI incident names.
