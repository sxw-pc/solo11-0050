from __future__ import annotations

from pathlib import Path

import pytest

from sampleripple.config import load_config
from sampleripple.errors import AppError

from conftest import CLEAN_FILE, DEMO_CONFIG, PROJECT_ROOT


def test_load_demo_config(demo_config):
    assert len(demo_config.streams) == 6
    assert demo_config.config_hash.startswith("sha256:")
    assert demo_config.streams[0].group_by == ("site", "line", "sensor")
    assert demo_config.streams[0].input_format == "csv"
    assert demo_config.ledger.db_path.is_absolute()
    assert demo_config.server.host == "127.0.0.1"
    assert demo_config.server.port == 8765


def test_missing_field_has_code_and_line(config_writer, stream_factory):
    stream = stream_factory(CLEAN_FILE)
    stream.pop("nominal_period_ms")
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_MISSING_FIELD"
    assert caught.value.line is not None


def test_bad_period_is_rejected(config_writer, stream_factory):
    stream = stream_factory(CLEAN_FILE, nominal_period_ms=0)
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_PERIOD"


def test_bad_missing_policy_is_rejected(config_writer, stream_factory):
    stream = stream_factory(CLEAN_FILE, missing_policy="sometimes")
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_ENUM"


def test_unknown_stream_field_is_rejected(config_writer, stream_factory):
    stream = stream_factory(CLEAN_FILE, surprise=1)
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_UNKNOWN_FIELD"


def test_non_loopback_server_is_rejected(config_writer, stream_factory):
    path = config_writer(
        [stream_factory(CLEAN_FILE)],
        server={"host": "0.0.0.0", "port": 8765},
    )
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_HOST"


def test_bad_port_is_rejected(config_writer, stream_factory):
    path = config_writer(
        [stream_factory(CLEAN_FILE)],
        server={"host": "127.0.0.1", "port": 70000},
    )
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_PORT"


def test_missing_source_is_rejected(config_writer, stream_factory):
    path = config_writer([stream_factory("does-not-exist.csv")])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_SOURCE_NOT_FOUND"


def test_duplicate_stream_id_is_rejected(config_writer, stream_factory):
    one = stream_factory(CLEAN_FILE)
    two = stream_factory(PROJECT_ROOT / "fixtures")
    path = config_writer([one, two])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_DUPLICATE_STREAM"


def test_yaml_syntax_error_reports_line(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    path.write_text("version: 1\nstreams: [\n", encoding="utf-8", newline="\n")
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_YAML_SYNTAX"
    assert caught.value.line is not None


def test_missing_config_file(tmp_path: Path):
    with pytest.raises(AppError) as caught:
        load_config(tmp_path / "absent.yaml")
    assert caught.value.code == "CFG_FILE_NOT_FOUND"


def test_demo_and_strict_profiles_load():
    assert len(load_config(DEMO_CONFIG).streams) == 6
    assert len(load_config(PROJECT_ROOT / "configs" / "strict.yaml").streams) == 6


def test_epoch_align_ms_defaults_to_none(config_writer, stream_factory):
    path = config_writer([stream_factory(CLEAN_FILE)])
    config = load_config(path)
    assert config.streams[0].epoch_align_ms is None


def test_epoch_align_ms_accepts_integer_and_null(config_writer, stream_factory):
    path = config_writer([stream_factory(CLEAN_FILE, epoch_align_ms=1717200000000)])
    assert load_config(path).streams[0].epoch_align_ms == 1717200000000
    stream = stream_factory(CLEAN_FILE)
    stream["epoch_align_ms"] = None
    path = config_writer([stream])
    assert load_config(path).streams[0].epoch_align_ms is None


@pytest.mark.parametrize("value", [True, 1.5, "1717200000000", [1717200000000]])
def test_epoch_align_ms_rejects_non_integer(config_writer, stream_factory, value):
    path = config_writer([stream_factory(CLEAN_FILE, epoch_align_ms=value)])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_TYPE"
