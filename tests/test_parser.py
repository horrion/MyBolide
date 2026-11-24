from mybolide.parser import MAX_LENGTH, FrameParser, checksum

from tests.support import FIXTURE, fixture_bytes, parse_one

SPEED_RPM = "8005BF1841197A"


def test_fixture_checksums_and_frame_count():
    expected = [
        line.strip().lower()
        for line in FIXTURE.read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    parser = FrameParser(gap_s=0.05)
    assert parser.feed(fixture_bytes(), now=0) == []
    frames = parser.feed(b"", now=0.05)
    assert [frame.raw.hex() for frame in frames] == expected
    for frame in frames:
        assert checksum(frame.raw[:-1]) == frame.raw[-1]


def test_speed_rpm_frame_fields():
    frame = parse_one(SPEED_RPM)
    assert frame.source == 0x80
    assert frame.destination == 0xBF
    assert frame.command == 0x18
    assert frame.payload == bytes.fromhex("4119")


def test_drops_leading_noise_byte():
    frames = _parse(bytes.fromhex("FF" + SPEED_RPM))
    assert len(frames) == 1
    assert frames[0].command == 0x18


def test_resyncs_after_bad_checksum():
    bad = bytes.fromhex("8005BF18411900")
    good = bytes.fromhex(SPEED_RPM)
    frames = _parse(bad + good)
    assert [frame.raw for frame in frames] == [good]


def test_rejects_length_above_maximum():
    assert MAX_LENGTH == 34
    noise = bytes([0x7F, MAX_LENGTH + 1])
    frames = _parse(noise + bytes.fromhex(SPEED_RPM))
    assert len(frames) == 1
    assert frames[0].raw.hex() == SPEED_RPM.lower()


def test_gap_discards_a_partial_frame():
    parser = FrameParser(gap_s=0.05)
    assert parser.feed(bytes.fromhex("8005BF"), now=0) == []
    assert parser.feed(b"", now=0.05) == []
    parser.feed(bytes.fromhex(SPEED_RPM), now=0.05)
    frames = parser.feed(b"", now=0.10)
    assert [frame.raw.hex() for frame in frames] == [SPEED_RPM.lower()]


def test_short_silence_keeps_a_partial_frame():
    parser = FrameParser(gap_s=0.05)
    parser.feed(bytes.fromhex("8005"), now=0)
    assert parser.feed(b"", now=0.02) == []
    parser.feed(bytes.fromhex("BF1841197A"), now=0.02)
    frames = parser.feed(b"", now=0.10)
    assert len(frames) == 1
    assert frames[0].raw.hex() == SPEED_RPM.lower()


def test_buffer_limit_keeps_a_trailing_partial_frame():
    parser = FrameParser(gap_s=10, max_buffer=8)
    frame = bytes.fromhex(SPEED_RPM)
    first = parser.feed(frame + frame[:2], now=0)
    assert [item.raw for item in first] == [frame]
    assert parser.feed(frame[2:], now=0) == []
    assert [item.raw for item in parser.feed(b"", now=10)] == [frame]


def test_buffer_limit_parses_without_waiting_for_a_gap():
    parser = FrameParser(gap_s=10, max_buffer=8)
    frame = bytes.fromhex(SPEED_RPM)
    frames = parser.feed(frame + frame, now=0)
    assert [item.raw for item in frames] == [frame, frame]


def _parse(data: bytes):
    parser = FrameParser()
    parser.feed(data, now=0)
    return parser.feed(b"", now=1)
