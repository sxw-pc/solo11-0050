from __future__ import annotations

import pytest

from sampleripple.cli import main

from conftest import BASELINE, DEMO_CONFIG


def test_cli_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "verify" in capsys.readouterr().out


def test_cli_verify_smoke(temp_demo_config, tmp_path):
    code = main(
        [
            "verify",
            "--config",
            str(temp_demo_config.config_path),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0
    assert (tmp_path / "out" / "report.json").is_file()
    assert (tmp_path / "out" / "report.md").is_file()
    assert (tmp_path / "out" / "run_meta.json").is_file()


def test_cli_runs_lists_history(temp_demo_config, tmp_path, capsys):
    assert main(
        ["verify", "--config", str(temp_demo_config.config_path),
         "--out", str(tmp_path / "out")]
    ) == 0
    capsys.readouterr()
    assert main(["runs", "--config", str(temp_demo_config.config_path)]) == 0
    output = capsys.readouterr().out
    assert "run_id" in output
    assert "total=1" in output


def test_cli_golden_passes_and_writes_diff(tmp_path):
    diff_path = tmp_path / "golden.md"
    code = main(
        [
            "golden",
            "--config",
            str(DEMO_CONFIG),
            "--baseline",
            str(BASELINE),
            "--diff-out",
            str(diff_path),
        ]
    )
    assert code == 0
    assert "PASS" in diff_path.read_text(encoding="utf-8")


def test_cli_golden_returns_nonzero_on_tamper(tmp_path):
    import json

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline["summary"]["anomaly_count"] += 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(baseline), encoding="utf-8", newline="\n")
    code = main(
        [
            "golden",
            "--config",
            str(DEMO_CONFIG),
            "--baseline",
            str(tampered),
            "--diff-out",
            str(tmp_path / "diff.md"),
        ]
    )
    assert code == 1
    assert "FAIL" in (tmp_path / "diff.md").read_text(encoding="utf-8")
