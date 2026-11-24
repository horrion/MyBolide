import json
import threading

import pytest

from mybolide.ble import (
    CHARACTERISTIC_UUID,
    SERVICE_UUID,
    Advertisement,
    Application,
    GattCharacteristic,
    PresenceAdvertiser,
    advertisement_properties,
    characteristic_bytes,
    characteristic_value,
    choose_adapter,
    presence_payload,
    unacked,
)


class _Cache:
    def __init__(self, frames: int, signals: int) -> None:
        self._frames = frames
        self._signals = signals

    def counts(self) -> tuple[int, int]:
        return self._frames, self._signals


def test_payload_is_the_port_and_the_pending_flag():
    body = json.loads(presence_payload(8741, True))
    assert body == {"pending": True, "port": 8741}
    assert set(body) == {"pending", "port"}
    assert b"token" not in presence_payload(8741, False)


def test_payload_rejects_a_port_or_a_flag_the_phone_cannot_read():
    with pytest.raises(ValueError):
        presence_payload(0, True)
    with pytest.raises(ValueError):
        presence_payload(True, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        presence_payload(8741, 1)  # type: ignore[arg-type]


def test_pending_flag_is_true_when_either_table_has_rows():
    assert unacked(_Cache(0, 0)) is False
    assert unacked(_Cache(2, 0)) is True
    assert unacked(_Cache(0, 4)) is True


def test_a_store_error_still_tells_the_phone_to_try_http():
    def broken() -> bool:
        raise RuntimeError("database is closed")

    body = json.loads(characteristic_value(8741, broken))
    assert body["pending"] is True
    assert body["port"] == 8741


def test_read_honors_an_offset_and_skips_the_token():
    class _Offset:
        def __init__(self, value: int) -> None:
            self.value = value

    full = characteristic_bytes(8741, lambda: False, {})
    offset = characteristic_bytes(8741, lambda: False, {"offset": _Offset(9)})
    assert bytes(full).startswith(b'{"pending"')
    assert bytes(offset) == bytes(full)[9:]
    assert b"token" not in bytes(full)


def test_gatt_read_returns_the_payload_and_nothing_secret():
    characteristic = GattCharacteristic(8741, lambda: True)
    read = characteristic.ReadValue.__dict__["__DBUS_METHOD"].fn
    body = json.loads(bytes(read(characteristic, {})))
    assert body == {"pending": True, "port": 8741}
    managed = Application().GetManagedObjects.__dict__["__DBUS_METHOD"].fn(Application())
    encoded = str(managed)
    assert SERVICE_UUID in encoded
    assert CHARACTERISTIC_UUID in encoded
    assert "token" not in encoded
    advertisement = Advertisement()
    assert advertisement.Type == "peripheral"
    assert advertisement.ServiceUUIDs == [SERVICE_UUID]


def test_advertisement_carries_only_the_service_uuid():
    props = advertisement_properties()
    assert props["ServiceUUIDs"] == [SERVICE_UUID]
    assert CHARACTERISTIC_UUID not in props["ServiceUUIDs"]
    assert "token" not in props
    assert SERVICE_UUID != CHARACTERISTIC_UUID


def test_adapter_choice_prefers_the_builtin_controller():
    objects = {
        "/org/bluez/hci1": {"org.bluez.LEAdvertisingManager1": {}},
        "/org/bluez/hci0": {"org.bluez.LEAdvertisingManager1": {}},
        "/org/bluez/hci0/dev_xx": {},
    }
    assert choose_adapter(objects) == "/org/bluez/hci0"
    assert (
        choose_adapter({"/org/bluez/hci1": {"org.bluez.LEAdvertisingManager1": {}}})
        == "/org/bluez/hci1"
    )
    with pytest.raises(OSError, match="no Bluetooth LE adapter"):
        choose_adapter({"/org/bluez": {}})


def test_missing_bluetooth_does_not_stop_the_caller():
    started = threading.Event()

    def advertise(port: int, pending, stop: threading.Event) -> None:
        assert port == 8741
        assert pending() is False
        started.set()
        raise OSError("org.bluez is not available")

    beacon = PresenceAdvertiser(
        8741,
        lambda: False,
        advertise=advertise,
        retry_s=0.01,
    )
    beacon.start()
    assert started.wait(2)
    assert beacon._thread is not None
    assert beacon._thread.is_alive()
    beacon.close()
    assert not beacon._thread.is_alive()


def test_beacon_thread_stops_when_closed():
    ran = threading.Event()

    def advertise(port: int, pending, stop: threading.Event) -> None:
        assert pending() is True
        ran.set()
        stop.wait(2)

    beacon = PresenceAdvertiser(8741, lambda: True, advertise=advertise)
    beacon.start()
    assert ran.wait(2)
    beacon.close()
    assert beacon._thread is not None
    assert not beacon._thread.is_alive()
