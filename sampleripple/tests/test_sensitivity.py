from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sampleripple.anomalies import RULE_CATEGORY, _period_drift
from sampleripple.certificate import has_flip
from sampleripple.cli import main
from sampleripple.config import load_config
from sampleripple.engine import run_verification
from sampleripple.ledger import list_runs, list_sensitivity_flips
from sampleripple.report_json import build_report
from sampleripple.report_markdown import render_markdown
from sampleripple.util.jsonutil import dump_bytes

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NO_FLIP_JSON = PROJECT_ROOT / "fixtures" / "golden" / "no_flip_report.json"
NO_FLIP_MD = PROJECT_ROOT / "fixtures" / "golden" / "no_flip_report.md"
ANCHOR = 1_000_000_000_000
PERIOD = 60000
_BASE_STREAM = {
    "stream_id": "sens-stream", "format": "csv",
    "time_field": "ts", "time_format": "epoch_ms", "sequence_field": "seq",
    "group_by": ["site", "line", "sensor"], "nominal_period_ms": PERIOD,
    "jitter_tolerance_ms": 500, "gap_tolerance_ms": 1000,
    "missing_policy": "allow_k", "missing_allow_k": 1, "disorder_window": 2,
    "duplicate_policy": "report", "drift_tolerance_ms": 500,
    "period_drift_tolerance_ms": 200, "edge_tolerance_ms": 500,
}


