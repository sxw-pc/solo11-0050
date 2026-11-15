from __future__ import annotations

import sqlite3
from pathlib import Path

from sampleripple.anomalies import Anomaly
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


LEGACY_ANOMALIES_DDL = """
CREATE TABLE runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, config_hash TEXT NOT NULL, config_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL, version TEXT NOT NULL, stream_count INTEGER NOT NULL,
    group_count INTEGER NOT NULL, actual_points INTEGER NOT NULL, anomaly_count INTEGER NOT NULL
);
CREATE TABLE anomalies (
    anomaly_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, stream_id TEXT NOT NULL, group_key TEXT NOT NULL,
    point_index INTEGER NOT NULL, expected_ms INTEGER NOT NULL,
    actual_ms INTEGER NOT NULL, delta_ms INTEGER NOT NULL, rule_id TEXT NOT NULL,
    category TEXT NOT NULL, severity TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as connection:
        return {
            row[1]
            for row in connection.execute("PRAGMA table_info(anomalies)")
        }


def test_legacy_ledger_without_report_seq(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(str(db_path)) as connection:
        connection.executescript(LEGACY_ANOMALIES_DDL)
        connection.execute(
            "INSERT INTO runs VALUES (1, 't', 'h', 'p', 's', 'v', 1, 1, 2, 2)"
        )
        for index in (2, 1):  # legacy rows read back in insertion (pk) order
            connection.execute(
                "INSERT INTO anomalies (run_id, stream_id, group_key, point_index,"
                " expected_ms, actual_ms, delta_ms, rule_id, category, severity)"
                " VALUES (1, 's', 'g', ?, ?, ?, 100, 'GAP_ADJACENT', 'GAP', 'error')",
                (index, index * 100, index * 200),
            )
    items, total = list_anomalies(db_path, 1, group_key="g", category="GAP")
    assert total == 2
    assert [item["index"] for item in items] == [2, 1]
    assert "report_seq" not in _columns(db_path)
    anomaly = Anomaly(
        stream_id="s", group_key="g", index=3, expected_ms=500, actual_ms=600,
        delta_ms=100, rule_id="GAP_ADJACENT", category="GAP", severity="error",
        detail="crafted",
    )
    report = {
        "summary": {"stream_count": 1, "group_count": 1, "actual_points": 3},
        "tool": {"version": "0.1.0"},
    }
    run_id = record_run(
        db_path, started_at="t2", config_hash="h", config_path="p",
        report_sha256="s", report=report, anomalies=[anomaly],
    )
    assert "report_seq" in _columns(db_path)
    items, total = list_anomalies(db_path, run_id, group_key="g")
    assert total == 1 and items[0]["index"] == 3
    items, total = list_anomalies(db_path, 1)
    assert [item["index"] for item in items] == [2, 1]
