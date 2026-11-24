from pathlib import Path

from mybolide.ble import CHARACTERISTIC_UUID, SERVICE_UUID

ROOT = Path(__file__).resolve().parents[1]


def test_contract_lists_the_sync_routes():
    text = (ROOT / "openapi.yaml").read_text(encoding="utf-8")
    for path in ("/health", "/v1/sync/signals", "/v1/sync/frames", "/v1/sync/ack"):
        assert path in text
    for field in (
        "signals_through",
        "frames_through",
        "deleted_signals",
        "deleted_frames",
        "oldest_id",
        "next_after",
        "raw_hex",
        "speed_kmh",
    ):
        assert field in text
    assert SERVICE_UUID in text
    assert CHARACTERISTIC_UUID in text
    assert "pending" in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert SERVICE_UUID in readme
    assert CHARACTERISTIC_UUID in readme
