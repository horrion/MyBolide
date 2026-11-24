import os
import threading
import time

from mybolide.capture import run_capture
from mybolide.config import Config
from mybolide.serial_port import ReadOnlySerial
from mybolide.store import Store

SPEED_RPM = bytes.fromhex("8005BF1841197A")


def test_port_is_read_only_and_has_no_write_path():
    master, slave = os.openpty()
    try:
        reader = ReadOnlySerial(os.ttyname(slave), baud=9600, parity="even", rtscts=False)
        try:
            assert reader.is_read_only()
            assert not hasattr(reader, "write")
            os.write(master, SPEED_RPM)
            seen = b""
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and SPEED_RPM not in seen:
                seen += reader.read(0.05)
            assert SPEED_RPM in seen
        finally:
            reader.close()
    finally:
        os.close(master)
        os.close(slave)


def test_capture_loop_stores_a_pty_frame(tmp_path):
    master, slave = os.openpty()
    name = os.ttyname(slave)
    store = Store(tmp_path / "bus.sqlite", max_db_bytes=50_000_000, batch_ms=1000)
    config = Config(
        device=name,
        baud=9600,
        parity="none",
        rtscts=False,
        gap_ms=30,
        token="test-token",
        db_path=str(store.path),
        batch_ms=1000,
        mdns=False,
    )
    stop = threading.Event()
    thread = threading.Thread(target=run_capture, args=(config, store, stop), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        found = False
        while time.monotonic() < deadline:
            os.write(master, SPEED_RPM)
            rows, _ = store.frames_page(0, 10)
            if rows:
                found = True
                assert rows[0]["raw_hex"] == SPEED_RPM.hex()
                signals, _ = store.signals_page(0, 10)
                assert {row["name"]: row["value"] for row in signals} == {
                    "speed_kmh": 130.0,
                    "rpm": 2500.0,
                }
                break
            time.sleep(0.05)
        assert found
    finally:
        stop.set()
        thread.join(timeout=5)
        os.close(master)
        os.close(slave)
        store.close()
