from __future__ import annotations

import http.client
import json
import threading
from collections import Counter
from contextlib import contextmanager
from urllib.parse import urlencode

import pytest

from sampleripple import __version__
from sampleripple.anomalies import RULE_CATEGORY, Anomaly, anomaly_sort_key
from sampleripple.engine import run_verification
from sampleripple.errors import AppError
from sampleripple.ledger import record_run
from sampleripple.report_json import build_report
from sampleripple.server import create_server
from sampleripple.util.hashing import sha256_text


@contextmanager
def _serve(config, report, anomalies):
    run_id = record_run(
        config.ledger.db_path,
        started_at="2026-01-01T00:00:00.000Z",
        config_hash=config.config_hash,
        config_path="configs/demo.yaml",
        report_sha256=sha256_text("report"),
        report=report,
        anomalies=anomalies,
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


@contextmanager
def _server(config, report):
    verification = run_verification(config)
    with _serve(config, report, verification.anomalies) as ctx:
        yield ctx


def _get(host, port, path):
    connection = http.client.HTTPConnection(str(host), int(port), timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        return response.status, body
    finally:
        connection.close()


def _query(host, port, run_id, **params):
    return _get(host, port, f"/runs/{run_id}/anomalies?{urlencode(params)}")


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


GROUP_ONE = "site-a/line-07/sensor-x"
GROUP_TWO = "site-b/line-07/sensor-x"
PROJECTED_KEYS = (
    "stream_id", "group_key", "index", "expected_ms", "actual_ms",
    "delta_ms", "rule_id", "category", "severity",
)


def _craft(stream_id, group_key, index, rule_id, expected, actual, detail):
    return Anomaly(
        stream_id=stream_id,
        group_key=group_key,
        index=index,
        expected_ms=expected,
        actual_ms=actual,
        delta_ms=actual - expected,
        rule_id=rule_id,
        category=RULE_CATEGORY[rule_id],
        severity="error",
        detail=detail,
    )


def _crafted_anomalies() -> list[Anomaly]:
    """One run where detail alone decides the order of two same-group GAPs."""

    crafted = [
        _craft("crafted-a", GROUP_ONE, 3, "GAP_ADJACENT", 500, 900,
               "gap threshold 10ms exceeded"),
        _craft("crafted-a", GROUP_ONE, 5, "DUPLICATE_POINT", 700, 700,
               "duplicate timestamp"),
        _craft("crafted-a", GROUP_ONE, 7, "GAP_ADJACENT", 1000, 2000,
               "gap threshold 10ms exceeded"),
        _craft("crafted-b", GROUP_ONE, 7, "GAP_ADJACENT", 1000, 2000,
               "gap threshold 9ms exceeded"),
        _craft("crafted-b", GROUP_ONE, 9, "DRIFT_CUMULATIVE", 1500, 1700,
               "trailing_mean_residual_ms=600.000"),
        _craft("crafted-b", GROUP_TWO, 1, "GAP_ADJACENT", 100, 500,
               "gap threshold 10ms exceeded"),
    ]
    return sorted(crafted, key=anomaly_sort_key)


def _bare_report() -> dict:
    return {
        "summary": {"stream_count": 2, "group_count": 2, "actual_points": 42},
        "tool": {"name": "sampleripple", "version": __version__},
    }


def _projection(anomaly: Anomaly) -> dict:
    return {key: getattr(anomaly, key) for key in PROJECTED_KEYS}


def test_anomaly_filters_compose_and_follow_report_order(temp_demo_config):
    anomalies = _crafted_anomalies()
    with _serve(temp_demo_config, _bare_report(), anomalies) as (host, port, run_id):
        status, body = _query(host, port, run_id, group_key=GROUP_ONE,
                              category="GAP")
        assert status == 200
        assert body["total"] == 3
        assert body["group_key"] == GROUP_ONE
        assert body["category"] == "GAP"
        assert body["rule_id"] is None
        expected = [
            item for item in anomalies
            if item.group_key == GROUP_ONE and item.category == "GAP"
        ]
        assert body["anomalies"] == [_projection(item) for item in expected]
        # The index-7 pair ties on every stored column; detail decides:
        # "…10ms…" sorts before "…9ms…" (string comparison).
        assert [item["stream_id"] for item in body["anomalies"]] == [
            "crafted-a", "crafted-a", "crafted-b",
        ]
        status, and_body = _query(host, port, run_id, group_key=GROUP_ONE,
                                  category="GAP", rule_id="GAP_ADJACENT")
        assert status == 200 and and_body["anomalies"] == body["anomalies"]
        status, body = _query(host, port, run_id, group_key=GROUP_ONE,
                              category="GAP", rule_id="DUPLICATE_POINT")
        assert status == 200 and body["total"] == 0 and body["anomalies"] == []
        status, body = _query(host, port, run_id, category="GAP")
        assert status == 200
        expected = [item for item in anomalies if item.category == "GAP"]
        assert len(expected) == 4
        assert body["anomalies"] == [_projection(item) for item in expected]
        status, body = _query(host, port, run_id, group_key=GROUP_ONE)
        assert status == 200 and body["total"] == 5
        status, body = _query(host, port, run_id,
                              group_key="site-z/line-99/sensor-q")
        assert status == 200 and body["total"] == 0


def test_anomaly_pagination_is_consistent(temp_demo_config):
    anomalies = _crafted_anomalies()
    with _serve(temp_demo_config, _bare_report(), anomalies) as (host, port, run_id):
        params = {"group_key": GROUP_ONE, "category": "GAP"}
        _, single = _query(host, port, run_id, limit=100, **params)
        pages, totals, offset = [], [], 0
        while True:
            status, body = _query(host, port, run_id, limit=2, offset=offset,
                                  **params)
            assert status == 200
            totals.append(body["total"])
            pages.append(body["anomalies"])
            if len(body["anomalies"]) < 2:
                break
            offset += 2
        flat = [item for page in pages for item in page]
        assert flat == single["anomalies"]
        assert totals == [3] * len(pages)
        serialized = [json.dumps(item, sort_keys=True) for item in flat]
        assert len(set(serialized)) == len(serialized)
        walked = []
        for offset in range(len(anomalies) + 1):
            _, body = _query(host, port, run_id, limit=1, offset=offset)
            assert body["total"] == len(anomalies)
            walked.extend(body["anomalies"])
        assert walked == [_projection(item) for item in anomalies]


@pytest.mark.parametrize(
    "params",
    [
        {"group_key": ""},
        {"group_key": "   "},
        {"category": ""},
        {"category": "VELOCITY"},
        {"limit": 0},
        {"limit": 101},
        {"limit": "many"},
        {"offset": -1},
    ],
)
def test_anomaly_filter_validation_errors(temp_demo_config, params):
    anomalies = _crafted_anomalies()
    with _serve(temp_demo_config, _bare_report(), anomalies) as (host, port, run_id):
        status, body = _query(host, port, run_id, **params)
        assert status == 400
        assert body["error"]["code"] == "HTTP_BAD_QUERY"


def test_anomaly_filter_edge_cases(temp_demo_config):
    anomalies = _crafted_anomalies()
    with _serve(temp_demo_config, _bare_report(), anomalies) as (host, port, run_id):
        status, body = _query(host, port, run_id, category="gap")
        assert status == 200
        assert body["category"] == "GAP"
        assert body["total"] == 4
        status, body = _query(host, port, 999999, group_key="")
        assert status == 404
        assert body["error"]["code"] == "RUN_NOT_FOUND"


def test_anomalies_follow_report_json_order(temp_demo_config):
    verification = run_verification(temp_demo_config)
    report = build_report(verification)
    with _serve(temp_demo_config, report, verification.anomalies) as (host, port, run_id):
        expected = [
            {key: item[key] for key in PROJECTED_KEYS}
            for item in report["anomalies"]
        ]
        status, body = _query(host, port, run_id, limit=100)
        assert status == 200
        assert body["total"] == len(expected) == 15
        assert body["anomalies"] == expected
        counts = Counter(
            (item["group_key"], item["category"]) for item in expected
        )
        (group_key, category), count = counts.most_common(1)[0]
        assert count >= 2
        focus = [
            item for item in expected
            if item["group_key"] == group_key and item["category"] == category
        ]
        status, body = _query(host, port, run_id, group_key=group_key,
                              category=category)
        assert status == 200
        assert body["total"] == count
        assert body["anomalies"] == focus
