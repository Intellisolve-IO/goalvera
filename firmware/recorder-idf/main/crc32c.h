#pragma once

#include <stddef.h>
#include <stdint.h>

/* Start with crc = 0; feed chunks in order. */
uint32_t crc32c_update(uint32_t crc, const void *data, size_t len);
