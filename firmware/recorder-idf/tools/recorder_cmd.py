#!/usr/bin/env python3
"""Send one serial console command to the recorder (firmware 0.8.2+) and print
what comes back for a few seconds.

    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 status
    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 queue
    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 "queue wipe YES" --seconds 60

Close any other serial monitor first. Opening the port does not reset the board
(recorder_serial.py explains why the old pyserial version did).
"""
import argparse
import os
import sys

from recorder_serial import open_port, read_for, write


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("command")
    ap.add_argument("--seconds", type=float, default=6)
    a = ap.parse_args()
    fd = open_port(a.port)
    try:
        write(fd, b"\n" + a.command.encode() + b"\n")
        read_for(fd, a.seconds, lambda d: (sys.stdout.write(d.decode("utf-8", "replace")), sys.stdout.flush()))
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
