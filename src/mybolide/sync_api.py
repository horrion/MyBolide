"""Pull-and-ack HTTP API for the later iPhone app."""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from mybolide.store import Store

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
MAX_BODY = 4096


class SyncHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], store: Store, token: str) -> None:
        if not token:
            raise ValueError("sync API requires a token")
        super().__init__(server_address, SyncHandler)
        self.store = store
        self.token = token


class SyncHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: SyncHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._get()
        except Exception:
            log.exception("sync request failed")
            self._send(500, {"error": "internal"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._post()
        except Exception:
            log.exception("sync request failed")
            self._send(500, {"error": "internal"})

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def _get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        after, limit = _page_args(parse_qs(parsed.query))
        if after is None or limit is None:
            self._send(400, {"error": "invalid after or limit"})
            return
        if parsed.path == "/v1/sync/signals":
            rows, oldest = self.server.store.signals_page(after, limit)
            self._send(200, _page_body("signals", rows, after, oldest))
            return
        if parsed.path == "/v1/sync/frames":
            rows, oldest = self.server.store.frames_page(after, limit)
            self._send(200, _page_body("frames", rows, after, oldest))
            return
        self._send(404, {"error": "not found"})

    def _post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/v1/sync/ack":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        body = self._json_body()
        if body is None:
            self._send(400, {"error": "invalid json"})
            return
        signals_through = _nonneg_int(body.get("signals_through"))
        frames_through = _nonneg_int(body.get("frames_through"))
        if signals_through is None or frames_through is None:
            self._send(400, {"error": "invalid ack"})
            return
        deleted_signals, deleted_frames = self.server.store.ack(
            signals_through=signals_through,
            frames_through=frames_through,
        )
        self._send(
            200,
            {
                "deleted_signals": deleted_signals,
                "deleted_frames": deleted_frames,
            },
        )

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        presented = header[len(prefix) :]
        expected = self.server.token
        if len(presented) != len(expected):
            return False
        return hmac.compare_digest(presented, expected)

    def _json_body(self) -> dict | None:
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            return None
        length = int(raw_length)
        if length < 2 or length > MAX_BODY:
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="mybolide"')
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, store: Store, token: str) -> SyncHTTPServer:
    return SyncHTTPServer((host, port), store, token)


def _page_args(query: dict[str, list[str]]) -> tuple[int | None, int | None]:
    after = _query_int(query, "after", 0)
    limit = _query_int(query, "limit", DEFAULT_LIMIT)
    if after is None or limit is None or after < 0 or limit < 1 or limit > MAX_LIMIT:
        return None, None
    return after, limit


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int | None:
    if name not in query or not query[name]:
        return default
    text = query[name][-1]
    if text.startswith("-"):
        if text[1:].isdigit():
            return -int(text[1:])
        return None
    if not text.isdigit():
        return None
    return int(text)


def _nonneg_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _page_body(key: str, rows: list[dict], after: int, oldest: int | None) -> dict:
    next_after = rows[-1]["id"] if rows else after
    return {key: rows, "next_after": next_after, "oldest_id": oldest}
