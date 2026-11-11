#pragma once
/* Option B (plaintext SD): no recorder-side encryption, so
 * plaintext audio must live on the SD card as briefly as possible.
 *  - pg_wipe_file: overwrite a file's bytes with zeros, fsync, then unlink
 *    (FAT keeps deleted clusters readable until reused).
 *  - pg_backlog_level: cap on unsent audio. WARN past 30 min of audio or an
 *    oldest file over 4 h; BLOCK new recordings past 60 min or 24 h.
 * Pure C/stdio so it builds and tests on the host (test/host/test_pilot_guard.c). */
#include <stdbool.h>
#include <stdint.h>

#define PG_WARN_AUDIO_S (30 * 60)
#define PG_BLOCK_AUDIO_S (60 * 60)
#define PG_WARN_AGE_S (4 * 3600)
#define PG_BLOCK_AGE_S (24 * 3600)

typedef enum { PG_OK = 0, PG_WARN = 1, PG_BLOCK = 2 } pg_level_t;

/* 0 on success (bytes zeroed, synced and the file removed), -1 otherwise.
 * On failure the file may still exist; the caller must keep retrying. */
int pg_wipe_file(const char *path);

/* audio_s: seconds of finished-but-unsent audio. oldest_age_s: age of the
 * oldest unsent recording, or -1 when unknown (clock not set). */
pg_level_t pg_backlog_level(int64_t audio_s, int64_t oldest_age_s);
