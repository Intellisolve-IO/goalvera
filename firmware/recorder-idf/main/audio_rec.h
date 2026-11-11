#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>
#include <time.h>

typedef enum {
    AUDIO_IDLE = 0,
    AUDIO_STARTING,
    AUDIO_RECORDING,
    AUDIO_PAUSED,
    AUDIO_SAVING,
    AUDIO_ERROR,
} audio_state_t;

typedef struct {
    char classroom_code[32];
    char classroom_label[48];
    char teacher[48];
    char class_code[48];
} rec_labels_t;

typedef struct {
    audio_state_t state;
    bool time_valid;
    struct tm start_local;
    uint32_t recorded_ms;
    uint8_t level;
    char err[80];
    uint32_t err_seq; /* bumped on every new error */
    char path[160];
} audio_status_t;

#define AUDIO_MAX_MARKERS 64

/* A marker captured when Mark is pressed (not when OK is pressed). */
typedef struct {
    uint32_t offset_ms;  /* position in the recording's audio */
    bool wall_valid;
    time_t wall;         /* wall clock at the press (if the clock is set) */
} audio_mark_time_t;

esp_err_t audio_init(void);
/* Stop capturing immediately (RECORDING -> PAUSED) without a queue hop.
 * Returns true if it was recording. Used by Pause and by End. */
bool audio_rec_hold(void);
/* Snapshot "now" for a marker. Returns false if no session is active. */
bool audio_rec_mark_now(audio_mark_time_t *out);
/* Store a marker for the current session (goes into the .json sidecar). */
esp_err_t audio_rec_add_marker(const char *type, const audio_mark_time_t *t);
esp_err_t audio_rec_start(const rec_labels_t *labels);
esp_err_t audio_rec_pause(void);
esp_err_t audio_rec_resume(void);
esp_err_t audio_rec_save(const rec_labels_t *labels);
void audio_get_status(audio_status_t *out);
