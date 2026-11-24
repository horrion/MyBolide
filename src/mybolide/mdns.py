"""mDNS advertisement so the iPhone can find the Pi without a fixed address."""

from __future__ import annotations

import logging
import socket
import threading

from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE = "_mybolide._tcp.local."
SERVICE_NAME = "MyBolide._mybolide._tcp.local."


def local_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except socket.gaierror:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        ip = probe.getsockname()[0]
        if isinstance(ip, str) and not ip.startswith("127."):
            found.add(ip)
    except OSError:
        pass
    finally:
        probe.close()
    return sorted(found)


def build_service_info(port: int, addresses: list[str]) -> ServiceInfo:
    return ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(ip) for ip in addresses],
        port=port,
        properties={"path": "/v1/sync", "version": "1"},
    )


class Advertiser:
    def __init__(self, port: int, refresh_s: float = 30.0) -> None:
        self._port = port
        self._refresh_s = refresh_s
        self._stop = threading.Event()
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._addresses: tuple[str, ...] = ()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._zc = Zeroconf(ip_version=IPVersion.V4Only)
        self._publish()
        self._thread = threading.Thread(target=self._loop, name="mdns", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._unpublish()
        if self._zc is not None:
            self._zc.close()
            self._zc = None

    def _loop(self) -> None:
        while not self._stop.wait(self._refresh_s):
            try:
                self._publish()
            except Exception:
                log.exception("mDNS refresh failed")

    def _publish(self) -> None:
        addresses = tuple(local_ipv4_addresses())
        if not addresses:
            log.warning("no IPv4 address for mDNS yet")
            return
        if addresses == self._addresses and self._info is not None:
            return
        if self._zc is None:
            return
        self._unpublish()
        info = build_service_info(self._port, list(addresses))
        self._zc.register_service(info)
        self._info = info
        self._addresses = addresses
        log.info(
            "advertising _mybolide._tcp on port %s via %s",
            self._port,
            ", ".join(addresses),
        )

    def _unpublish(self) -> None:
        if self._zc is None or self._info is None:
            return
        try:
            self._zc.unregister_service(self._info)
        except Exception:
            log.exception("mDNS unregister failed")
        self._info = None
        self._addresses = ()
