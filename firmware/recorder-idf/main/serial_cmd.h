#pragma once

/* USB serial admin console (0.8.2). Start after the UI and the uploader.
 * Line commands typed on the USB-Serial/JTAG port (the same port as the log):
 *
 *   help                 list the commands
 *   version              firmware version and device id
 *   status               upload status (url host, device id, token source, queue)
 *   queue                list the files waiting in /sd/rec (name, size, minutes, dates)
 *   uploads hold|resume  stop / allow uploads (separate from auto-sleep)
 *   screenshot           dump the current screen (LVGL snapshot, RGB565) as
 *                        base64 lines; tools/screenshot.py turns it into a PNG
 *
 * Nothing here runs unless a command is typed, and nothing here deletes
 * recordings: clearing the SD card is a one-time step that never goes into
 * committed firmware. */
void serial_cmd_start(void);
