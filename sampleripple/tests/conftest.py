from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from sampleripple.config import AppConfig, load_config
from sampleripple.engine import run_verification
from sampleripple.report_json import build_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "fixtures" / "samples"
CLEAN_FILE = FIXTURES / "clean" / "2024-06-01.csv"
DEMO_CONFIG = PROJECT_ROOT / "configs" / "demo.yaml"
BASELINE = PROJECT_ROOT / "fixtures" / "golden" / "baseline.json"


def default_stream(source: str | Path, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "stream_id": "test-stream",
        "source": str(source),
        "format": "auto",
        "time_field": "ts",
        "time_format": "epoch_ms",
        "sequence_field": "seq",
        "group_by": ["site", "line", "sensor"],
        "nominal_period_ms": 60000,
        "jitter_tolerance_ms": 500,
        "gap_tolerance_ms": 1000,
        "missing_policy": "strict",
        "disorder_window": 2,
        "duplicate_policy": "report",
        "drift_tolerance_ms": 500,
        "period_drift_tolerance_ms": 200,
        "edge_tolerance_ms": 500,
    }
    data.update(overrides)
    for key in [key for key, value in data.items() if value is None]:
        del data[key]
    return data


@pytest.fixture
def stream_factory():
    return default_stream


@pytest.fixture
def config_writer(tmp_path: Path):
    def write(
        streams: list[dict[str, Any]],
        *,
        base_dir: str | Path = PROJECT_ROOT,
        ledger: str | Path | None = None,
        server: dict[str, Any] | None = None,
        name: str = "config.yaml",
        extra: dict[str, Any] | None = None,
    ) -> Path:
        config: dict[str, Any] = {
            "version": 1,
            "base_dir": str(base_dir),
            "ledger": {"db_path": str(ledger or tmp_path / "ledger.db")},
            "server": server or {"host": "127.0.0.1", "port": 8765},
            "streams": streams,
        }
        if extra:
            config.update(extra)
        path = tmp_path / name
        path.write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
        )
        return path

    return write


@pytest.fixture
def demo_config() -> AppConfig:
    return load_config(DEMO_CONFIG)


@pytest.fixture
def temp_demo_config(tmp_path: Path) -> AppConfig:
    raw = yaml.safe_load(DEMO_CONFIG.read_text(encoding="utf-8"))
    raw["base_dir"] = str(PROJECT_ROOT)
    raw["ledger"] = {"db_path": str(tmp_path / "ledger.db")}
    path = tmp_path / "demo.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return load_config(path)


@pytest.fixture
def demo_report(demo_config: AppConfig) -> dict[str, Any]:
    return build_report(run_verification(demo_config))
