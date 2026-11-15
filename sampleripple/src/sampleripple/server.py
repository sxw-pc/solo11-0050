"""Local-only, read-only HTTP access to the SQLite ledger."""

from __future__ import annotations

import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .anomalies import CATEGORIES
from .config import AppConfig, LOOPBACK_HOSTS
from .errors import AppError
from .ledger import get_run, list_anomalies, list_runs

ANOMALY_PATH = re.compile(r"^/runs/(\d+)/anomalies$")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def normalize_bind_host(host: str) -> str:
    if not isinstance(host, str):
        raise AppError("SRV_BAD_HOST", "bind host must be a string")
    normalized = host.strip().lower()
    if normalized not in LOOPBACK_HOSTS:
        raise AppError(
            "SRV_BAD_HOST",
            "only loopback binds are allowed (127.0.0.1, localhost, ::1)",
        )
    return "127.0.0.1" if normalized == "localhost" else normalized


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    values = query.get(name)
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError) as exc:
        raise AppError("HTTP_BAD_QUERY", f"{name} must be an integer") from exc


def _query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values else None


def _category_filter(query: dict[str, list[str]]) -> str | None:
    raw = _query_value(query, "category")
    if raw is None:
        return None
    category = raw.strip().upper()
    if category not in CATEGORIES:
        raise AppError(
            "HTTP_BAD_QUERY",
            "category must be one of " + ", ".join(CATEGORIES),
        )
    return category


def _group_key_filter(query: dict[str, list[str]]) -> str | None:
    group_key = _query_value(query, "group_key")
    if group_key is not None and not group_key.strip():
        raise AppError("HTTP_BAD_QUERY", "group_key must not be blank")
    return group_key


class RippleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RippleHTTPServerV6(RippleHTTPServer):
    address_family = socket.AF_INET6


def _make_handler(db_path: Path) -> type[BaseHTTPRequestHandler]:
    class LedgerHandler(BaseHTTPRequestHandler):
        server_version = f"SampleRipple/{__version__}"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            self._send_json(status, {"error": {"code": code, "message": message}})

        def _pagination(self, query: dict[str, list[str]]) -> tuple[int, int]:
            limit = _query_int(query, "limit", DEFAULT_LIMIT)
            offset = _query_int(query, "offset", 0)
            if not 1 <= limit <= MAX_LIMIT:
                raise AppError("HTTP_BAD_QUERY", f"limit must be 1..{MAX_LIMIT}")
            if offset < 0:
                raise AppError("HTTP_BAD_QUERY", "offset must be >= 0")
            return limit, offset

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                if path == "/healthz":
                    self._send_json(
                        200, {"status": "ok", "version": __version__}
                    )
                    return
                if path == "/runs":
                    limit, offset = self._pagination(query)
                    runs, total = list_runs(db_path, limit=limit, offset=offset)
                    self._send_json(
                        200,
                        {
                            "limit": limit,
                            "offset": offset,
                            "runs": runs,
                            "total": total,
                        },
                    )
                    return
                match = ANOMALY_PATH.fullmatch(path)
                if match:
                    self._anomalies(int(match.group(1)), query)
                    return
                self._error(404, "HTTP_NOT_FOUND", "route not found")
            except AppError as exc:
                self._error(400, exc.code, exc.message)

        def _anomalies(self, run_id: int, query: dict[str, list[str]]) -> None:
            if get_run(db_path, run_id) is None:
                self._error(404, "RUN_NOT_FOUND", f"run {run_id} not found")
                return
            limit, offset = self._pagination(query)
            rule_id = _query_value(query, "rule_id")
            group_key = _group_key_filter(query)
            category = _category_filter(query)
            anomalies, total = list_anomalies(
                db_path,
                run_id,
                rule_id=rule_id,
                group_key=group_key,
                category=category,
                limit=limit,
                offset=offset,
            )
            self._send_json(
                200,
                {
                    "anomalies": anomalies,
                    "category": category,
                    "group_key": group_key,
                    "limit": limit,
                    "offset": offset,
                    "rule_id": rule_id,
                    "run_id": run_id,
                    "total": total,
                },
            )

        def _discard_body(self) -> None:
            """Consume a small request body so closing cannot reset the socket."""

            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                return
            if 0 < length <= 65536:
                self.rfile.read(length)

        def _method_not_allowed(self) -> None:
            self._discard_body()
            self._error(405, "METHOD_NOT_ALLOWED", "only GET is supported")

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_HEAD = _method_not_allowed

    return LedgerHandler


def create_server(
    config: AppConfig,
    *,
    host: str | None = None,
    port: int | None = None,
) -> RippleHTTPServer:
    """Bind a loopback server; nothing is served until ``serve_forever``."""

    bind_host = normalize_bind_host(host if host is not None else config.server.host)
    bind_port = port if port is not None else config.server.port
    if isinstance(bind_port, bool) or not isinstance(bind_port, int):
        raise AppError("SRV_BAD_PORT", "bind port must be an integer")
    if not 0 <= bind_port <= 65535:
        raise AppError("SRV_BAD_PORT", "bind port must be 0..65535")
    cls = RippleHTTPServerV6 if ":" in bind_host else RippleHTTPServer
    try:
        return cls((bind_host, bind_port), _make_handler(config.ledger.db_path))
    except OSError as exc:
        raise AppError(
            "SRV_BIND", f"cannot bind {bind_host}:{bind_port}: {exc}"
        ) from exc
