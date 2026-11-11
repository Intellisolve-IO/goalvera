#pragma once

#include "esp_app_desc.h"

/* Set once in the project CMakeLists.txt (PROJECT_VER). Printed at boot and
 * written to every recording's .json sidecar. */
#define FW_VERSION (esp_app_get_description()->version)
