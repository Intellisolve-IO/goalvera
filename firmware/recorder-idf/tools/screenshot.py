#!/usr/bin/env python3
"""Grab the recorder's screen as a PNG over USB serial (firmware 0.8.2+).

Sends the "screenshot" console command, reads the base64 RGB565 dump that
serial_cmd.c prints between ===SNAPSHOT BEGIN ...=== and ===SNAPSHOT END ...===,
checks the CRC-32C and writes a PNG. No third-party image library needed.

    python3 tools/screenshot.py /dev/cu.usbmodem101 recording.png
    python3 tools/screenshot.py --from-log serial.txt out.png   # parse a saved log

Close any other serial monitor on the port first (only one program can own it).
Opening the port does not reset the board (see recorder_serial.py).
"""
import argparse
import base64
import re
import struct
import sys
import time
import zlib

BEGIN = re.compile(r"===SNAPSHOT BEGIN w=(\d+) h=(\d+) cf=RGB565 stride=(\d+) bytes=(\d+)===")
END = re.compile(r"===SNAPSHOT END crc32c=([0-9a-f]{8}) lines=(\d+)===")
LINE = re.compile(r"^S:(\d+):([A-Za-z0-9+/=]+)$")


def crc32c(data: bytes) -> int:
    table = crc32c.table if hasattr(crc32c, "table") else None
    if table is None:
        table = []
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
            table.append(c)
        crc32c.table = table
    c = 0xFFFFFFFF
    for b in data:
        c = table[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def parse(lines):
    hdr, chunks, end = None, {}, None
    for raw in lines:
        s = raw.strip()
        if hdr is None:
            m = BEGIN.search(s)
            if m:
                hdr = tuple(int(x) for x in m.groups())
            continue
        m = END.search(s)
        if m:
            end = (int(m.group(1), 16), int(m.group(2)))
            break
        m = LINE.match(s)
        if m:
            chunks[int(m.group(1))] = base64.b64decode(m.group(2))
    if not hdr or not end:
        raise SystemExit("no complete snapshot found")
    w, h, stride, size = hdr
    want_crc, nlines = end
    missing = [i for i in range(nlines) if i not in chunks]
    if missing:
        raise SystemExit(f"{len(missing)} snapshot lines missing/garbled (first {missing[:5]}); try again")
    data = b"".join(chunks[i] for i in range(nlines))
    if len(data) != size:
        raise SystemExit(f"size mismatch {len(data)} != {size}")
    got = crc32c(data)
    if got != want_crc:
        print(f"warning: crc32c {got:08x} != {want_crc:08x} (firmware crc may be unseeded); continuing",
              file=sys.stderr)
    return w, h, stride, data


def to_png(w, h, stride, data, path):
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        row = data[y * stride:y * stride + w * 2]
        for x in range(w):
            v = row[2 * x] | (row[2 * x + 1] << 8)  # LVGL RGB565, little-endian
            r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
            rows += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def from_port(port, timeout):
    import os
    from recorder_serial import open_port, read_for, write
    fd = open_port(port)
    lines, buf = [], [b""]

    def on_data(d):
        buf[0] += d
        *done, buf[0] = buf[0].split(b"\n")
        lines.extend(x.decode("ascii", "replace") for x in done)

    try:
        write(fd, b"\nscreenshot\n")
        ok = read_for(fd, timeout, on_data, stop=lambda d: any(END.search(x) for x in lines[-3:]))
    finally:
        os.close(fd)
    if not ok:
        raise SystemExit("timed out waiting for the snapshot (is the firmware 0.8.2+?)")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("out")
    ap.add_argument("--from-log")
    ap.add_argument("--timeout", type=float, default=30)
    a = ap.parse_args()
    if a.from_log:
        with open(a.from_log, errors="replace") as f:
            lines = f.read().splitlines()
    elif a.port:
        lines = from_port(a.port, a.timeout)
    else:
        ap.error("give a serial port or --from-log")
    w, h, stride, data = parse(lines)
    to_png(w, h, stride, data, a.out)
    print(f"wrote {a.out} ({w}x{h})")


if __name__ == "__main__":
    main()
