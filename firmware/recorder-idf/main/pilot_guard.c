#include "pilot_guard.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

int pg_wipe_file(const char *path)
{
    if (!path || !path[0]) {
        return -1;
    }
    FILE *f = fopen(path, "r+b");
    if (!f) {
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return -1;
    }
    long size = ftell(f);
    if (size < 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return -1;
    }
    static const char zeros[4096];
    long left = size;
    while (left > 0) {
        size_t n = left > (long)sizeof zeros ? sizeof zeros : (size_t)left;
        if (fwrite(zeros, 1, n, f) != n) {
            fclose(f);
            return -1;
        }
        left -= (long)n;
    }
    if (fflush(f) != 0 || fsync(fileno(f)) != 0) {
        fclose(f);
        return -1;
    }
    fclose(f);
    return unlink(path) == 0 ? 0 : -1;
}

pg_level_t pg_backlog_level(int64_t audio_s, int64_t oldest_age_s)
{
    if (audio_s >= PG_BLOCK_AUDIO_S || oldest_age_s >= PG_BLOCK_AGE_S) {
        return PG_BLOCK;
    }
    if (audio_s >= PG_WARN_AUDIO_S || oldest_age_s >= PG_WARN_AGE_S) {
        return PG_WARN;
    }
    return PG_OK;
}
