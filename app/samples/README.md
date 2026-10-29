# Samples (synthetic only)

All fixtures here are **fictional**. First names + last initials only (except `sis-demo/`, which uses full invented names, example.com/.org emails and 555-01xx phones so the SIS import can be exercised). No real student PII, no live classroom audio in git.

| File | Purpose |
|---|---|
| `transcript_session_student_a_thu.json` | Speaker-diarized transcript + 2 IEP goals + incident flag timestamps |
| `expected_gemini_output_student_a_thu.json` | Schema-valid Gemini output for offline analyzer / UI |
| `ui_fixtures.json` | Teacher weekly matrix roster + sessions for the HITL web UI |
| `demo_roster.json` | The hosted demo's roster: **Everyday Heroes Academy** (fictional), built from `sis-demo/` by `app/tools/build_demo_roster.py`. Classrooms, recorder code aliases, teachers, RBTs, staff, students and IEP summaries. Don't edit by hand |
| `profiles/demo-school/demo_roster.json` | Goalvera's old one-student roster (RM12 → Marcus A., Sarah Jenkins). Saved for their setup after the LOU; the RM12 fixtures and tests use it |
| `pipeline/chirp3_output_rm12.json` | Real Chirp 3 output (speaker separation + word times) for the synthetic TTS clip made by `app/tools/make_sample_audio.py` |
| `pipeline/gemini_output_rm12.json` | Recorded Gemini 3.8 Flash draft for that clip (Sep 28 cloud run) |
| `sis-demo/` | **Fictional sample school, Everyday Heroes Academy** (87 students, 23 staff, 78 IEPs) in the SIS import layout (OneRoster 1.2 + add-on CSVs + IEP JSON/CSV). The demo roster is built from it. Staff credential numbers and pronouns are always empty (Ops). Includes fictional daily attendance Aug 3 – Oct 9, 2026 (`ext_attendance.csv`, codes in `ext_attendance_codes.csv`) |
| `pipeline/sidecar_rm12.json` | Sample recorder sidecar with two Mark-button markers |

Do **not** commit files under `samples/real/` (gitignored) or any `.enc` blobs with production keys.
