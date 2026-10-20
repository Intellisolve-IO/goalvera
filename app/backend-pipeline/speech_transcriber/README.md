# speech_transcriber

Chirp 3 (Speech-to-Text v2, `us` multi-region) helpers used by the pipeline service.

- `chunking.py` — checks recorder format (16 kHz, mono, 16-bit WAV) and splits
  sessions longer than `CHUNK_SECONDS` (default 1140 s = 19 min; Chirp 3 allows
  ~20 min per file with word timestamps), cutting at the quietest 100 ms within
  15 s before each boundary.
- `turns.py` — converts BatchRecognize JSON into `speaker_turns`
  (`speaker`, `start_seconds`, `end_seconds`, `text`, `words[{w,s,e}]`), the same
  shape as `app/samples/transcript_session_student_a_thu.json` plus word times.

Speaker separation gives generic labels (`Speaker 1/2/3`). It does not identify
anyone and stores no voiceprints. Labels are per file, so parts of a split
session are labelled `Speaker N (part 2)`; a person maps labels in review.

The request itself is built in `../pipeline_service/main.py`
(`model="chirp_3"`, `diarization_config`, `enable_word_time_offsets`).
