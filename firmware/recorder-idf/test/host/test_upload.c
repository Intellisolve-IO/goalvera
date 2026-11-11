/* Host unit test for the uploader's pure helpers.
 *   cc -Wall -Imain test/host/test_upload.c main/upload_name.c main/crc32c.c -o /tmp/tu && /tmp/tu */
#include "crc32c.h"
#include "upload_name.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void stem(const char *local, const char *cls, const char *want)
{
    char out[64] = "";
    bool ok = upload_remote_stem(local, cls, out, sizeof out);
    if (!want) {
        assert(!ok);
        return;
    }
    if (!ok || strcmp(out, want) != 0) {
        printf("FAIL %s + %s -> %s (want %s)\n", local, cls ? cls : "(null)", out, want);
        assert(0);
    }
}

int main(void)
{
    assert(crc32c_update(0, "123456789", 9) == 0xE3069283u); /* standard check value */
    assert(crc32c_update(crc32c_update(0, "1234", 4), "56789", 5) == 0xE3069283u);

    stem("20260928_101400_room-104", "Reading 3A", "20260928_101400_Reading-3A");
    stem("20260928_101400_room-104", "", "20260928_101400_room-104");
    stem("20260928_101400_room_104", NULL, "20260928_101400_room-104");
    stem("20260928_101400_room-104_2", "Math 2B", "20260928_101400_Math-2B-2");
    stem("20260928_101400_x", "A very long class name here", "20260928_101400_A-very-long-clas");
    stem("20260928_101400_x_12", "A very long class name here", "20260928_101400_A-very-long-c-12");
    stem("20260928_101400_", "!!", "20260928_101400_rec");
    stem("NOTIME_55_room-104", "X", NULL);
    stem("2026092_101400_a", "b", NULL);

    char c[24];
    assert(upload_class_code(c, sizeof c, "Reading 3A") == 10 && strcmp(c, "Reading-3A") == 0);
    assert(upload_class_code(c, sizeof c, "A very long class name here") == 16 && strcmp(c, "A very long clas") != 0);
    assert(strcmp(c, "A-very-long-clas") == 0);
    /* The sidecar code and the file-name code are the same. */
    char st[64];
    upload_remote_stem("20260929_131900_room-104", "Reading 3A", st, sizeof st);
    upload_class_code(c, sizeof c, "Reading 3A");
    assert(strcmp(st + 16, c) == 0);

    char m[256];
    upload_receipt_msg(m, sizeof m, "rec/d/20260928_101400_A.wav", "17", 123, "md5==", "crc=");
    assert(strcmp(m, "rec/d/20260928_101400_A.wav|17|123|md5==|crc=|verified") == 0);

    assert(upload_token_ok("abcdefghijklmnopqrstuvwxyz0123456789_-AB"));
    assert(!upload_token_ok("short"));
    assert(!upload_token_ok("abcdefghijklmnopqrstu v"));
    assert(upload_str_eq_ct("abc", "abc") && !upload_str_eq_ct("abc", "abd") && !upload_str_eq_ct("abc", "abcd"));
    puts("upload helpers: all tests passed");
    return 0;
}
