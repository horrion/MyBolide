"""Choose a cache cap from the SD card, leaving room for other software.

The database lives on the Pi's SD card, which also holds the OS and, later,
CarPlay. The automatic cap is the smaller of half the card and the free space
above a reserve. That reserve is a quarter of the card, and at least 2 GiB
when the card is 8 GiB or larger. A `max_db_mb` setting replaces the
automatic cap. An override larger than the free space is cut down so the
card still has 64 MiB left.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MiB = 1024 * 1024
GiB = 1024 * MiB
_RESERVE_MIN = 2 * GiB
_LARGE_CARD = 8 * GiB
_MIN_AUTO = 64 * MiB
_OVERRIDE_MARGIN = 64 * MiB
_FALLBACK = 2048 * MiB


@dataclass(frozen=True, slots=True)
class Filesystem:
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True, slots=True)
class CacheLimit:
    nbytes: int
    summary: str


def filesystem_usage(path: Path) -> Filesystem:
    target = path
    while not target.exists():
        parent = target.parent
        if parent == target:
            break
        target = parent
    stats = os.statvfs(target)
    total = int(stats.f_blocks) * int(stats.f_frsize)
    free = int(stats.f_bavail) * int(stats.f_frsize)
    return Filesystem(total_bytes=total, free_bytes=min(free, total))


def cache_limit_bytes(total: int, free: int, override_mb: int | None) -> CacheLimit:
    if total <= 0:
        raise ValueError("filesystem size must be positive")
    free = max(0, min(free, total))
    if override_mb is not None:
        requested = override_mb * MiB
        if requested > free:
            kept = free - _OVERRIDE_MARGIN if free > _OVERRIDE_MARGIN else free
            return CacheLimit(
                max(kept, 1),
                f"max_db_mb is larger than the free space; cache cap is {_mib(kept)} MiB",
            )
        return CacheLimit(requested, f"cache cap is {_mib(requested)} MiB from max_db_mb")

    reserve = total // 4
    if total >= _LARGE_CARD:
        reserve = max(reserve, _RESERVE_MIN)
    room = free - reserve
    if room >= _MIN_AUTO:
        nbytes = min(room, total // 2)
    else:
        nbytes = free // 4
    nbytes = max(nbytes, 1)
    return CacheLimit(
        nbytes,
        (
            f"SD card filesystem is {_mib(total)} MiB with {_mib(free)} MiB free; "
            f"cache cap is {_mib(nbytes)} MiB, leaving room for the OS, CarPlay, "
            f"and other software"
        ),
    )


def resolve_cache_limit(path: Path, override_mb: int | None) -> CacheLimit:
    try:
        usage = filesystem_usage(path)
    except OSError:
        if override_mb is not None:
            nbytes = override_mb * MiB
            return CacheLimit(nbytes, f"cache cap is {_mib(nbytes)} MiB from max_db_mb")
        return CacheLimit(
            _FALLBACK,
            "could not read the SD card size; cache cap is 2048 MiB",
        )
    return cache_limit_bytes(usage.total_bytes, usage.free_bytes, override_mb)


def _mib(nbytes: int) -> int:
    return nbytes // MiB
