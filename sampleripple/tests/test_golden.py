from __future__ import annotations

import json
from pathlib import Path

import pytest

from sampleripple.errors import AppError
from sampleripple.golden import DEFAULT_TOLERANCE_MS, compare_values, run_golden

from conftest import BASELINE


def test_golden_baseline_passes(demo_config, tmp_path: Path):
    diff_path = tmp_path / "diff.md"
    result = run_golden(demo_config, BASELINE, diff_path)
    assert result.passed
    assert result.differences == ()
    body = diff_path.read_text(encoding="utf-8")
    assert "PASS" in body
    assert "No structural differences" in body


def test_tampered_baseline_fails(demo_config, tmp_path: Path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline["summary"]["anomaly_count"] += 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(baseline), encoding="utf-8", newline="\n")
    result = run_golden(demo_config, tampered, tmp_path / "diff.md")
    assert not result.passed
    assert any(item.path.endswith("summary.anomaly_count") for item in result.differences)
    assert "FAIL" in (tmp_path / "diff.md").read_text(encoding="utf-8")


def test_fixed_float_tolerance():
    assert compare_values({"v": 1.0005}, {"v": 1.0010}) == []
    assert compare_values({"v": 1.0}, {"v": 1.0 + DEFAULT_TOLERANCE_MS * 2})
    assert compare_values({"i": 1}, {"i": 1.0}) == []


def test_golden_missing_baseline(demo_config, tmp_path: Path):
    with pytest.raises(AppError) as caught:
        run_golden(demo_config, tmp_path / "absent.json", tmp_path / "diff.md")
    assert caught.value.code == "GOLDEN_MISSING"


def test_manifest_records_provenance(demo_config):
    manifest = json.loads(
        (BASELINE.parent / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["config_hash"] == demo_config.config_hash
    assert manifest["baseline_report"] == "baseline.json"
    assert manifest["tool"]["version"]