def _write_csv(path: Path, rows: list[tuple[int, int]]) -> None:
    lines = ["site,line,sensor,seq,ts,value"]
    lines += [f"site-a,line-07,sensor-x,{seq},{ts},1.0" for seq, ts in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_config(path: Path, source: Path, overrides: dict) -> Path:
    stream = {**_BASE_STREAM, "source": str(source), **overrides}
    config = {
        "version": 1, "base_dir": str(PROJECT_ROOT),
        "ledger": {"db_path": str(path.parent / "ledger.db")},
        "server": {"host": "127.0.0.1", "port": 8765},
        "streams": [stream],
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, rows: list[tuple[int, int]], overrides: dict | None = None):
    source = tmp_path / "samples" / "data.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(source, rows)
    config_path = _write_config(tmp_path / "config.yaml", source, overrides or {})
    config = load_config(config_path)
    return config, run_verification(config)


def _rule_row(verification, rule_id: str):
    certificate = verification.sensitivity[0][0]
    return next(row for row in certificate.rules if row.rule_id == rule_id)


def test_period_drift_float_slope_flips_on_adjacent_integer(tmp_path: Path):
    # slope fit is 60200.5ms -> distance from nominal is exactly 200.5ms, a
    # non-integer float sitting between the integer tolerances 200 and 201.
    rows = [(0, ANCHOR), (1, ANCHOR + 60100), (2, ANCHOR + 120401)]
    config, verification = _load(tmp_path, rows)
    timeline = verification.streams[0].timelines[0]
    assert timeline.period_fit_ms == pytest.approx(60200.5)
    assert abs(timeline.period_fit_ms - PERIOD) == pytest.approx(200.5)

    rule = _rule_row(verification, "PERIOD_DRIFT_FIT")
    assert (rule.triggered, rule.flippable, rule.parameter) == (
        True, True, "period_drift_tolerance_ms",
    )
    assert rule.current_value == 200 and rule.decrease is None
    assert rule.increase is not None
    # The critical integer is verified, never derived by rounding 200.5:
    # 200 keeps the anomaly while the adjacent integer 201 clears it.
    assert (rule.increase.delta, rule.increase.adjusted_value) == (1, 201)
    assert rule.increase.triggered_now is True
    assert rule.increase.triggered_after is False
    assert rule.stable_delta == 0

    # Guard against a rounded-threshold implementation: tolerances on both
    # sides of the float distance must reproduce the certificate boundary.
    at_200 = replace(config.streams[0], period_drift_tolerance_ms=200)
    at_201 = replace(config.streams[0], period_drift_tolerance_ms=201)
    assert len(_period_drift(at_200, timeline)) == 1
    assert _period_drift(at_201, timeline) == []
    assert isinstance(rule.increase.adjusted_value, int)


def test_missing_allow_k_group_silence_coupling(tmp_path: Path):
    # One absent slot; allow_k=1 keeps the whole group silent, allow_k=0
    # reports the missing slot at once.  Wide tolerances isolate MISSING.
    rows = [
        (0, ANCHOR + 100), (1, ANCHOR + PERIOD + 100),
        (3, ANCHOR + 3 * PERIOD + 100), (4, ANCHOR + 4 * PERIOD + 100),
    ]
    quiet = {
        "gap_tolerance_ms": 100000, "edge_tolerance_ms": 20000,
        "drift_tolerance_ms": 5000, "period_drift_tolerance_ms": 5000,
    }
    _, verification = _load(tmp_path, rows, quiet)
    timeline = verification.streams[0].timelines[0]
    assert timeline.missing_slots == (2,)
    assert verification.streams[0].anomalies == ()

    rule = _rule_row(verification, "MISSING_SLOT")
    assert (rule.triggered, rule.flippable, rule.parameter) == (
        False, True, "missing_allow_k",
    )
    assert rule.current_value == 1 and rule.increase is None
    assert rule.decrease is not None
    # One integer step (k 1 -> 0) un-silences the whole group.
    assert (rule.decrease.delta, rule.decrease.adjusted_value) == (1, 0)
    assert rule.decrease.triggered_after is True
    assert rule.stable_delta == 0

    # Raising k back to the missing count silences an already-reporting group.
    _, verification2 = _load(tmp_path, rows, {**quiet, "missing_allow_k": 0})
    fired = [
        a for a in verification2.streams[0].anomalies
        if a.rule_id == "MISSING_SLOT"
    ]
    assert len(fired) == 1
    rule2 = _rule_row(verification2, "MISSING_SLOT")
    assert rule2.triggered is True and rule2.increase is not None
    assert (rule2.increase.delta, rule2.increase.adjusted_value) == (1, 1)
    assert rule2.increase.triggered_after is False

    # Under strict/ignore policies the tolerance cannot move the conclusion.
    for policy in ("strict", "ignore"):
        _, verification3 = _load(tmp_path, rows, {**quiet, "missing_policy": policy})
        rule3 = _rule_row(verification3, "MISSING_SLOT")
        assert rule3.flippable is False
        assert rule3.reason == "governed_by_policy_not_tolerance"


def test_no_flip_report_bytes_match_pre_change_golden():
    # The no_flip_report.* fixtures were generated before any sensitivity
    # field existed; a run with no flippable rule must reproduce both exactly.
    config = load_config(PROJECT_ROOT / "configs" / "sensitivity_no_flip.yaml")
    report = build_report(run_verification(config))
    assert report["summary"]["anomaly_count"] == 0
    assert "sensitivity_certificate" not in report
    assert dump_bytes(report) == NO_FLIP_JSON.read_bytes()
    assert render_markdown(report).encode("utf-8") == NO_FLIP_MD.read_bytes()


def test_certificate_is_deterministic(tmp_path: Path):
    rows = [(0, ANCHOR), (1, ANCHOR + 60100), (2, ANCHOR + 120401)]
    config, verification = _load(tmp_path, rows)
    first = dump_bytes(build_report(verification))
    second = dump_bytes(build_report(run_verification(config)))
    assert first == second
    # Re-running the solver flips the same boundaries every time.
    assert run_verification(config).sensitivity == verification.sensitivity


def test_ledger_records_same_flip_information(tmp_path: Path):
    rows = [(0, ANCHOR), (1, ANCHOR + 60100), (2, ANCHOR + 120401)]
    config, verification = _load(tmp_path, rows)
    out = tmp_path / "out"
    assert main(["verify", "--config", str(config.config_path),
                 "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    cert_row = next(
        rule
        for group in report["sensitivity_certificate"]["groups"]
        for rule in group["rules"]
        if rule["rule_id"] == "PERIOD_DRIFT_FIT" and rule["flippable"]
    )
    runs, total = list_runs(config.ledger.db_path)
    assert total == 1
    flips, flip_total = list_sensitivity_flips(
        config.ledger.db_path, runs[0]["run_id"], rule_id="PERIOD_DRIFT_FIT"
    )
    assert flip_total == 1
    row = flips[0]
    assert row["rule_id"] == "PERIOD_DRIFT_FIT"
    assert row["category"] == RULE_CATEGORY["PERIOD_DRIFT_FIT"]
    assert row["parameter"] == cert_row["parameter"]
    assert row["current_value"] == cert_row["current_value"]
    assert row["stable_delta"] == cert_row["stable_delta"]
    assert row["direction"] == cert_row["increase"]["direction"]
    assert row["flip_delta"] == cert_row["increase"]["delta"]
    assert row["adjusted_value"] == cert_row["increase"]["adjusted_value"]
    assert row["triggered_after"] == int(cert_row["increase"]["triggered_after"])

    # A no-flip run leaves the sensitivity table empty.  The anchor is aligned
    # to a 60s epoch boundary so EDGE/GAP/DRIFT are all structurally stable.
    aligned_anchor = 1_000_000_080_000
    clean_rows = [(i, aligned_anchor + i * PERIOD) for i in range(6)]
    config2, verification2 = _load(tmp_path / "clean", clean_rows)
    assert not has_flip(verification2.sensitivity)
    assert "sensitivity_certificate" not in build_report(verification2)
    flips2, flips2_total = list_sensitivity_flips(config2.ledger.db_path, 1)
    assert flips2_total == 0 and flips2 == []
