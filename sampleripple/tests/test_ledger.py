from __future__ import annotations

from pathlib import Path

from sampleripple.engine import run_verification
from sampleripple.ledger import (
    get_run,
    list_anomalies,
    list_runs,
    record_run,
)
from sampleripple.util.hashing import sha256_text


def _record(config, report, db_path: Path, started: str = "2026-01-01T00:00:00.000Z"):
    verification = run_verification(config)
    return record_run(
        db_path,
        started_at=started,
        config_hash=config.config_hash,
        config_path="configs/demo.yaml",
        report_sha256=sha256_text("report"),
        report=report,
        anomalies=verification.anomalies,
    )


def test_ledger_append_and_query(demo_config, demo_report, tmp_path: Path):
    db_path = tmp_path / "ledger.db"
    first = _record(demo_config, demo_report, db_path)
    second = _record(demo_config, demo_report, db_path, "2026-01-02T00:00:00.000Z")
    assert second > first
    runs, total = list_runs(db_path, limit=10)
    assert total == 2
    assert [run["run_id"] for run in runs] == [second, first]
    assert runs[0]["anomaly_count"] == 15
    assert get_run(db_path, first)["config_hash"] == demo_config.config_hash
    assert get_run(db_path, 99999) is None


def test_ledger_anomaly_filter(demo_config, demo_report, tmp_path: Path):
    db_path = tmp_path / "ledger.db"
    run_id = _record(demo_config, demo_report, db_path)
    drift, total = list_anomalies(db_path, run_id, rule_id="DRIFT_CUMULATIVE")
    assert total == 6
    assert all(item["rule_id"] == "DRIFT_CUMULATIVE" for item in drift)
    all_items, all_total = list_anomalies(db_path, run_id, limit=100)
    assert all_total == 15
    assert len(all_items) == 15
    assert {"index", "expected_ms", "actual_ms", "delta_ms"} <= set(all_items[0])
    none, none_total = list_anomalies(db_path, run_id, rule_id="NOT_A_RULE")
    assert none == [] and none_total == 0


def test_missing_ledger_is_empty(tmp_path: Path):
    assert list_runs(tmp_path / "absent.db") == ([], 0)
    assert list_anomalies(tmp_path / "absent.db", 1) == ([], 0)
    assert get_run(tmp_path / "absent.db", 1) is None
