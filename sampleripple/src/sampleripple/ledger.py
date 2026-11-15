"""Append-only SQLite ledger for verification runs and anomalies."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .anomalies import Anomaly

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    stream_count INTEGER NOT NULL,
    group_count INTEGER NOT NULL,
    actual_points INTEGER NOT NULL,
    anomaly_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stream_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    point_index INTEGER NOT NULL,
    expected_ms INTEGER NOT NULL,
    actual_ms INTEGER NOT NULL,
    delta_ms INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_anomalies_run_rule
    ON anomalies(run_id, rule_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_run
    ON anomalies(run_id);
CREATE TABLE IF NOT EXISTS sensitivity_flips (
    flip_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stream_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    parameter TEXT NOT NULL,
    current_value INTEGER NOT NULL,
    triggered INTEGER NOT NULL,
    stable_delta INTEGER NOT NULL,
    direction TEXT NOT NULL,
    flip_delta INTEGER NOT NULL,
    adjusted_value INTEGER NOT NULL,
    triggered_after INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_sensitivity_run_rule
    ON sensitivity_flips(run_id, rule_id);
"""


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _session(path: Path) -> Iterator[sqlite3.Connection]:
    connection = _connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_schema(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _session(path) as connection:
        connection.executescript(SCHEMA)
    return path


def record_run(
    db_path: str | Path,
    *,
    started_at: str,
    config_hash: str,
    config_path: str,
    report_sha256: str,
    report: dict[str, Any],
    anomalies: Iterable[Anomaly],
    sensitivity: Iterable[Any] = (),
) -> int:
    """Append one run, its anomalies and sensitivity flips in one transaction."""

    path = ensure_schema(db_path)
    anomaly_list = list(anomalies)
    summary = report["summary"]
    flip_rows = list(_flip_records(sensitivity))
    with _session(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO runs (
                started_at, config_hash, config_path, report_sha256, version,
                stream_count, group_count, actual_points, anomaly_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at, config_hash, config_path, report_sha256,
                report["tool"]["version"], summary["stream_count"],
                summary["group_count"], summary["actual_points"],
                len(anomaly_list),
            ),
        )
        run_id = int(cursor.lastrowid)
        connection.executemany(
            """
            INSERT INTO anomalies (
                run_id, stream_id, group_key, point_index, expected_ms,
                actual_ms, delta_ms, rule_id, category, severity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id, anomaly.stream_id, anomaly.group_key, anomaly.index,
                    anomaly.expected_ms, anomaly.actual_ms, anomaly.delta_ms,
                    anomaly.rule_id, anomaly.category, anomaly.severity,
                )
                for anomaly in anomaly_list
            ],
        )
        if flip_rows:
            connection.executemany(
                """
                INSERT INTO sensitivity_flips (
                    run_id, stream_id, group_key, rule_id, category, parameter,
                    current_value, triggered, stable_delta, direction, flip_delta,
                    adjusted_value, triggered_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id, row["stream_id"], row["group_key"], row["rule_id"],
                        row["category"], row["parameter"], row["current_value"],
                        row["triggered"], row["stable_delta"],
                        row["boundary"].direction, row["boundary"].delta,
                        row["boundary"].adjusted_value,
                        row["boundary"].triggered_after,
                    )
                    for row in flip_rows
                ],
            )
    return run_id


def _flip_records(certificates: Iterable[Any]) -> Iterator[dict[str, Any]]:
    # ``verification.sensitivity`` groups GroupSensitivity per stream, so the
    # outer structure is a tuple of tuples.
    groups = [
        group
        for item in certificates
        for group in (item if isinstance(item, tuple) else (item,))
    ]
    for group_certificate in groups:
        for rule in group_certificate.rules:
            if not rule.flippable:
                continue
            for boundary in (rule.decrease, rule.increase):
                if boundary is None:
                    continue
                yield {
                    "stream_id": group_certificate.stream_id,
                    "group_key": group_certificate.group_key,
                    "rule_id": rule.rule_id,
                    "category": rule.category,
                    "parameter": rule.parameter,
                    "current_value": rule.current_value,
                    "triggered": int(rule.triggered),
                    "stable_delta": rule.stable_delta,
                    "boundary": boundary,
                }


def _filtered_rows(
    path: Path, table: str, run_id: int, rule_id: str | None,
    limit: int, offset: int, columns: str, order_column: str
) -> tuple[list[dict[str, Any]], int]:
    sql_filter = "WHERE run_id = ?"
    args: list[Any] = [run_id]
    if rule_id is not None:
        sql_filter += " AND rule_id = ?"
        args.append(rule_id)
    with _session(path) as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} {sql_filter}", args
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"SELECT {columns} FROM {table} {sql_filter} "
            f"ORDER BY {order_column} ASC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
    return [dict(row) for row in rows], total


def list_sensitivity_flips(
    db_path: str | Path,
    run_id: int,
    *,
    rule_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    path = Path(db_path)
    if not path.exists():
        return [], 0
    return _filtered_rows(
        path, "sensitivity_flips", run_id, rule_id, limit, offset,
        "stream_id, group_key, rule_id, category, parameter, current_value, "
        "triggered, stable_delta, direction, flip_delta, adjusted_value, "
        "triggered_after",
        "flip_pk",
    )


def list_runs(
    db_path: str | Path, *, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    path = Path(db_path)
    if not path.exists():
        return [], 0
    with _session(path) as connection:
        total = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        rows = connection.execute(
            """
            SELECT run_id, started_at, config_hash, config_path, report_sha256,
                   version, stream_count, group_count, actual_points, anomaly_count
            FROM runs ORDER BY run_id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows], total


def get_run(db_path: str | Path, run_id: int) -> dict[str, Any] | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with _session(path) as connection:
        row = connection.execute(
            """
            SELECT run_id, started_at, config_hash, config_path, report_sha256,
                   version, stream_count, group_count, actual_points, anomaly_count
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def list_anomalies(
    db_path: str | Path,
    run_id: int,
    *,
    rule_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    path = Path(db_path)
    if not path.exists():
        return [], 0
    return _filtered_rows(
        path, "anomalies", run_id, rule_id, limit, offset,
        'stream_id, group_key, point_index AS "index", expected_ms, actual_ms, '
        "delta_ms, rule_id, category, severity",
        "anomaly_pk",
    )
