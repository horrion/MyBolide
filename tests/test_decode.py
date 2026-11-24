from mybolide.decode import decode_frame

from tests.support import parse_one


def _values(hex_text: str) -> dict[str, float]:
    return {signal.name: signal.value for signal in decode_frame(parse_one(hex_text))}


def test_fixture_signals():
    assert _values("8005BF1841197A") == {"speed_kmh": 130, "rpm": 2500}
    assert _values("8004BF110329") == {"ignition": 2}
    assert _values("8006BF1917370000") == {"ambient_c": 23, "coolant_c": 55}
    assert _values("800ABF17FA340C001F32CC01") == {"odometer_km": 799994}
    assert _values("8006BF1902630041") == {"ambient_c": 2, "coolant_c": 99}


def test_invalid_speed_is_omitted():
    assert _values("8005BF18FF19C4") == {"rpm": 2500}


def test_ignition_levels():
    assert _values("8004BF11002A")["ignition"] == 0
    assert _values("8004BF11012B")["ignition"] == 1
    assert _values("8004BF110329")["ignition"] == 2
    assert _values("8004BF11072D")["ignition"] == 3


def test_negative_temperatures():
    assert _values("8006BF190AE200C8") == {"ambient_c": 10, "coolant_c": -30}
    assert _values("8006BF19F63700E1") == {"ambient_c": -10, "coolant_c": 55}


def test_coolant_extension():
    assert _values("8006BF190A7F0550")["coolant_c"] == 132
    assert _values("8006BF190A7F80D5")["coolant_c"] == 255


def test_non_ike_frame_has_no_signals():
    assert decode_frame(parse_one("50046832111F")) == []


def test_extended_sensors_frame_reports_fuel_in_litres():
    # 80 0A BF 13 00 00 00 00 00 00 47 61, fuel byte 0x47.
    values = {
        signal.name: signal.value
        for signal in decode_frame(parse_one("800ABF130000000000004761"), tank_litres=255)
    }
    assert values == {"fuel_l": 71.0}
    values = {
        signal.name: signal.value
        for signal in decode_frame(parse_one("800ABF130000000000004761"), tank_litres=63)
    }
    assert values["fuel_l"] == 71 * 63 / 255


def test_short_sensors_frame_has_no_fuel_reading():
    assert decode_frame(parse_one("8006BF1303000029")) == []
