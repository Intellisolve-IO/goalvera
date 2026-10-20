/* Host test for the pilot guard (Option B).
 *   cc -Wall -Imain test/host/test_pilot_guard.c main/pilot_guard.c -o /tmp/tpg && /tmp/tpg */
#include "pilot_guard.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
    /* Backlog policy */
    assert(pg_backlog_level(0, -1) == PG_OK);
    assert(pg_backlog_level(29 * 60, 3 * 3600) == PG_OK);
    assert(pg_backlog_level(30 * 60, -1) == PG_WARN);
    assert(pg_backlog_level(10, 4 * 3600) == PG_WARN);
    assert(pg_backlog_level(60 * 60, -1) == PG_BLOCK);
    assert(pg_backlog_level(10, 24 * 3600) == PG_BLOCK);
    assert(pg_backlog_level(10, -1) == PG_OK); /* clock unknown: audio cap only */

    /* Wipe: bytes are zeroed in place (checked through a hard link that
     * survives the unlink), then the name is gone. */
    const char *p = "/tmp/tpg_rec.wav", *l = "/tmp/tpg_rec_link.wav";
    unlink(p); unlink(l);
    FILE *f = fopen(p, "wb");
    assert(f);
    char secret[10000];
    memset(secret, 'S', sizeof secret);
    fwrite(secret, 1, sizeof secret, f);
    fclose(f);
    assert(link(p, l) == 0);
    assert(pg_wipe_file(p) == 0);
    assert(access(p, F_OK) != 0);
    f = fopen(l, "rb");
    assert(f);
    char back[10000];
    assert(fread(back, 1, sizeof back, f) == sizeof back);
    fclose(f);
    for (size_t i = 0; i < sizeof back; i++) {
        assert(back[i] == 0);
    }
    unlink(l);
    assert(pg_wipe_file("/tmp/does-not-exist.wav") == -1);
    assert(pg_wipe_file(NULL) == -1);
    puts("pilot_guard OK");
    return 0;
}
