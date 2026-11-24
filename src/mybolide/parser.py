"""iBus frame parser.

A frame is source, length, destination, data, XOR checksum. The length byte
counts every byte after itself, including the checksum. There is no delimiter.
Bytes are held until the host sees a silence gap, then walked. A bad checksum
or an illegal length drops one byte and tries again.

The gap is measured on the USB serial stream, not on the bus wire. FTDI
adapters often hold bytes for a 16ms latency timer, so the default gap is
50ms. That still closes a frame promptly and does not split one USB packet
across two parses.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_LENGTH = 2
MAX_LENGTH = 34
DEFAULT_GAP_S = 0.05
DEFAULT_MAX_BUFFER = 4096


def checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


@dataclass(frozen=True, slots=True)
class Frame:
    source: int
    destination: int
    data: bytes
    raw: bytes

    @property
    def command(self) -> int | None:
        if not self.data:
            return None
        return self.data[0]

    @property
    def payload(self) -> bytes:
        """Bytes after the command byte."""
        return self.data[1:]


class FrameParser:
    def __init__(
        self,
        *,
        gap_s: float = DEFAULT_GAP_S,
        max_length: int = MAX_LENGTH,
        max_buffer: int = DEFAULT_MAX_BUFFER,
    ) -> None:
        if gap_s <= 0:
            raise ValueError("gap_s must be positive")
        if max_length < MIN_LENGTH:
            raise ValueError("max_length is below the shortest iBus frame")
        self.gap_s = gap_s
        self.max_length = max_length
        self.max_buffer = max_buffer
        self._buf = bytearray()
        self._last_data_at: float | None = None

    def feed(self, data: bytes, now: float) -> list[Frame]:
        """Append bytes, or observe silence when data is empty.

        A silence of at least gap_s closes the buffer and parses it. A new
        chunk that arrives after such a silence closes the previous buffer
        first, then starts a new one. The returned frames never include bytes
        from the chunk that just arrived; those wait for the next gap.
        """
        if not data:
            if self._buf and self._gap_elapsed(now):
                self._last_data_at = None
                return self._take_buffer(discard_incomplete=True)
            return []

        frames: list[Frame] = []
        if self._buf and self._gap_elapsed(now):
            frames.extend(self._take_buffer(discard_incomplete=True))
        self._buf.extend(data)
        self._last_data_at = now
        if len(self._buf) >= self.max_buffer:
            frames.extend(self._take_buffer(discard_incomplete=False))
        return frames

    def force_flush(self) -> list[Frame]:
        """Parse whatever is buffered. Used on shutdown and device loss."""
        self._last_data_at = None
        return self._take_buffer(discard_incomplete=True)

    def _gap_elapsed(self, now: float) -> bool:
        if self._last_data_at is None:
            return False
        return now - self._last_data_at + 1e-9 >= self.gap_s

    def _take_buffer(self, *, discard_incomplete: bool) -> list[Frame]:
        buf = self._buf
        self._buf = bytearray()
        frames: list[Frame] = []
        while len(buf) >= 2:
            length = buf[1]
            if length < MIN_LENGTH or length > self.max_length:
                del buf[0]
                continue
            total = length + 2
            if len(buf) < total:
                # A gap means nothing more is coming, so a short tail cannot
                # be a real frame. Keep scanning in case a valid frame follows
                # a false length. A mid-stream flush keeps that tail instead.
                if discard_incomplete:
                    del buf[0]
                    continue
                break
            raw = bytes(buf[:total])
            if checksum(raw[:-1]) != raw[-1]:
                del buf[0]
                continue
            del buf[:total]
            frames.append(
                Frame(
                    source=raw[0],
                    destination=raw[2],
                    data=raw[3:-1],
                    raw=raw,
                )
            )
        if buf and not discard_incomplete:
            self._buf = buf
        return frames
