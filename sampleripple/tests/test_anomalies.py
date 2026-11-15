from __future__ import annotations

import re

import pytest

from sampleripple.anomalies import CATEGORIES, RULES

POSITIVE_CASES = [
    ("gap-run", "GAP"),
    ("drift-run", "DRIFT"),
    ("dup-run", "DUPLICATE"),
    ("disorder-run", "DISORDER"),
    ("gap-run", "MISSING"),
    ("mixed-run", "EDGE_SKEW"),
    ("drift-run", "PERIOD_DRIFT"),
]


def _stream(report, stream_id):
    return next(item for item in report["streams"] if item["stream_id"] == stream_id)


def _categories(report, stream_id):
    return {item["category"] for item in _stream(report, stream_id)["anomalies"]}


@pytest.mark.parametrize(("stream_id", "category"), POSITIVE_CASES)
def test_each_category_has_a_positive_fixture(demo_report, stream_id, category):
    assert category in _categories(demo_report, stream_id)


@pytest.mark.parametrize("category", CATEGORIES)
def test_each_category_does_not_fire_on_clean(demo_report, category):
    assert category not in _categories(demo_report, "clean-grid")


def test_all_seven_categories_are_present(demo_report):
    assert set(demo_report["summary"]["by_category"]) == set(CATEGORIES)
    assert all(demo_report["summary"]["by_category"].values())
    assert set(demo_report["summary"]["by_rule"]) == set(RULES)


def test_anomaly_records_have_required_fields(demo_report):
    required = {
        "group_key", "index", "expected_ms", "actual_ms", "delta_ms",
        "rule_id", "category", "severity", "expected_at", "actual_at",
    }
    for stream in demo_report["streams"]:
        for anomaly in stream["anomalies"]:
            assert required <= set(anomaly)
            assert anomaly["rule_id"] in RULES
            assert anomaly["category"] in CATEGORIES
            assert anomaly["severity"] in {"info", "warning", "error", "critical"}
            stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
            assert stamp.match(anomaly["expected_at"])
            assert stamp.match(anomaly["actual_at"])


def test_gap_and_missing_are_linked(demo_report):
    anomalies = _stream(demo_report, "gap-run")["anomalies"]
    by_rule = {item["rule_id"]: item for item in anomalies}
    assert by_rule["GAP_ADJACENT"]["delta_ms"] == 60000
    assert by_rule["MISSING_SLOT"]["index"] == 3


def test_duplicate_is_reported_once(demo_report):
    anomalies = _stream(demo_report, "dup-run")["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["rule_id"] == "DUPLICATE_POINT"
    assert anomalies[0]["detail"] == "duplicate timestamp+sequence"


def test_disorder_delta_is_negative(demo_report):
    anomaly = _stream(demo_report, "disorder-run")["anomalies"][0]
    assert anomaly["rule_id"] == "DISORDER_LOCAL"
    assert anomaly["delta_ms"] < 0


def test_top_level_anomaly_list_matches_streams(demo_report):
    flattened = [
        anomaly
        for stream in demo_report["streams"]
        for anomaly in stream["anomalies"]
    ]
    assert demo_report["anomalies"] == flattened
    assert len(flattened) == demo_report["summary"]["anomaly_count"]


def _outlier_report(config_writer, tmp_path, estimator: str) -> dict:
    # Ten clean grid points plus one leverage outlier whose offset is close to
    # a full nominal period; tolerances are tuned so only PERIOD_DRIFT fires.
    rows = ["site,line,sensor,seq,ts,value"]
    for slot in range(10):
        rows.append(f"s,l,x,{slot},{slot * 60000},{slot}.0")
    rows.append(f"s,l,x,10,{10 * 60000 + 25000},10.0")
    source = tmp_path / "outlier.csv"
    source.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    from conftest import default_stream

    stream = default_stream(
        source,
        stream_id="outlier-run",
        missing_policy="ignore",
        disorder_window=0,
        duplicate_policy="ignore",
        drift_tolerance_ms=100000,
        period_drift_tolerance_ms=500,
        edge_tolerance_ms=100000,
        gap_tolerance_ms=100000,
        period_estimator=estimator,
    )
    config = config_writer([stream])
    from sampleripple.config import load_config
    from sampleripple.engine import run_verification
    from sampleripple.report_json import build_report

    return build_report(run_verification(load_config(config)))


def _period_drift(report: dict) -> dict | None:
    anomalies = report["streams"][0]["anomalies"]
    matches = [item for item in anomalies if item["rule_id"] == "PERIOD_DRIFT_FIT"]
    return matches[0] if matches else None


def test_robust_estimator_keeps_period_drift_silent_on_single_outlier(
    config_writer, tmp_path
):
    report = _outlier_report(config_writer, tmp_path, "robust")
    group = report["streams"][0]["groups"][0]
    assert group["period_fit_ms"] == pytest.approx(60000.0)
    assert _period_drift(report) is None
    assert group["status"] == "ok"


def test_least_squares_estimator_flags_the_same_outlier(config_writer, tmp_path):
    report = _outlier_report(config_writer, tmp_path, "least_squares")
    group = report["streams"][0]["groups"][0]
    anomaly = _period_drift(report)
    assert group["period_fit_ms"] == pytest.approx(61136.364, abs=1e-3)
    assert anomaly is not None
    assert anomaly["fit_period_ms"] == group["period_fit_ms"]
    assert anomaly["detail"] == "least_squares_period_ms=61136.364"
    assert group["status"] == "anomalous"


def test_period_drift_expected_ms_uses_the_fit_last_slot(config_writer, tmp_path):
    report = _outlier_report(config_writer, tmp_path, "least_squares")
    anomaly = _period_drift(report)
    assert anomaly["index"] == 10
    assert anomaly["expected_ms"] == 10 * 60000
    assert anomaly["actual_ms"] == 10 * 60000 + 25000
    assert anomaly["delta_ms"] == 25000


def test_robust_detail_labels_the_estimator_on_drift_fixture(demo_report):
    drift = next(
        item
        for stream in demo_report["streams"]
        if stream["stream_id"] == "drift-run"
        for item in stream["anomalies"]
        if item["rule_id"] == "PERIOD_DRIFT_FIT"
    )
    group = next(
        group
        for stream in demo_report["streams"]
        if stream["stream_id"] == "drift-run"
        for group in stream["groups"]
    )
    assert drift["detail"] == "robust_period_ms=60300.000"
    assert drift["fit_period_ms"] == group["period_fit_ms"] == 60300.0
