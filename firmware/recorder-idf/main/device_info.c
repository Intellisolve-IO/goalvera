#include "device_info.h"

#include "esp_mac.h"
#include <stdio.h>

static char s_id[13] = "000000000000";

void device_id_init(void)
{
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(s_id, sizeof s_id, "%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

const char *device_id_str(void)
{
    return s_id;
}
