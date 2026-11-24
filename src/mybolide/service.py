"""Process entry point. Capture runs on the main thread; HTTP is beside it."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from mybolide.capture import run_capture
from mybolide.config import load_config
from mybolide.disk import resolve_cache_limit
from mybolide.store import Store
from mybolide.sync_api import make_server

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mybolide")
    parser.add_argument("--config", default="/etc/mybolide/config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = load_config(Path(args.config))
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    limit = resolve_cache_limit(Path(config.db_path), config.max_db_mb)
    log.info("%s", limit.summary)
    store = Store(
        config.db_path,
        max_db_bytes=limit.nbytes,
        batch_ms=config.batch_ms,
    )
    server = make_server(config.bind_host, config.bind_port, store, config.token)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.2),
        name="sync-api",
        daemon=True,
    )
    thread.start()
    log.info("sync API listening on %s:%s", config.bind_host, config.bind_port)

    advertiser = None
    if config.mdns:
        try:
            from mybolide.mdns import Advertiser

            advertiser = Advertiser(config.bind_port)
            advertiser.start()
        except Exception:
            log.exception("mDNS advertisement failed; the sync API is still available")

    beacon = None
    if config.ble:
        try:
            from mybolide.ble import PresenceAdvertiser, unacked

            beacon = PresenceAdvertiser(config.bind_port, lambda: unacked(store))
            beacon.start()
        except Exception:
            log.exception(
                "Bluetooth advertisement failed; the sync API is still available"
            )

    stop = threading.Event()

    def _stop(signum: int, _frame: object) -> None:
        log.info("received signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        run_capture(config, store, stop)
    finally:
        log.info("stopping")
        store.flush()
        if advertiser is not None:
            advertiser.close()
        if beacon is not None:
            beacon.close()
        server.shutdown()
        thread.join(timeout=5)
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
