"""Clocks stored with each sample.

The Pi often has no RTC. Monotonic time stays consistent for a boot even when
wall time steps after NTP. Wall time is flagged once systemd-timesyncd says
the clock is synchronized.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

_TIMESYNC_PATH = "/run/systemd/timesync/synchronized"
_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


@dataclass(frozen=True, slots=True)
class ClockSample:
    boot_id: str
    mono_ms: int
    wall_ms: int
    clock_trusted: bool


def read_boot_id() -> str:
    try:
        with open(_BOOT_ID_PATH, encoding="ascii") as handle:
            value = handle.read().strip()
    except OSError:
        value = ""
    return value or str(uuid.uuid4())


def clock_is_trusted() -> bool:
    return os.path.exists(_TIMESYNC_PATH)


def sample_clock(boot_id: str) -> ClockSample:
    return ClockSample(
        boot_id=boot_id,
        mono_ms=time.monotonic_ns() // 1_000_000,
        wall_ms=time.time_ns() // 1_000_000,
        clock_trusted=clock_is_trusted(),
    )
