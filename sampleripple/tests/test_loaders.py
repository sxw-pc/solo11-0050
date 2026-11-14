from __future__ import annotations

import json
from pathlib import Path

import pytest

from sampleripple.config import load_config
from sampleripple.errors import AppError
from sampleripple.loaders import discover_files, load_stream
from sampleripple.util.hashing import sha256_file

from conftest import FIXTURES


def _contract(path: Path):
    return load_config(path).streams[0]


def _csv(path: Path, header: str, rows: list[str], encoding: str = "utf-8") -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding=encoding, newline="\n")
    return path


def test_csv_stream_from_directory(demo_config):
    sample = load_stream(demo_config.streams[0])
    assert len(sample.files) == 1
    assert len(sample.points) == 12
    assert sample.points[0].group_key == "site-a/line-07/sensor-x"
    assert sample.points[6].group_key == "site-b/line-08/sensor-y"
    assert sample.points[0].ts_ms == 1717200000000


def test_jsonl_stream(demo_config):
    stream = next(item for item in demo_config.streams if item.stream_id == "gap-run")
    sample = load_stream(stream)
    assert len(sample.points) == 5
    assert [point.ts_ms for point in sample.points] == [
        1717372800000,
        1717372860000,
        1717372920000,
        1717373040000,
        1717373100000,
    ]


def test_iso8601_and_utf8_sig(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "iso.csv"
    _csv(
        source,
        "site,line,sensor,seq,when,value",
        [
            "site-a,line-07,sensor-x,0,2024-06-01T00:00:00.000Z,1",
            "site-a,line-07,sensor-x,1,2024-06-01T00:01:00.000+00:00,2",
        ],
        encoding="utf-8-sig",
    )
    stream = stream_factory(str(source), time_field="when", time_format="iso8601")
    path = config_writer([stream])
    sample = load_stream(_contract(path))
    assert [point.ts_ms for point in sample.points] == [1717200000000, 1717200060000]


def test_nested_directory_input(tmp_path: Path, config_writer, stream_factory):
    root = tmp_path / "nested"
    (root / "deep").mkdir(parents=True)
    row = "site-a,line-07,sensor-x,0,1717200000000,1"
    _csv(root / "a.csv", "site,line,sensor,seq,ts,value", [row])
    _csv(root / "deep" / "b.csv", "site,line,sensor,seq,ts,value", [row])
    path = config_writer([stream_factory(root)])
    contract = _contract(path)
    files = discover_files(contract)
    assert len(files) == 2
    assert files[0].name == "a.csv"
    assert len(load_stream(contract).points) == 2


def test_missing_csv_column(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "bad.csv"
    _csv(source, "site,line,sensor,seq,value", ["site-a,line-07,sensor-x,0,1"])
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_MISSING_COLUMN"


def test_bad_jsonl_reports_line(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"site":"site-a"}\n{not-json}\n', encoding="utf-8", newline="\n")
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_MISSING_FIELD"
    assert caught.value.line == 1


def test_bad_json_syntax(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "bad.jsonl"
    source.write_text("{not-json}\n", encoding="utf-8", newline="\n")
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_BAD_JSON"
    assert caught.value.line == 1


def test_bad_timestamp(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "bad.csv"
    _csv(
        source,
        "site,line,sensor,seq,ts,value",
        ["site-a,line-07,sensor-x,0,not-a-time,1"],
    )
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_BAD_TIME"


def test_missing_json_field(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "bad.jsonl"
    record = {"site": "site-a", "line": "line-07", "seq": 0,
              "ts": 1717200000000}
    source.write_text(json.dumps(record) + "\n", encoding="utf-8", newline="\n")
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_MISSING_FIELD"


def test_empty_stream_is_rejected(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "empty.csv"
    _csv(source, "site,line,sensor,seq,ts,value", [])
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_EMPTY"


def test_load_does_not_modify_source(demo_config):
    files = discover_files(demo_config.streams[0])
    before = {path: sha256_file(path) for path in files}
    load_stream(demo_config.streams[0])
    after = {path: sha256_file(path) for path in files}
    assert before == after


def test_all_fixture_files_are_local():
    assert all(path.is_relative_to(FIXTURES) for path in FIXTURES.rglob("*") if path.is_file())


def test_empty_csv_header_is_rejected(tmp_path: Path, config_writer, stream_factory):
    source = tmp_path / "headerless.csv"
    source.write_text("", encoding="utf-8", newline="\n")
    path = config_writer([stream_factory(source)])
    with pytest.raises(AppError) as caught:
        load_stream(_contract(path))
    assert caught.value.code == "LOAD_BAD_HEADER"
