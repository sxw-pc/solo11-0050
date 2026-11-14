from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from sampleripple.engine import run_verification
from sampleripple.errors import AppError
from sampleripple.ledger import record_run
from sampleripple.server import create_server
from sampleripple.util.hashing import sha256_text


@contextmanager
def _server(config, report):
    verification = run_verification(config)
    run_id = record_run(
        config.ledger.db_path,
        started_at="2026-01-01T00:00:00.000Z",
        config_hash=config.config_hash,
        config_path="configs/demo.yaml",
        report_sha256=sha256_text("report"),
        report=report,
        anomalies=verification.anomalies,
    )
    server = create_server(config, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield host, port, run_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(host, port, path):
    connection = http.client.HTTPConnection(str(host), int(port), timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        return response.status, body
    finally:
        connection.close()


def test_http_read_only_endpoints(temp_demo_config, demo_report):
    with _server(temp_demo_config, demo_report) as (host, port, run_id):
        status, health = _get(host, port, "/healthz")
        assert status == 200 and health["status"] == "ok"
        status, runs = _get(host, port, "/runs?limit=5&offset=0")
        assert status == 200
        assert runs["total"] == 1
        assert runs["runs"][0]["run_id"] == run_id
        status, anomalies = _get(
            host, port, f"/runs/{run_id}/anomalies?rule_id=GAP_ADJACENT"
        )
        assert status == 200
        assert anomalies["total"] == 2
        assert all(item["rule_id"] == "GAP_ADJACENT" for item in anomalies["anomalies"])
        status, _ = _get(host, port, "/runs/999999/anomalies")
        assert status == 404
        status, _ = _get(host, port, "/does-not-exist")
        assert status == 404
        status, _ = _get(host, port, "/runs?limit=0")
        assert status == 400


def test_http_rejects_non_get(temp_demo_config, demo_report):
    with _server(temp_demo_config, demo_report) as (host, port, _):
        connection = http.client.HTTPConnection(str(host), int(port), timeout=3)
        try:
            connection.request("POST", "/runs", body=b"{}")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 405
            assert payload["error"]["code"] == "METHOD_NOT_ALLOWED"
        finally:
            connection.close()


def test_invalid_listen_address_is_rejected(temp_demo_config):
    with pytest.raises(AppError) as caught:
        create_server(temp_demo_config, host="0.0.0.0", port=0)
    assert caught.value.code == "SRV_BAD_HOST"
    with pytest.raises(AppError):
        create_server(temp_demo_config, host="not-loopback.invalid", port=0)
