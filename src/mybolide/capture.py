"""Serial capture loop. The HTTP API stays up while the adapter is absent."""

from __future__ import annotations

import logging
import threading
import time

from mybolide.clock import read_boot_id, sample_clock
from mybolide.config import Config
from mybolide.decode import decode_frame
from mybolide.parser import FrameParser
from mybolide.serial_port import ReadOnlySerial
from mybolide.store import Store

log = logging.getLogger(__name__)


def run_capture(config: Config, store: Store, stop: threading.Event) -> None:
    parser = FrameParser(gap_s=config.gap_s)
    boot_id = read_boot_id()
    store.enforce_cap()
    frames, signals = store.counts()
    log.info("cache has %s frames and %s signals", frames, signals)
    announced_wait = False
    try:
        while not stop.is_set():
            try:
                reader = ReadOnlySerial(
                    config.device, config.baud, config.parity, config.rtscts
                )
            except OSError as exc:
                if not announced_wait:
                    log.warning("iBus adapter not available (%s); retrying", exc)
                    announced_wait = True
                if stop.wait(2.0):
                    break
                continue
            announced_wait = False
            log.info(
                "listening on %s at %s baud, parity %s",
                config.device,
                config.baud,
                config.parity,
            )
            try:
                while not stop.is_set():
                    try:
                        data = reader.read(parser.gap_s)
                    except InterruptedError:
                        continue
                    now = time.monotonic()
                    _persist(store, boot_id, parser.feed(data, now), config.tank_litres)
                    if store.maybe_flush(now):
                        store.enforce_cap()
            except OSError as exc:
                log.warning("iBus read failed (%s); reopening", exc)
            finally:
                _persist(store, boot_id, parser.force_flush(), config.tank_litres)
                store.flush()
                reader.close()
            if stop.wait(1.0):
                break
    finally:
        _persist(store, boot_id, parser.force_flush(), config.tank_litres)
        store.flush()


def _persist(store: Store, boot_id: str, frames: list, tank_litres: float) -> None:
    for frame in frames:
        store.add(
            frame,
            decode_frame(frame, tank_litres=tank_litres),
            sample_clock(boot_id),
        )
