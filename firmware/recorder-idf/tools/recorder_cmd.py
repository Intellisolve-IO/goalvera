#!/usr/bin/env python3
"""Send one serial console command to the recorder (firmware 0.8.2+) and print
what comes back for a few seconds.

    python3 -m pip install pyserial
    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 status
    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 queue
    python3 tools/recorder_cmd.py /dev/cu.usbmodem101 "queue wipe YES" --seconds 60

Close any other serial monitor first. Opening the port does not reset the board.
"""
import argparse
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("command")
    ap.add_argument("--seconds", type=float, default=6)
    a = ap.parse_args()
    # Set RTS/DTR low BEFORE opening: the ESP32-S3 USB-Serial/JTAG resets the
    # chip on RTS-high/DTR-low, which a default open + later change can hit.
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = a.port, 115200, 0.2
    s.rts = False
    s.dtr = False
    s.open()
    with s:
        s.reset_input_buffer()
        s.write(b"\n" + a.command.encode() + b"\n")
        s.flush()
        end = time.time() + a.seconds
        while time.time() < end:
            data = s.read(4096)
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()


if __name__ == "__main__":
    main()
