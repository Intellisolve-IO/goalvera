#pragma once

#include "lvgl.h"
#include <stdint.h>

/*
 * Marker types: one big one-tap button each on the recording screen
 * (0.6.8,). Edit this one table to rename or add types (e.g.
 * "Incident" -> "Behavior Event"). Fixed list only; no free-text entry.
 *
 * label: the button text the teacher sees.
 * type:  what is written to the recording's .json sidecar under
 *        "markers": [{"type", "offset_ms", "wall_time"}].
 * icon:  LVGL symbol shown before the label.
 * color: button colour.
 * confirm_title / confirm_text: if set, the tap captures the time and then
 *        asks OK / Cancel; OK stores the marker at the tap time, Cancel
 *        stores nothing. NULL = one tap, stored at once.
 * "Incident" and "Teacher Notes" have always written their label verbatim,
 * and the cloud pipeline keys on those strings ("Incident" -> incident
 * flags), so their type must not change. "bug" (0.6.7) is a teacher's report
 * that something on the recorder or in this recording isn't working; the
 * review site files it for the support team and keeps it out of
 * incidents/PDFs. "Other" (and the UI-only "Photo") were dropped in 0.6.8;
 * older recordings may still carry "Other" markers.
 */
typedef struct {
    const char *label;
    const char *type;
    const char *icon;
    uint32_t color;
    const char *confirm_title;
    const char *confirm_text;
} marker_type_t;

static const marker_type_t MARKER_TYPES[] = {
    {"Incident", "Incident", LV_SYMBOL_WARNING, 0xC2410C, "Mark an incident?",
     "This flags the transcript with a marker in the saved recording and creates an incident report "
     "for the teacher to review and edit."},
    {"Teacher Notes", "Teacher Notes", LV_SYMBOL_EDIT, 0x2F6FE0, NULL, NULL},
    {"Bug", "bug", LV_SYMBOL_SETTINGS, 0x6D4AB8, "Acknowledge",
     "Use this to request a feature or report something that isn't working as expected. If you'd like "
     "a reply, say your name in the recording and the school team will reach out to you."},
};

#define MARKER_TYPE_COUNT ((int)(sizeof MARKER_TYPES / sizeof MARKER_TYPES[0]))

/* Sidecar type of Teacher Notes; tapping it also shows the phone upload QR (0.7.2). */
#define MARKER_TYPE_TEACHER_NOTES "Teacher Notes"

/* Sidecar type of the Bug marker; tapping it shows MARKER_BUG_TOAST. */
#define MARKER_TYPE_BUG "bug"
#define MARKER_BUG_TOAST "Bug noted, sent to support with this recording"
