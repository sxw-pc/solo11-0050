from __future__ import annotations

import pytest

from sampleripple.config import load_config
from sampleripple.errors import AppError

from conftest import CLEAN_FILE, PROJECT_ROOT


def _minimal(source: str = str(CLEAN_FILE)) -> dict:
    return {
        "stream_id": "minimal",
        "source": source,
        "time_field": "ts",
        "time_format": "epoch_ms",
        "group_by": ["site"],
        "nominal_period_ms": 60000,
        "jitter_tolerance_ms": 250,
        "gap_tolerance_ms": 1000,
        "missing_policy": "allow_k",
        "disorder_window": 1,
        "duplicate_policy": "ignore",
    }


def test_minimal_contract_defaults(config_writer):
    path = config_writer([_minimal()])
    contract = load_config(path).streams[0]
    assert contract.input_format == "auto"
    assert contract.sequence_field is None
    assert contract.missing_allow_k == 0
    assert contract.drift_tolerance_ms == 250
    assert contract.period_drift_tolerance_ms == 250
    assert contract.edge_tolerance_ms == 250
    assert contract.display_source == CLEAN_FILE.relative_to(PROJECT_ROOT).as_posix()


def test_contract_rejects_bad_time_format(config_writer):
    stream = _minimal()
    stream["time_format"] = "unix_nanos"
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_TIME_FORMAT"


def test_contract_rejects_time_field_in_group_by(config_writer):
    stream = _minimal()
    stream["group_by"] = ["site", "ts"]
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_GROUP_BY"


def test_contract_rejects_duplicate_group_by(config_writer):
    stream = _minimal()
    stream["group_by"] = ["site", "site"]
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_GROUP_BY"


def test_contract_rejects_negative_tolerance(config_writer):
    stream = _minimal()
    stream["gap_tolerance_ms"] = -1
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_TOLERANCE"


def test_contract_rejects_bad_missing_allow_k(config_writer):
    stream = _minimal()
    stream["missing_allow_k"] = -2
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_TYPE"


def test_contract_rejects_bad_input_format(config_writer):
    stream = _minimal()
    stream["format"] = "parquet"
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_ENUM"
