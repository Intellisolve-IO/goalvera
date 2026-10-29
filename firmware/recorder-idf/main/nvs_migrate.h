#pragma once
#include "esp_err.h"
/* tasks#22. Opens the default NVS partition encrypted, migrating a plaintext
 * partition in place on the first boot after flash encryption. */
esp_err_t nvs_migrate_init(void);
/* Read-only check: snapshot every entry, log only counts. */
esp_err_t nvs_migrate_dry_run(void);
