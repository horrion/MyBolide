import sqlite3

from mybolide.clock import ClockSample
from mybolide.decode import Signal, decode_frame
from mybolide.parser import Frame
from mybolide.store import Store

from tests.support import parse_one


def _clock() -> ClockSample:
    return ClockSample("boot-1", mono_ms=10, wall_ms=1_700_000_000_000, clock_trusted=False)


def _add(store: Store, hex_text: str) -> None:
    frame = parse_one(hex_text)
    store.add(frame, decode_frame(frame), _clock())


def test_round_trip_links_signals_and_ack_deletes_them(tmp_path):
    store = Store(tmp_path / "bus.sqlite", max_db_bytes=50_000_000, batch_ms=1000)
    _add(store, "8005BF1841197A")
    _add(store, "8004BF110329")
    _add(store, "50046832111F")
    frames, oldest_frame = store.frames_page(0, 10)
    signals, oldest_signal = store.signals_page(0, 10)
    assert oldest_frame == 1
    assert oldest_signal == 1
    assert [frame["cmd"] for frame in frames] == [0x18, 0x11, 0x32]
    assert frames[2]["raw_hex"] == "50046832111f"
    speed = [row for row in signals if row["name"] == "speed_kmh"][0]
    rpm = [row for row in signals if row["name"] == "rpm"][0]
    assert speed["frame_id"] == rpm["frame_id"] == frames[0]["id"]
    assert speed["value"] == 130
    assert rpm["value"] == 2500
    assert signals[-1]["name"] == "ignition"
    deleted_signals, deleted_frames = store.ack(
        signals_through=signals[-1]["id"],
        frames_through=frames[1]["id"],
    )
    assert deleted_signals == 3
    assert deleted_frames == 2
    remaining, _ = store.frames_page(0, 10)
    assert [frame["id"] for frame in remaining] == [frames[2]["id"]]
    assert store.signals_page(0, 10)[0] == []
    store.close()


def test_ack_is_idempotent(tmp_path):
    store = Store(tmp_path / "bus.sqlite", max_db_bytes=50_000_000, batch_ms=1000)
    _add(store, "8004BF110329")
    signals, _ = store.signals_page(0, 10)
    frames, _ = store.frames_page(0, 10)
    first = store.ack(signals_through=signals[0]["id"], frames_through=frames[0]["id"])
    second = store.ack(signals_through=signals[0]["id"], frames_through=frames[0]["id"])
    assert first == (1, 1)
    assert second == (0, 0)
    assert store.counts() == (0, 0)
    store.close()


def test_batch_stays_uncommitted_until_the_interval(tmp_path):
    path = tmp_path / "bus.sqlite"
    store = Store(path, max_db_bytes=50_000_000, batch_ms=1000)
    store._last_flush = 0.0
    _add(store, "8004BF110329")
    assert store.maybe_flush(0.2) is False
    other = sqlite3.connect(path)
    try:
        assert other.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0
    finally:
        other.close()
    assert store.maybe_flush(2.0) is True
    other = sqlite3.connect(path)
    try:
        assert other.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 1
    finally:
        other.close()
    store.close()


def test_cap_drops_oldest_frames_and_their_signals(tmp_path):
    store = Store(tmp_path / "bus.sqlite", max_db_bytes=50_000_000, batch_ms=1000)
    bulky = Frame(source=0x80, destination=0xBF, data=b"\x11\x01", raw=b"\x11" * 500)
    for _ in range(400):
        store.add(bulky, [Signal("ignition", 1)], _clock())
    store.flush()
    full = store.disk_bytes()
    assert full > 100_000
    store.max_db_bytes = full // 2
    removed = store.enforce_cap()
    frames, signals = store.counts()
    assert removed > 0
    assert 0 < frames < 400
    assert signals == frames
    assert store.disk_bytes() <= store.max_db_bytes
    rows, oldest = store.frames_page(0, 1)
    assert oldest is not None and oldest > 1
    assert rows[0]["id"] == oldest
    store.close()


def test_auto_vacuum_is_incremental(tmp_path):
    path = tmp_path / "bus.sqlite"
    store = Store(path, max_db_bytes=50_000_000, batch_ms=1000)
    mode = store._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    assert mode == 2
    store.close()
    reopened = Store(path, max_db_bytes=50_000_000, batch_ms=1000)
    mode = reopened._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    assert mode == 2
    reopened.close()
