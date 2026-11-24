"""Read-only serial port.

The port is opened O_RDONLY, so this process cannot write a byte onto the
iBus even if a later change calls write on the file descriptor by mistake.
RTS and DTR are dropped when the driver allows it. Some adapters treat RTS
as a transmit enable.
"""

from __future__ import annotations

import fcntl
import os
import select
import struct
import termios

BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


class ReadOnlySerial:
    def __init__(self, device: str, baud: int, parity: str, rtscts: bool) -> None:
        if baud not in BAUD_RATES:
            raise ValueError(f"unsupported baud {baud}")
        if parity not in ("even", "none"):
            raise ValueError("parity must be 'even' or 'none'")
        self.device = device
        self._fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure(baud, parity, rtscts)
            self._drop_modem_lines()
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self._fd)
            self._fd = -1
            raise

    def read(self, timeout: float) -> bytes:
        if self._fd < 0:
            raise OSError("serial port is closed")
        readable, _, _ = select.select([self._fd], [], [], timeout)
        if not readable:
            return b""
        try:
            return os.read(self._fd, 4096)
        except BlockingIOError:
            return b""

    def is_read_only(self) -> bool:
        flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)
        return (flags & os.O_ACCMODE) == os.O_RDONLY

    def close(self) -> None:
        if self._fd < 0:
            return
        fd = self._fd
        self._fd = -1
        os.close(fd)

    def _configure(self, baud: int, parity: str, rtscts: bool) -> None:
        attrs = termios.tcgetattr(self._fd)
        cflag = attrs[2]
        cflag |= termios.CLOCAL | termios.CREAD
        cflag &= ~termios.CSIZE
        cflag |= termios.CS8
        cflag &= ~termios.CSTOPB
        cflag &= ~termios.HUPCL
        if parity == "even":
            cflag |= termios.PARENB
            cflag &= ~termios.PARODD
        else:
            cflag &= ~(termios.PARENB | termios.PARODD)
        if rtscts:
            cflag |= termios.CRTSCTS
        else:
            cflag &= ~termios.CRTSCTS
        speed = BAUD_RATES[baud]
        cc = attrs[6]
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(
            self._fd,
            termios.TCSANOW,
            [0, 0, cflag, 0, speed, speed, cc],
        )
        termios.tcflush(self._fd, termios.TCIFLUSH)

    def _drop_modem_lines(self) -> None:
        mask = struct.pack("I", termios.TIOCM_DTR | termios.TIOCM_RTS)
        try:
            fcntl.ioctl(self._fd, termios.TIOCMBIC, mask)
        except OSError:
            # A read-only descriptor can refuse modem-line changes. The port
            # still cannot carry outbound bytes.
            return
