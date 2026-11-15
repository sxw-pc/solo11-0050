from __future__ import annotations

import json
from pathlib import Path

from sampleripple.cli import main
from sampleripple.config import load_config
from sampleripple.engine import run_verification
from sampleripple.ledger import list_anomalies, record_run
from sampleripple.report_json import build_report
from sampleripple.util.hashing import sha256_text

HEADER = "site,line,sensor,seq,ts,value"
BASE_TS = 1717200000000  # 2024-06-01T00:00:00.000Z, aligned to the 60s grid
PERIOD = 60000
# Tolerances that keep every non-duplicate rule silent for these fixtures.
QUIET = {
    "disorder_window": 0,
    "drift_tolerance_ms": 60000,
    "period_drift_tolerance_ms": 60000,
    "edge_tolerance_ms": 60000,
}
LEDGER_KEYS = {
    "stream_id", "group_key", "index", "expected_ms", "actual_ms",
    "delta_ms", "rule_id", "category", "severity",
}


def _write_csv(path: Path, samples: list[tuple[int, int]]) -> Path:
    lines = [HEADER]
    for seq, ts in samples:
        lines.append(f"site-a,line-07,sensor-x,{seq},{ts},1.0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _aligned_day() -> list[tuple[int, int]]:
    return [(seq, BASE_TS + seq * PERIOD) for seq in range(6)]


def _verify(source: Path, config_writer, stream_factory, tmp_path: Path):
    config_path = config_writer([stream_factory(source, **QUIET)])
    config = load_config(config_path)
    verification = run_verification(config)
    return config.streams[0], verification, build_report(verification)


def _duplicates(verification):
    return [a for a in verification.anomalies if a.rule_id == "DUPLICATE_POINT"]


def _dup_signature(verification) -> list[tuple]:
    signature = []
    for anomaly in _duplicates(verification):
        evidence = anomaly.duplicate
        signature.append(
            (
                anomaly.group_key,
                anomaly.expected_ms,
                anomaly.actual_ms,
                evidence.scope,
                tuple(
                    sorted(
                        {
                            Path(evidence.source_file).name,
                            Path(evidence.other_source_file).name,
                        }
                    )
                ),
            )
        )
    return sorted(signature)


def test_cross_file_duplicate_same_sequence_half_period(
    tmp_path, config_writer, stream_factory
):
    source = tmp_path / "samples"
    source.mkdir()
    _write_csv(source / "a.csv", _aligned_day())
    _write_csv(source / "b.csv", [(2, BASE_TS + 150000)])  # seq 2, half period late
    contract, verification, report = _verify(
        source, config_writer, stream_factory, tmp_path
    )
    timeline = verification.streams[0].timelines[0]
    # 同序号但时间不同的点不丢失：7 个点全部留在唯一时间轴上。
    assert len(timeline.points) == 7
    assert len(timeline.unique_points) == 7
    (dup,) = _duplicates(verification)
    assert dup.detail == "duplicate sequence"
    assert dup.expected_ms == BASE_TS + 120000
    assert dup.actual_ms == BASE_TS + 150000
    assert dup.delta_ms == 30000
    evidence = dup.duplicate
    assert evidence.scope == "cross_file"
    assert evidence.source_file == contract.display_path(source / "b.csv")
    assert evidence.line_no == 2
    assert evidence.other_source_file == contract.display_path(source / "a.csv")
    assert evidence.other_line_no == 4
    summary = report["streams"][0]["summary"]
    assert summary["duplicates_by_file"] == {
        contract.display_path(source / "b.csv"): 1
    }
    assert report["summary"]["duplicates_by_file"] == summary["duplicates_by_file"]


def test_cross_file_duplicate_same_timestamp(tmp_path, config_writer, stream_factory):
    source = tmp_path / "samples"
    source.mkdir()
    _write_csv(source / "a.csv", _aligned_day())
    _write_csv(source / "b.csv", [(3, BASE_TS + 180000)])  # exact copy of seq 3
    contract, verification, _ = _verify(source, config_writer, stream_factory, tmp_path)
    timeline = verification.streams[0].timelines[0]
    # 同时间戳的重复行仍只保留一个代表点。
    assert len(timeline.points) == 7
    assert len(timeline.unique_points) == 6
    (dup,) = _duplicates(verification)
    assert dup.detail == "duplicate timestamp+sequence"
    assert dup.expected_ms == dup.actual_ms == BASE_TS + 180000
    evidence = dup.duplicate
    assert evidence.scope == "cross_file"
    assert evidence.source_file == contract.display_path(source / "b.csv")
    assert evidence.line_no == 2
    assert evidence.other_source_file == contract.display_path(source / "a.csv")
    assert evidence.other_line_no == 5


def test_intra_file_duplicate_scope(tmp_path, config_writer, stream_factory):
    source = tmp_path / "samples"
    source.mkdir()
    _write_csv(source / "c.csv", _aligned_day() + [(2, BASE_TS + 120000)])
    contract, verification, report = _verify(
        source, config_writer, stream_factory, tmp_path
    )
    (dup,) = _duplicates(verification)
    evidence = dup.duplicate
    assert evidence.scope == "intra_file"
    assert evidence.source_file == contract.display_path(source / "c.csv")
    assert evidence.other_source_file == evidence.source_file
    assert evidence.other_line_no == 4
    assert evidence.line_no == 8
    summary = report["streams"][0]["summary"]
    assert summary["duplicates_by_file"] == {
        contract.display_path(source / "c.csv"): 1
    }


def _renamed(signature: list[tuple], old: str, new: str) -> list[tuple]:
    return sorted(
        entry[:4] + (tuple(sorted(new if name == old else name for name in entry[4])),)
        for entry in signature
    )


def test_cross_file_duplicates_survive_file_rename(
    tmp_path, config_writer, stream_factory
):
    source = tmp_path / "samples"
    source.mkdir()
    _write_csv(source / "a.csv", _aligned_day())
    _write_csv(
        source / "m.csv",
        [(2, BASE_TS + 120000), (4, BASE_TS + 270000)],
    )
    _, first, _ = _verify(source, config_writer, stream_factory, tmp_path)
    assert len(first.anomalies) == 2
    before = _dup_signature(first)
    (source / "a.csv").rename(source / "z.csv")
    _, second, _ = _verify(source, config_writer, stream_factory, tmp_path)
    assert len(second.anomalies) == 2
    assert _dup_signature(second) == _renamed(before, "a.csv", "z.csv")
    scopes = {entry[3] for entry in _dup_signature(second)}
    assert scopes == {"cross_file"}


def test_demo_duplicate_carries_intra_file_evidence(demo_report):
    stream = next(
        item for item in demo_report["streams"] if item["stream_id"] == "dup-run"
    )
    (dup,) = [
        item for item in stream["anomalies"] if item["rule_id"] == "DUPLICATE_POINT"
    ]
    assert dup["detail"] == "duplicate timestamp+sequence"
    assert dup["duplicate_scope"] == "intra_file"
    assert dup["source_file"] == "fixtures/samples/dup/2024-06-04.csv"
    assert dup["other_source_file"] == dup["source_file"]
    assert dup["line_no"] == 5
    assert dup["other_line_no"] == 4
    expected_counts = {"fixtures/samples/dup/2024-06-04.csv": 1}
    assert stream["summary"]["duplicates_by_file"] == expected_counts
    assert demo_report["summary"]["duplicates_by_file"] == expected_counts
    clean = next(
        item for item in demo_report["streams"] if item["stream_id"] == "clean-grid"
    )
    assert clean["summary"]["duplicates_by_file"] == {}
    assert demo_report["summary"]["anomaly_count"] == 15


def test_ledger_and_runs_ignore_evidence_fields(
    temp_demo_config, demo_report, tmp_path, capsys
):
    verification = run_verification(temp_demo_config)
    run_id = record_run(
        temp_demo_config.ledger.db_path,
        started_at="2026-01-01T00:00:00.000Z",
        config_hash=temp_demo_config.config_hash,
        config_path="configs/demo.yaml",
        report_sha256=sha256_text("report"),
        report=demo_report,
        anomalies=verification.anomalies,
    )
    rows, total = list_anomalies(
        temp_demo_config.ledger.db_path, run_id, rule_id="DUPLICATE_POINT"
    )
    assert total == 1
    assert set(rows[0]) == LEDGER_KEYS
    assert (
        main(["runs", "--config", str(temp_demo_config.config_path), "--json"]) == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["total"] == 1
    assert listed["runs"][0]["anomaly_count"] == 15
