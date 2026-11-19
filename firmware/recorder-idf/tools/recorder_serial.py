"""Open the recorder's USB serial port WITHOUT resetting the board.

Why pyserial reset it: on macOS (and Linux) the kernel raises DTR and RTS
when the port is opened, then pyserial.open() clears DTR first and RTS second
(serialposix.py: _update_dtr_state() before _update_rts_state()). For a
moment that is RTS=1, DTR=0, which the ESP32-S3 USB-Serial/JTAG treats as
"reset the chip". Closing with HUPCL set drops both lines again.

Here the port is opened with plain os.open + termios: the modem lines are
never touched after the kernel's own open (both high together = no reset),
and HUPCL is cleared so closing doesn't drop them either. Same effect as
`stty -f PORT -hupcl; cat PORT`. No pyserial needed.
"""
import os
import select
import termios
import time


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
    iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
               termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF)
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.HUPCL | getattr(termios, "CRTSCTS", 0))
    cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
    return fd


def write(fd, data):
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
            view = view[n:]
        except BlockingIOError:
            select.select([], [fd], [], 0.5)


def read_for(fd, seconds, on_data, stop=None):
    """Call on_data(bytes) for everything read in `seconds`; stop early if stop(bytes) is true."""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if data:
            on_data(data)
            if stop and stop(data):
                return True
    return False
