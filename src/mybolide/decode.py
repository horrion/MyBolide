"""Decode the IKE broadcasts that are worth graphing.

Everything else is kept as a raw frame and is not turned into a signal.
Formulas follow published IKE reconstructions, checked against captured frames:

- Speed and RPM: byte * 2 km/h and byte * 100 rpm. Speed 0xFF is invalid.
- Temperature: signed Celsius. Coolant 0x7F is an escape for values above 126.
  A Rover 75 / MG ZT capture of the same IKE family,
  ``80 06 BF 19 02 63 00 41``, is outside 2 C and coolant 99 C.
- Odometer: first three payload bytes, little-endian kilometres.
- Ignition: bit 0 accessory, bit 1 run, bit 2 starter. The stored value is the
  highest active position: 0 off, 1 accessory, 2 run, 3 start.
- Fuel: extended sensors broadcast, byte 7, 0 empty through 255 full. Litres
  are that fraction of ``tank_litres``. Clusters from before about 2000 omit
  the byte, and those frames stay raw.
"""

from __future__ import annotations

from dataclasses import dataclass

from mybolide.parser import Frame

IKE = 0x80


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    value: float


@dataclass(frozen=True, slots=True)
class LinearField:
    command: int
    name: str
    index: int
    scale: float
    bias: float = 0.0
    invalid: int | None = None


LINEAR_FIELDS: tuple[LinearField, ...] = (
    LinearField(0x18, "speed_kmh", 0, 2.0, invalid=0xFF),
    LinearField(0x18, "rpm", 1, 100.0),
)


def decode_frame(frame: Frame, *, tank_litres: float = 63.0) -> list[Signal]:
    if frame.source != IKE or frame.command is None:
        return []
    signals: list[Signal] = []
    payload = frame.payload
    for field in LINEAR_FIELDS:
        if frame.command != field.command or field.index >= len(payload):
            continue
        raw = payload[field.index]
        if field.invalid is not None and raw == field.invalid:
            continue
        signals.append(Signal(field.name, raw * field.scale + field.bias))
    if frame.command == 0x19:
        signals.extend(_temperature(payload))
    elif frame.command == 0x17:
        signals.extend(_odometer(payload))
    elif frame.command == 0x11:
        signals.extend(_ignition(payload))
    elif frame.command == 0x13:
        signals.extend(_fuel(payload, tank_litres))
    return signals


def _temperature(payload: bytes) -> list[Signal]:
    if len(payload) < 2:
        return []
    ambient = _signed(payload[0])
    coolant_byte = payload[1]
    extension = payload[2] if len(payload) > 2 else 0
    if coolant_byte == 0x7F:
        if extension & 0x80:
            coolant = 0xFF
        else:
            coolant = 0x7F + extension
    else:
        coolant = _signed(coolant_byte)
    return [
        Signal("ambient_c", float(ambient)),
        Signal("coolant_c", float(coolant)),
    ]


def _fuel(payload: bytes, tank_litres: float) -> list[Signal]:
    if len(payload) < 7:
        return []
    litres = payload[6] * tank_litres / 255.0
    return [Signal("fuel_l", litres)]


def _odometer(payload: bytes) -> list[Signal]:
    if len(payload) < 3:
        return []
    kilometres = payload[0] | (payload[1] << 8) | (payload[2] << 16)
    return [Signal("odometer_km", float(kilometres))]


def _ignition(payload: bytes) -> list[Signal]:
    if not payload:
        return []
    status = payload[0]
    if status & 0x04:
        level = 3
    elif status & 0x02:
        level = 2
    elif status & 0x01:
        level = 1
    else:
        level = 0
    return [Signal("ignition", float(level))]


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value
