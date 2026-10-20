cmake_minimum_required(VERSION 3.16)

# sdkconfig.defaults.local is gitignored and may hold Wi-Fi credentials.
# Later files override earlier ones. Never commit the local file.
set(SDKCONFIG_DEFAULTS "sdkconfig.defaults")
# Board selection. Default (nothing passed) = CrowPanel Advance 4.3, unchanged.
#   idf.py -B build-hosyond35 -D SDKCONFIG=build-hosyond35/sdkconfig \
#          -D RECORDER_BOARD=hosyond35 build
# adds sdkconfig.defaults.hosyond35 on top. Use a separate build dir and
# sdkconfig per board: defaults only apply when that sdkconfig is created.
if(DEFINED RECORDER_BOARD AND NOT RECORDER_BOARD STREQUAL "" AND NOT RECORDER_BOARD STREQUAL "crowpanel43")
    if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults.${RECORDER_BOARD}")
        message(FATAL_ERROR "Unknown RECORDER_BOARD '${RECORDER_BOARD}' (known: crowpanel43, hosyond35)")
    endif()
    set(SDKCONFIG_DEFAULTS "${SDKCONFIG_DEFAULTS};sdkconfig.defaults.${RECORDER_BOARD}")
endif()
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults.local")
    set(SDKCONFIG_DEFAULTS "${SDKCONFIG_DEFAULTS};sdkconfig.defaults.local")
endif()
# tools/build-with-wifi.sh passes a gitignored credentials file here.
if(DEFINED RECORDER_EXTRA_DEFAULTS)
    set(SDKCONFIG_DEFAULTS "${SDKCONFIG_DEFAULTS};${RECORDER_EXTRA_DEFAULTS}")
endif()

# Firmware version: the ONE place to change it. It goes into the app
# descriptor (esptool image_info, OTA) and FW_VERSION (boot log + sidecar).
# v4 = 0.4.0, v5 = 0.5.0, ...
set(PROJECT_VER "0.8.0")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(recorder)

# 0.7.7: LVGL's built-in 64 KB heap (work_mem_int) was the largest block of
# internal RAM (static .bss). Put it in PSRAM (.ext_ram.bss, needs
# CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY) so Wi-Fi/TLS/SD get that RAM.
# The extra LVGL pools added at boot were already in PSRAM.
idf_component_get_property(lvgl_lib lvgl__lvgl COMPONENT_LIB)
target_compile_definitions(${lvgl_lib} PRIVATE
    "LV_ATTRIBUTE_LARGE_RAM_ARRAY=__attribute__((section(\".ext_ram.bss.lv_mem\")))")
