"""Semantics for the two coverage modes in timeline reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sampleripple.config import load_config
from sampleripple.engine import run_verification
from sampleripple.errors import AppError
from sampleripple.loaders import Point
from sampleripple.report_json import build_report
from sampleripple.timeline import build_timeline

PERIOD = 60000
WINDOW_START = 1_000_000
WINDOW_END = WINDOW_START + 10 * PERIOD


def _point(ts_ms: int, ordinal: int) -> Point:
    return Point(
        group=("g",),
        group_key="g",
        ts_ms=ts_ms,
        sequence=None,
        ordinal=ordinal,
        source_file="synthetic",
        line_no=ordinal + 1,
    )


def _timeline(timestamps, **kwargs):
    return build_timeline(
        ("g",),
        [_point(ts, index) for index, ts in enumerate(timestamps)],
        PERIOD,
        **kwargs,
    )


DECLARED = dict(
    coverage_mode="declared_window",
    coverage_window_start_ms=WINDOW_START,
    coverage_window_end_ms=WINDOW_END,
)


def test_observed_span_defaults_to_measured_span():
    timeline = _timeline([WINDOW_START, WINDOW_START + PERIOD])
    assert timeline.coverage_mode == "observed_span"
    assert timeline.anchor_ms == WINDOW_START
    assert timeline.expected_points == 2
    assert timeline.missing_slots == ()
    assert timeline.coverage_window_start_ms is None
    assert timeline.coverage_window_end_ms is None


def test_declared_window_rebuilds_slots_across_full_window():
    # Only two samples inside an 11-slot declared window.
    timeline = _timeline(
        [WINDOW_START, WINDOW_START + PERIOD],
        **DECLARED,
    )
    assert timeline.anchor_ms == WINDOW_START
    assert timeline.expected_points == 11
    assert timeline.slots == (0, 1)
    assert timeline.missing_slots == tuple(range(2, 11))


def test_declared_window_counts_gaps_inside_sparse_observed_span():
    # Observed span alone would hide the missing middle slot.
    timeline = _timeline(
        [WINDOW_START, WINDOW_START + 3 * PERIOD],
        **DECLARED,
    )
    assert timeline.missing_slots == (1, 2, 4, 5, 6, 7, 8, 9, 10)
    assert timeline.expected_points == 11
    assert set(timeline.slots).isdisjoint(timeline.missing_slots)


def test_occupied_slots_are_never_also_missing():
    timeline = _timeline(
        [WINDOW_START + 2 * PERIOD, WINDOW_START + 5 * PERIOD],
        **DECLARED,
    )
    assert set(timeline.slots) == {2, 5}
    assert 2 not in timeline.missing_slots
    assert 5 not in timeline.missing_slots
    assert len(timeline.missing_slots) + len(set(timeline.slots)) == 11
    # Every slot in the interval is either present or missing exactly once.
    assert sorted(set(timeline.slots) | set(timeline.missing_slots)) == list(range(11))


def test_declared_window_accepts_jittered_points_inside_interval():
    timeline = _timeline(
        [WINDOW_START + 100, WINDOW_START + PERIOD - 100, WINDOW_END],
        **DECLARED,
    )
    assert timeline.slots == (0, 1, 10)
    assert all(0 <= slot <= 10 for slot in timeline.slots)
    assert timeline.missing_slots == tuple(range(2, 10))


def test_declared_window_requires_intersection_with_observed_span():
    # All observations lie strictly before the declared window.
    with pytest.raises(AppError) as caught:
        _timeline([WINDOW_START - 3 * PERIOD, WINDOW_START - PERIOD], **DECLARED)
    assert caught.value.code == "COVERAGE_WINDOW_MISMATCH"
    assert "no intersection" in caught.value.message
    # All observations lie strictly after it.
    with pytest.raises(AppError) as caught:
        _timeline([WINDOW_END + PERIOD, WINDOW_END + 2 * PERIOD], **DECLARED)
    assert caught.value.code == "COVERAGE_WINDOW_MISMATCH"
    assert "no intersection" in caught.value.message


def test_negative_slot_is_rejected_without_dropping_point():
    # A point rounds to slot -1 relative to the declared window start.
    with pytest.raises(AppError) as caught:
        _timeline([WINDOW_START - PERIOD, WINDOW_START], **DECLARED)
    assert caught.value.code == "COVERAGE_WINDOW_MISMATCH"
    assert "negative slot" in caught.value.message
    assert "-1" in caught.value.message


def test_slot_beyond_upper_bound_is_rejected_not_silently_counted():
    with pytest.raises(AppError) as caught:
        _timeline([WINDOW_START, WINDOW_END + PERIOD], **DECLARED)
    assert caught.value.code == "COVERAGE_WINDOW_MISMATCH"
    assert "beyond declared window upper slot" in caught.value.message


def test_alignment_and_period_fit_keep_their_definitions():
    sparse = _timeline(
        [WINDOW_START, WINDOW_START + 3 * PERIOD],
        **DECLARED,
    )
    observed = build_timeline(
        ("g",),
        [_point(WINDOW_START, 0), _point(WINDOW_START + 3 * PERIOD, 1)],
        PERIOD,
    )
    assert sparse.alignment_offset_ms == observed.alignment_offset_ms
    assert sparse.period_fit_ms == observed.period_fit_ms
    assert sparse.expected_ms(3) == WINDOW_START + 3 * PERIOD


def test_contract_defaults_cover_observed_span_only(config_writer, stream_factory):
    from conftest import CLEAN_FILE

    path = config_writer([stream_factory(CLEAN_FILE)])
    contract = load_config(path).streams[0]
    assert contract.coverage_mode == "observed_span"
    assert contract.coverage_window_start_ms is None
    assert contract.coverage_window_end_ms is None


def test_contract_rejects_window_fields_without_declared_mode(
    config_writer, stream_factory, tmp_path
):
    source = tmp_path / "s.csv"
    source.write_text(
        "site,ts\n"
        f"g,{WINDOW_START}\n"
        f"g,{WINDOW_START + PERIOD}\n",
        encoding="utf-8",
        newline="\n",
    )
    stream = stream_factory(
        source,
        group_by=["site"],
        sequence_field=None,
        coverage_window_start_ms=WINDOW_START,
    )
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        load_config(path)
    assert caught.value.code == "CFG_BAD_TYPE"


def test_contract_rejects_bad_enum_and_missing_endpoints(
    config_writer, stream_factory, tmp_path
):
    source = tmp_path / "s.csv"
    source.write_text(
        "site,ts\n"
        f"g,{WINDOW_START}\n"
        f"g,{WINDOW_START + PERIOD}\n",
        encoding="utf-8",
        newline="\n",
    )
    bad_enum = stream_factory(
        source, group_by=["site"], sequence_field=None, coverage_mode="whole_stream"
    )
    with pytest.raises(AppError) as caught:
        load_config(config_writer([bad_enum]))
    assert caught.value.code == "CFG_BAD_ENUM"

    missing_end = stream_factory(
        source,
        group_by=["site"],
        sequence_field=None,
        coverage_mode="declared_window",
        coverage_window_start_ms=WINDOW_START,
    )
    with pytest.raises(AppError) as caught:
        load_config(config_writer([missing_end]))
    assert caught.value.code == "CFG_MISSING_FIELD"

    inverted = stream_factory(
        source,
        group_by=["site"],
        sequence_field=None,
        coverage_mode="declared_window",
        coverage_window_start_ms=WINDOW_END,
        coverage_window_end_ms=WINDOW_START,
    )
    with pytest.raises(AppError) as caught:
        load_config(config_writer([inverted]))
    assert caught.value.code == "CFG_BAD_TYPE"


def test_end_to_end_group_layer_and_byte_stable_output(tmp_path):
    config = load_config(Path("configs/coverage.yaml"))
    verification = run_verification(config)
    report = build_report(verification)
    stream = report["streams"][0]
    assert stream["coverage_mode"] == "declared_window"
    groups = {group["group_key"]: group for group in stream["groups"]}
    assert groups["site-c/line-09/sensor-z"]["expected_points"] == 11
    assert groups["site-c/line-09/sensor-z"]["missing_slots"] == 9
    assert groups["site-d/line-10/sensor-w"]["expected_points"] == 11
    assert groups["site-d/line-10/sensor-w"]["missing_slots"] == 8
    assert all(group["status"] == "anomalous" for group in stream["groups"])

    first = tmp_path / "first"
    second = tmp_path / "second"
    from sampleripple.cli import main

    assert main(["verify", "--config", "configs/coverage.yaml",
                 "--out", str(first)]) == 0
    assert main(["verify", "--config", "configs/coverage.yaml",
                 "--out", str(second)]) == 0
    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()
    assert (first / "report.md").read_bytes() == (second / "report.md").read_bytes()
    payload = json.loads((first / "report.json").read_text(encoding="utf-8"))
    assert payload["summary"]["expected_points"] == 22
    assert payload["summary"]["missing_slots"] == 17


def test_mismatched_window_fails_the_whole_verification(
    config_writer, stream_factory, tmp_path
):
    source = tmp_path / "s.csv"
    source.write_text(
        "site,ts\n"
        f"g,{WINDOW_START - PERIOD}\n"
        f"g,{WINDOW_START}\n",
        encoding="utf-8",
        newline="\n",
    )
    stream = stream_factory(
        source,
        group_by=["site"],
        sequence_field=None,
        coverage_mode="declared_window",
        coverage_window_start_ms=WINDOW_START,
        coverage_window_end_ms=WINDOW_END,
    )
    path = config_writer([stream])
    with pytest.raises(AppError) as caught:
        run_verification(load_config(path))
    assert caught.value.code == "COVERAGE_WINDOW_MISMATCH"
