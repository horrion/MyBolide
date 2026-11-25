"""Bluetooth LE presence beacon.

iOS can relaunch an app that already has permission when it hears this
service UUID. The advertisement only names the service. The log itself is
still pulled over the HTTP API. The bearer token is not in the
advertisement or the characteristic.

The characteristic adds a public id, the first 16 hex characters of the
SHA-256 of the token. The phone uses it to tell one car's Pi from another.
It does not authenticate anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Mapping
from typing import Any

from dbus_next import Variant
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

log = logging.getLogger(__name__)

# Fixed UUIDs. iOS background scanning matches the service UUID, not a name.
SERVICE_UUID = "8f3c2a10-6b1e-4d55-9c0a-1e7b54a9d2f0"
CHARACTERISTIC_UUID = "8f3c2a11-6b1e-4d55-9c0a-1e7b54a9d2f0"
LOCAL_NAME = "MyBolide"

ADV_PATH = "/org/mybolide/advertisement"
APP_PATH = "/org/mybolide/gatt"
SERVICE_PATH = "/org/mybolide/gatt/service0"
CHAR_PATH = "/org/mybolide/gatt/service0/char0"

_BLUEZ = "org.bluez"
_ADVERTISING = "org.bluez.LEAdvertisingManager1"


def logger_public_id(token: str) -> str:
    """Public id for one Pi. Not the bearer token, and not useful as one."""
    if not isinstance(token, str):
        raise ValueError("token must be text")
    stripped = token.strip()
    if not stripped:
        raise ValueError("token is empty")
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:16]


def presence_payload(port: int, pending: bool, logger_id: str = "") -> bytes:
    """JSON the phone reads: the HTTP port, whether any row is unacked, and the public id."""
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("port must be an integer")
    if port < 1 or port > 65535:
        raise ValueError("port is out of range")
    if not isinstance(pending, bool):
        raise ValueError("pending must be true or false")
    body: dict[str, Any] = {"pending": pending, "port": port}
    if logger_id:
        if not isinstance(logger_id, str) or re.fullmatch(r"[0-9a-f]{16}", logger_id) is None:
            raise ValueError("logger id must be 16 hex characters")
        body["id"] = logger_id
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("ascii")


def unacked(store: Any) -> bool:
    """True when the cache still holds a frame or a decoded signal."""
    frames, signals = store.counts()
    return frames > 0 or signals > 0


def characteristic_value(
    port: int,
    pending_fn: Callable[[], bool],
    logger_id: str = "",
) -> bytes:
    """Read the live pending flag. A store error reports pending, so the phone still tries HTTP."""
    try:
        pending = pending_fn()
    except Exception:
        log.exception("could not read the cache for the Bluetooth beacon")
        pending = True
    if not isinstance(pending, bool):
        log.error("Bluetooth pending flag was not a boolean")
        pending = True
    return presence_payload(port, pending, logger_id)


def characteristic_bytes(
    port: int,
    pending_fn: Callable[[], bool],
    options: Mapping[str, Any] | None,
    logger_id: str = "",
) -> list[int]:
    """GATT ReadValue body, honoring an ATT offset when BlueZ passes one."""
    payload = characteristic_value(port, pending_fn, logger_id)
    offset = _value_offset(options)
    return list(payload[offset:])


def advertisement_properties() -> dict[str, Any]:
    """What the radio broadcasts. A service UUID, and nothing secret."""
    return {
        "Type": "peripheral",
        "ServiceUUIDs": [SERVICE_UUID],
        "LocalName": LOCAL_NAME,
    }


def choose_adapter(objects: Mapping[str, Mapping[str, Any]]) -> str:
    """Pick the built-in controller when BlueZ exposes more than one."""
    found = [
        path
        for path, interfaces in objects.items()
        if _ADVERTISING in interfaces
    ]
    if not found:
        raise OSError("no Bluetooth LE adapter")
    for path in found:
        if path.endswith("/hci0"):
            return path
    return sorted(found)[0]


class PresenceAdvertiser:
    """Advertise the beacon on a background thread.

    A missing adapter or a BlueZ failure is logged. ``start`` itself returns,
    so capture and the HTTP API keep running.
    """

    def __init__(
        self,
        port: int,
        pending: Callable[[], bool],
        *,
        logger_id: str = "",
        advertise: Callable[[int, Callable[[], bool], threading.Event], None]
        | None = None,
        retry_s: float = 5.0,
    ) -> None:
        self._port = port
        self._pending = pending
        self._logger_id = logger_id
        self._advertise = advertise or (
            lambda port, pending, stop: serve_bluez(port, pending, stop, logger_id)
        )
        self._retry_s = retry_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ble", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=3)
        if thread.is_alive():
            log.warning("Bluetooth advertisement is still stopping")

    def _run(self) -> None:
        warned = False
        while not self._stop.is_set():
            try:
                self._advertise(self._port, self._pending, self._stop)
            except Exception as exc:
                if self._stop.is_set():
                    return
                if not warned:
                    log.warning(
                        "Bluetooth advertisement is unavailable (%s); "
                        "the sync API is still available",
                        exc,
                    )
                    warned = True
                else:
                    log.debug("Bluetooth advertisement still unavailable", exc_info=True)
                if self._stop.wait(self._retry_s):
                    return
                continue
            return


class Advertisement(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.LEAdvertisement1")
        self._props = advertisement_properties()
        self.released = threading.Event()

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return self._props["Type"]

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return list(self._props["ServiceUUIDs"])

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self._props["LocalName"]

    @method()
    def Release(self):
        self.released.set()
        log.info("Bluetooth stack released the advertisement")


class Application(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.freedesktop.DBus.ObjectManager")

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return {
            SERVICE_PATH: {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", SERVICE_UUID),
                    "Primary": Variant("b", True),
                    "Characteristics": Variant("ao", [CHAR_PATH]),
                }
            },
            CHAR_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", CHARACTERISTIC_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read"]),
                }
            },
        }


class GattService(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return SERVICE_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Characteristics(self) -> "ao":
        return [CHAR_PATH]


class GattCharacteristic(ServiceInterface):
    def __init__(self, port: int, pending: Callable[[], bool], logger_id: str = "") -> None:
        super().__init__("org.bluez.GattCharacteristic1")
        self._port = port
        self._pending = pending
        self._logger_id = logger_id

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return CHARACTERISTIC_UUID

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return SERVICE_PATH

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return ["read"]

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        return characteristic_bytes(self._port, self._pending, options, self._logger_id)


def serve_bluez(
    port: int,
    pending: Callable[[], bool],
    stop: threading.Event,
    logger_id: str = "",
) -> None:
    """Register the advertisement with BlueZ. Raises when the stack is absent."""
    asyncio.run(_serve(port, pending, stop, logger_id))


async def _serve(
    port: int,
    pending: Callable[[], bool],
    stop: threading.Event,
    logger_id: str = "",
) -> None:
    from dbus_next import BusType
    from dbus_next.aio import MessageBus

    bus = MessageBus(bus_type=BusType.SYSTEM)
    await bus.connect()
    try:
        adapter_path = await _adapter_path(bus)
        await _power_on(bus, adapter_path)
        advertisement = Advertisement()
        bus.export(ADV_PATH, advertisement)
        bus.export(APP_PATH, Application())
        bus.export(SERVICE_PATH, GattService())
        bus.export(CHAR_PATH, GattCharacteristic(port, pending, logger_id))
        intro = await bus.introspect(_BLUEZ, adapter_path)
        proxy = bus.get_proxy_object(_BLUEZ, adapter_path, intro)
        ad_manager = proxy.get_interface(_ADVERTISING)
        gatt_manager = proxy.get_interface("org.bluez.GattManager1")
        await ad_manager.call_register_advertisement(ADV_PATH, {})
        try:
            await gatt_manager.call_register_application(APP_PATH, {})
        except Exception:
            await _quiet(ad_manager.call_unregister_advertisement(ADV_PATH))
            raise
        log.info("Bluetooth beacon advertising %s on %s", SERVICE_UUID, adapter_path)
        while not stop.is_set():
            if advertisement.released.is_set() or not bus.connected:
                raise OSError("Bluetooth advertisement was released")
            await asyncio.sleep(0.2)
        await _quiet(ad_manager.call_unregister_advertisement(ADV_PATH))
        await _quiet(gatt_manager.call_unregister_application(APP_PATH))
    finally:
        bus.disconnect()


async def _adapter_path(bus: Any) -> str:
    intro = await bus.introspect(_BLUEZ, "/")
    proxy = bus.get_proxy_object(_BLUEZ, "/", intro)
    manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await manager.call_get_managed_objects()
    return choose_adapter(objects)


async def _power_on(bus: Any, adapter_path: str) -> None:
    intro = await bus.introspect(_BLUEZ, adapter_path)
    proxy = bus.get_proxy_object(_BLUEZ, adapter_path, intro)
    adapter = proxy.get_interface("org.bluez.Adapter1")
    if not await adapter.get_powered():
        await adapter.set_powered(True)


async def _quiet(awaitable: Any) -> None:
    try:
        await awaitable
    except Exception:
        log.debug("Bluetooth cleanup failed", exc_info=True)


def _value_offset(options: Mapping[str, Any] | None) -> int:
    if not options:
        return 0
    raw = options.get("offset")
    if raw is None:
        return 0
    value = raw.value if hasattr(raw, "value") else raw
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
