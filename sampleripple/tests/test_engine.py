from __future__ import annotations

import json
from pathlib import Path

from sampleripple.anomalies import CATEGORIES
from sampleripple.cli import main
from sampleripple.ledger import list_runs
from sampleripple.loaders import discover_files
from sampleripple.util.hashing import sha256_file


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_verify_covers_all_categories(temp_demo_config, tmp_path: Path, capsys):
    out = tmp_path / "out"
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(out)]) == 0
    assert "verify OK" in capsys.readouterr().out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["anomaly_count"] == 15
    assert all(report["summary"]["by_category"][name] > 0 for name in CATEGORIES)
    clean = next(
        item for item in report["streams"] if item["stream_id"] == "clean-grid"
    )
    assert clean["summary"]["anomaly_count"] == 0
    assert all(group["status"] == "ok" for group in clean["groups"])


def test_report_is_byte_identical_across_runs(temp_demo_config, tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(first)]) == 0
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(second)]) == 0
    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()
    assert (first / "report.md").read_bytes() == (second / "report.md").read_bytes()


def test_report_contains_no_runtime_clock(temp_demo_config, tmp_path: Path):
    out = tmp_path / "out"
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert "started_at" not in set(_keys(report))
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["started_at"].endswith("Z")
    assert report["config_hash"] == meta["config_hash"]


def test_verify_appends_ledger_runs(temp_demo_config, tmp_path: Path):
    out = tmp_path / "out"
    for _ in range(2):
        assert main(["verify", "--config", str(temp_demo_config.config_path),
                     "--out", str(out)]) == 0
    runs, total = list_runs(temp_demo_config.ledger.db_path, limit=10)
    assert total == 2
    assert len(runs) == 2
    assert runs[0]["run_id"] > runs[1]["run_id"]


def test_verify_does_not_mutate_sources(temp_demo_config, tmp_path: Path):
    files = []
    for contract in temp_demo_config.streams:
        files.extend(discover_files(contract))
    before = {path: sha256_file(path) for path in files}
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(tmp_path / "out")]) == 0
    after = {path: sha256_file(path) for path in files}
    assert before == after


def test_run_meta_records_input_digests(temp_demo_config, tmp_path: Path):
    out = tmp_path / "out"
    assert main(["verify", "--config", str(temp_demo_config.config_path),
                 "--out", str(out)]) == 0
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert len(meta["input_files"]) == 6
    assert all(item["sha256"].startswith("sha256:") for item in meta["input_files"])
    assert meta["report_sha256"].startswith("sha256:")
