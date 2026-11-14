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
