# Deletion runbook — one session (demo project)

For a deletion request, or to remove a test recording. Covers checklist row 36
(partial: demo project only; the school project repeats this at go-live).

1. **Find the recording ID.** It is shown in the review screen title
   (`rec_<device>_<YYYYMMDD_HHMMSS>_<class>`), or derive it from the upload path
   `rec/<device>/<file>.wav`.
2. **Dry run:** `./infra/delete_session.py <recording_id>` lists every object
   (audio, sidecar, chunks, raw Speech-to-Text output, transcript, draft,
   approval, audit records).
3. **Delete:** `./infra/delete_session.py <recording_id> --yes`. It re-checks
   and prints `verified: all deleted`. Soft delete is off on the bucket, so
   nothing is recoverable afterwards.
4. **Teacher browser:** unapproved edits, fixture-tile approvals and speaker
   mappings live in the review laptop's browser storage. Click **Reset demo state** (clears all) on
   that laptop.
5. **Downloaded PDFs:** we never store PDFs, but a teacher may have downloaded
   one. Before deleting, check the audit trail
   (`GET /api/sessions/<id>/audit` on the review service, or
   `gcloud storage cat gs://<bucket>/audit/<id>/*`) for `pdf_downloaded`
   events, and ask that teacher to delete the file from their computer.
6. **Device:** a recorder deletes its SD copy only after a verified upload. If
   the upload never verified, the file is still on the SD card: remove it there.
7. **Logs:** nothing to do. Logs only hold IDs, timings and error codes (check
   with `./infra/check_logs.py`), and are kept 7 days.
8. **Services:** Speech-to-Text batch jobs don't keep the audio (we have not
   opted in to data logging). Vertex AI doesn't train on our prompts, but by
   default may cache prompt data in memory for up to 24 hours; turning that
   off at project level is an open go-live item (checklist row 22).
9. Record the date, recording ID and who asked in the deletion log.

Automatic deletion without anyone acting: audio after 7 days, transcripts and
drafts after 30 days (bucket lifecycle rules).

## Weekly lesson planner records ()

The planner keeps its own records under `planner/` in the same bucket. They are
**not** on the 30-day rule: they are kept until the school data is deleted .

- **Fields kept:**
  - weekly plans per classroom
  - weekly summaries per student, with the IEP check and an `ai_assisted` marker when they started from an AI draft
  - teacher notes per student

  Every save is a new version. The student's first name is stored as "the student".
- **AI drafts are never stored.** They are returned to the browser and only the teacher's saved version is kept. Nothing to delete for them on our side.
- **One student leaves the program:** run `./infra/delete_planner.py --student <student_id>` (dry run), then add `--yes`. This removes all their summaries and notes, every week and every version.
- **One classroom:** `./infra/delete_planner.py --classroom <code> --yes` removes that classroom's plans. Plans hold no student ID. Check the plan text for the student's first name and edit it out if it was written there in another form.
- **Pilot end:** `./infra/delete_planner.py --all --yes` removes everything under `planner/`.
- **Deleting a session** (`delete_session.py`) does not remove planner records. The weekly summary lists the session IDs it drew on (`source_sessions`), but holds no transcript text. To delete everything about a student, run both scripts.
- **Activity journal:** entries for planner views, saves and AI drafts (`planner_*`) hold IDs only and follow the journal's own retention.
- **Vertex AI:** with `PLANNER_AI=live`, drafts go to Gemini with names masked. The same caching note as point 8 above applies. The demo default is `offline`, a local stand-in that makes no AI call.
