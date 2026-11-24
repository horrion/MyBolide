"""Durable cache of frames and decoded signals.

Rows stay until the phone acknowledges them. Commits land about once a
second, so a power cut drops at most the open batch. Ids are never reused.
If the file grows past the configured cap, the oldest frames are deleted.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mybolide.clock import ClockSample
from mybolide.decode import Signal
from mybolide.parser import Frame

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Pending:
    frame: Frame
    signals: tuple[Signal, ...]
    clock: ClockSample


class Store:
    def __init__(self, path: str | Path, *, max_db_bytes: int, batch_ms: int) -> None:
        self.path = Path(path)
        self.max_db_bytes = max_db_bytes
        self.batch_ms = batch_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending: list[_Pending] = []
        self._last_flush = time.monotonic()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None
        self._setup()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            self._conn.close()

    def add(self, frame: Frame, signals: list[Signal], clock: ClockSample) -> None:
        with self._lock:
            self._pending.append(_Pending(frame, tuple(signals), clock))

    def maybe_flush(self, now: float) -> bool:
        with self._lock:
            if not self._pending:
                return False
            if (now - self._last_flush) * 1000 < self.batch_ms:
                return False
            self._flush_locked()
            return True

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def counts(self) -> tuple[int, int]:
        with self._lock:
            self._flush_locked()
            frames = self._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
            signals = self._conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            return int(frames), int(signals)

    def frames_page(self, after_id: int, limit: int) -> tuple[list[dict], int | None]:
        with self._lock:
            self._flush_locked()
            rows = self._conn.execute(
                """
                SELECT id, boot_id, mono_ms, wall_ms, clock_trusted, src, dst, cmd, raw
                FROM frames
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
            oldest = self._conn.execute("SELECT MIN(id) FROM frames").fetchone()[0]
            return [_frame_row(row) for row in rows], _optional_int(oldest)

    def signals_page(self, after_id: int, limit: int) -> tuple[list[dict], int | None]:
        with self._lock:
            self._flush_locked()
            rows = self._conn.execute(
                """
                SELECT id, frame_id, boot_id, mono_ms, wall_ms, clock_trusted, name, value
                FROM signals
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
            oldest = self._conn.execute("SELECT MIN(id) FROM signals").fetchone()[0]
            return [_signal_row(row) for row in rows], _optional_int(oldest)

    def ack(self, *, signals_through: int, frames_through: int) -> tuple[int, int]:
        with self._lock:
            self._flush_locked()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "DELETE FROM signals WHERE id <= ?", (signals_through,)
                )
                signal_count = self._changes()
                self._conn.execute(
                    "DELETE FROM frames WHERE id <= ?", (frames_through,)
                )
                frame_count = self._changes()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            if signal_count or frame_count:
                self._release_space_locked()
            return signal_count, frame_count

    def disk_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
        return total

    def enforce_cap(self) -> int:
        if self.max_db_bytes <= 0:
            return 0
        with self._lock:
            self._flush_locked()
            self._release_space_locked()
        removed = 0
        for _ in range(100):
            if self.disk_bytes() <= self.max_db_bytes:
                break
            before = self.disk_bytes()
            with self._lock:
                self._flush_locked()
                total = int(self._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
                if total == 0:
                    break
                overflow = max(before - self.max_db_bytes, 1)
                fraction = min(0.5, max(overflow / before, 0.05))
                limit = max(1, int(total * fraction))
                if total > limit:
                    limit = min(limit, total - 1)
                rows = self._conn.execute(
                    "SELECT id FROM frames ORDER BY id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                if not rows:
                    break
                ids = [int(row[0]) for row in rows]
                self._delete_frame_ids_locked(ids)
                self._release_space_locked()
            removed += len(ids)
            if self.disk_bytes() >= before and self.disk_bytes() > self.max_db_bytes:
                log.error(
                    "cache is over %s bytes but the file did not shrink; stopped deleting",
                    self.max_db_bytes,
                )
                break
        if removed:
            log.warning(
                "cache exceeded %s bytes; dropped %s oldest frames",
                self.max_db_bytes,
                removed,
            )
        return removed

    def _setup(self) -> None:
        mode = int(self._conn.execute("PRAGMA auto_vacuum").fetchone()[0])
        if mode != 2:
            self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._conn.execute("VACUUM")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_size_limit=1048576")
        self._conn.execute("PRAGMA user_version=1")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                boot_id TEXT NOT NULL,
                mono_ms INTEGER NOT NULL,
                wall_ms INTEGER NOT NULL,
                clock_trusted INTEGER NOT NULL,
                src INTEGER NOT NULL,
                dst INTEGER NOT NULL,
                cmd INTEGER,
                raw BLOB NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_id INTEGER NOT NULL,
                boot_id TEXT NOT NULL,
                mono_ms INTEGER NOT NULL,
                wall_ms INTEGER NOT NULL,
                clock_trusted INTEGER NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS signals_frame_id ON signals(frame_id)"
        )

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for item in self._pending:
                frame = item.frame
                clock = item.clock
                cursor = self._conn.execute(
                    """
                    INSERT INTO frames (
                        boot_id, mono_ms, wall_ms, clock_trusted, src, dst, cmd, raw
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clock.boot_id,
                        clock.mono_ms,
                        clock.wall_ms,
                        1 if clock.clock_trusted else 0,
                        frame.source,
                        frame.destination,
                        frame.command,
                        frame.raw,
                    ),
                )
                frame_id = int(cursor.lastrowid)
                for signal in item.signals:
                    self._conn.execute(
                        """
                        INSERT INTO signals (
                            frame_id, boot_id, mono_ms, wall_ms, clock_trusted, name, value
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            frame_id,
                            clock.boot_id,
                            clock.mono_ms,
                            clock.wall_ms,
                            1 if clock.clock_trusted else 0,
                            signal.name,
                            signal.value,
                        ),
                    )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self._pending.clear()
        self._last_flush = time.monotonic()

    def _delete_frame_ids_locked(self, ids: list[int]) -> None:
        placeholders = ",".join("?" * len(ids))
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                f"DELETE FROM signals WHERE frame_id IN ({placeholders})", ids
            )
            self._conn.execute(
                f"DELETE FROM frames WHERE id IN ({placeholders})", ids
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _changes(self) -> int:
        return int(self._conn.execute("SELECT changes()").fetchone()[0])

    def _release_space_locked(self) -> None:
        """Shrink the file after deletes.

        WAL mode does not truncate the database when pages are freed, so a
        cache that had been acknowledged would still fill the SD card. Compact
        once the free space is large, or immediately when the cap is already
        exceeded. Small acks leave the file alone.
        """
        page_size = int(self._conn.execute("PRAGMA page_size").fetchone()[0])
        free_pages = int(self._conn.execute("PRAGMA freelist_count").fetchone()[0])
        free_bytes = free_pages * page_size
        over_cap = self.max_db_bytes > 0 and self.disk_bytes() > self.max_db_bytes
        if free_bytes < 1_048_576 and not (over_cap and free_bytes > 0):
            return
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("PRAGMA journal_mode=DELETE")
        try:
            self._conn.execute("VACUUM")
        finally:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA journal_size_limit=1048576")


def _frame_row(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "boot_id": row["boot_id"],
        "mono_ms": int(row["mono_ms"]),
        "wall_ms": int(row["wall_ms"]),
        "clock_trusted": bool(row["clock_trusted"]),
        "src": int(row["src"]),
        "dst": int(row["dst"]),
        "cmd": None if row["cmd"] is None else int(row["cmd"]),
        "raw_hex": row["raw"].hex(),
    }


def _signal_row(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "frame_id": int(row["frame_id"]),
        "boot_id": row["boot_id"],
        "mono_ms": int(row["mono_ms"]),
        "wall_ms": int(row["wall_ms"]),
        "clock_trusted": bool(row["clock_trusted"]),
        "name": row["name"],
        "value": float(row["value"]),
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
