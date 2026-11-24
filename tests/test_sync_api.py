import json
import threading
import urllib.error
import urllib.request

from mybolide.store import Store
from mybolide.sync_api import make_server

from tests.test_store import _add


def test_health_is_open_and_data_requires_the_token(tmp_path):
    store, base = _server(tmp_path, "secret-token")
    try:
        status, body = _request(f"{base}/health")
        assert status == 200
        assert body == {"status": "ok"}
        status, body = _request(f"{base}/v1/sync/signals")
        assert status == 401
        assert body == {"error": "unauthorized"}
        status, _ = _request(f"{base}/v1/sync/signals", token="wrong-token")
        assert status == 401
        status, body = _request(f"{base}/v1/sync/signals", token="secret-token")
        assert status == 200
        assert body["signals"] == []
        assert body["oldest_id"] is None
    finally:
        store.close()


def test_pagination_and_ack(tmp_path):
    store, base = _server(tmp_path, "secret-token")
    try:
        for _ in range(3):
            _add(store, "8004BF110329")
        status, page = _request(
            f"{base}/v1/sync/signals?after=0&limit=2", token="secret-token"
        )
        assert status == 200
        assert len(page["signals"]) == 2
        assert page["next_after"] == page["signals"][-1]["id"]
        status, rest = _request(
            f"{base}/v1/sync/signals?after={page['next_after']}&limit=2",
            token="secret-token",
        )
        assert len(rest["signals"]) == 1
        status, frames = _request(f"{base}/v1/sync/frames?limit=10", token="secret-token")
        assert len(frames["frames"]) == 3
        status, _ = _request(f"{base}/v1/sync/signals?limit=0", token="secret-token")
        assert status == 400
        status, _ = _request(f"{base}/v1/sync/signals?limit=5001", token="secret-token")
        assert status == 400
        status, _ = _request(f"{base}/v1/sync/signals?after=-1", token="secret-token")
        assert status == 400
        ack = json.dumps(
            {
                "signals_through": rest["signals"][-1]["id"],
                "frames_through": frames["frames"][-1]["id"],
            }
        ).encode()
        status, deleted = _request(
            f"{base}/v1/sync/ack",
            token="secret-token",
            data=ack,
            method="POST",
        )
        assert status == 200
        assert deleted == {"deleted_signals": 3, "deleted_frames": 3}
        status, empty = _request(f"{base}/v1/sync/frames", token="secret-token")
        assert empty["frames"] == []
        status, again = _request(
            f"{base}/v1/sync/ack",
            token="secret-token",
            data=ack,
            method="POST",
        )
        assert again == {"deleted_signals": 0, "deleted_frames": 0}
    finally:
        store.close()


def test_rejects_a_bad_ack(tmp_path):
    store, base = _server(tmp_path, "secret-token")
    try:
        for payload in (b"{}", b'{"signals_through": -1, "frames_through": 1}', b'{"signals_through": true, "frames_through": 1}'):
            status, body = _request(
                f"{base}/v1/sync/ack",
                token="secret-token",
                data=payload,
                method="POST",
            )
            assert status == 400
            assert body["error"] == "invalid ack"
        status, _ = _request(f"{base}/v1/sync/ack", data=b"{}", method="POST")
        assert status == 401
    finally:
        store.close()


def _server(tmp_path, token: str) -> tuple[Store, str]:
    store = Store(tmp_path / "bus.sqlite", max_db_bytes=50_000_000, batch_ms=1000)
    server = make_server("127.0.0.1", 0, store, token)
    thread = threading.Thread(target=server.serve_forever, name="test-api", daemon=True)
    thread.start()
    port = server.server_address[1]
    return store, f"http://127.0.0.1:{port}"


def _request(url: str, token: str | None = None, data: bytes | None = None, method: str | None = None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())
