#pragma once

/* After wifi_start_if_configured() and uploader_start(). Shows the firmware
 * version, confirms a pending image only after Wi-Fi and the upload API
 * answer, and checks the signed manifest at boot and every 6 hours while idle. */
void ota_start(void);
