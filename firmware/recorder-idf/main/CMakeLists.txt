# Board-specific sources (Kconfig "Hardware board"). CrowPanel is the default;
# its files stay in their original positions in the list below.
if(CONFIG_RECORDER_BOARD_HOSYOND_35)
    set(board_ctrl_src "board_ctrl_hosyond35.c")
    set(board_display_srcs "display_st77922.c" "es8311_min.c" "battery.c" "power_sleep.c")
    # SD recordings are encrypted (CONFIG_RECORDER_SD_ENCRYPTION, default y).
    # The recording PUBLIC key is a build-time input and is never committed:
    #   idf.py -D RECORDING_PUBKEY_PEM=/path/to/recording_pubkey.pem build
    # or export RECORDING_PUBKEY_PEM=/path/... (tools/fetch-rec-pubkey.sh writes one
    # from your own Cloud KMS key). No fallback to plaintext: a missing key stops the build.
    if(NOT RECORDING_PUBKEY_PEM AND DEFINED ENV{RECORDING_PUBKEY_PEM})
        set(RECORDING_PUBKEY_PEM "$ENV{RECORDING_PUBKEY_PEM}")
    endif()
    if(NOT RECORDING_PUBKEY_PEM OR NOT EXISTS "${RECORDING_PUBKEY_PEM}")
        message(FATAL_ERROR "Recording public key missing: pass -D RECORDING_PUBKEY_PEM=<path> or set the RECORDING_PUBKEY_PEM env var (see docs/sd-encryption.md), or turn off RECORDER_SD_ENCRYPTION")
    endif()
    configure_file("${RECORDING_PUBKEY_PEM}" "${CMAKE_CURRENT_BINARY_DIR}/recording_pubkey.pem" COPYONLY)
    set(rec_crypt_srcs "rec_crypt.c")
    set(rec_crypt_embed "${CMAKE_CURRENT_BINARY_DIR}/recording_pubkey.pem")
else()
    set(rec_crypt_srcs "")
    set(rec_crypt_embed "")
    set(board_ctrl_src "board_ctrl.c")
    set(board_display_srcs "display.c")
endif()

# TweetNaCl is the public-domain Ed25519 verifier (tweetnacl.cr.yp.to). It is
# compiled as shipped; the sign-compare warnings are in the upstream macros.
if(NOT CMAKE_BUILD_EARLY_EXPANSION)
set_source_files_properties(tweetnacl.c PROPERTIES
    COMPILE_FLAGS "-Wno-sign-compare -Wno-unused-function -Wno-unused-const-variable")
endif()

idf_component_register(
    SRCS
        "app_main.c"
        "i2c_bus.c"
        ${board_ctrl_src}
        "rtc_pcf8563.c"
        "sdcard.c"
        "config_store.c"
        "nvs_prefs.c"
        "device_info.c"
        "status_line.c"
        "audio_rec.c"
        "wifi_time.c"
        ${board_display_srcs}
        "ui_app.c"
        "crc32c.c"
        "upload_name.c"
        "uploader.c"
        "pilot_guard.c"
        "nvs_migrate.c"
        "ota_policy.c"
        "ota_sign.c"
        "ota_pubkey.c"
        "ota_update.c"
        "tweetnacl.c"
        ${rec_crypt_srcs}
    INCLUDE_DIRS "."
    EMBED_TXTFILES "sample_device.json" ${rec_crypt_embed}
    REQUIRES
        esp_lcd
        esp_lcd_touch
        esp_lcd_touch_gt911
        esp_lcd_st77922
        esp_lcd_touch_st7123
        lvgl
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
        esp_driver_spi
        esp_driver_usb_serial_jtag
        esp_adc
        esp_driver_sdspi
        esp_driver_sdmmc
        esp_driver_ledc
        fatfs
        sdmmc
        nvs_flash
        esp_wifi
        esp_netif
        esp_event
        esp_timer
        json
        esp_system
        heap
        lwip
        esp_hw_support
        esp_http_client
        esp-tls
        mbedtls
        esp_rom
        esp_app_format
        app_update
        freertos
)
