from pathlib import Path

from mybolide.parser import Frame, FrameParser

FIXTURE = Path(__file__).parent / "fixtures" / "e46_ike.hex"


def fixture_bytes() -> bytes:
    chunks = []
    for line in FIXTURE.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if line:
            chunks.append(bytes.fromhex(line))
    return b"".join(chunks)


def parse_one(hex_text: str) -> Frame:
    parser = FrameParser()
    parser.feed(bytes.fromhex(hex_text), now=0)
    frames = parser.feed(b"", now=1)
    assert len(frames) == 1
    return frames[0]
