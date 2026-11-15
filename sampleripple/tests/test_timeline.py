from __future__ import annotations

import pytest

from sampleripple.loaders import load_stream
from sampleripple.timeline import build_timeline, build_timelines


def _stream(demo_config, stream_id: str):
    return next(item for item in demo_config.streams if item.stream_id == stream_id)


def _timeline(demo_config, stream_id: str):
    contract = _stream(demo_config, stream_id)
    return build_timelines(load_stream(contract).points, contract)[0]


def test_clean_timeline_reconstruction(demo_config):
    contract = _stream(demo_config, "clean-grid")
    timelines = build_timelines(load_stream(contract).points, contract)
    assert [item.group_key for item in timelines] == [
        "site-a/line-07/sensor-x",
        "site-b/line-08/sensor-y",
    ]
    timeline = timelines[0]
    assert timeline.expected_points == 6
    assert timeline.missing_slots == ()
    assert timeline.slots == (0, 1, 2, 3, 4, 5)
    assert timeline.period_fit_ms == pytest.approx(60000.0)
    assert timeline.alignment_offset_ms == 0.0
    # clean-grid declares an on-grid epoch basis: the anchor leaves the
    # group-first sample and moves to the declared stream-level clock.
    assert timeline.anchor_source == "epoch"
    assert timeline.anchor_ms == 1717200000000
    assert timeline.basis_ms == 1717200000000
    assert timeline.anchor_offset_ms == 0
    assert timeline.grid_origin_ms == 1717200000000


def test_gap_timeline_has_missing_slot(demo_config):
    timeline = _timeline(demo_config, "gap-run")
    assert len(timeline.points) == 5
    assert timeline.expected_points == 6
    assert timeline.missing_slots == (3,)
    assert timeline.period_fit_ms == pytest.approx(60000.0)


def test_duplicate_timeline_keeps_rows_but_deduplicates_timestamps(demo_config):
    timeline = _timeline(demo_config, "dup-run")
    assert len(timeline.points) == 7
    assert len(timeline.unique_points) == 6
    assert timeline.missing_slots == ()


def test_drift_fit(demo_config):
    timeline = _timeline(demo_config, "drift-run")
    assert timeline.period_fit_ms == pytest.approx(60300.0)
    # The declared epoch basis sits 3000ms before the first sample, so the
    # median phase residual is measured on the declared clock, not the grid.
    assert timeline.alignment_offset_ms == 4350.0
    assert timeline.anchor_source == "epoch"
    assert timeline.anchor_ms == 1717286397000
    assert timeline.anchor_offset_ms == -3000


def test_mixed_groups_share_one_epoch_basis(demo_config):
    contract = _stream(demo_config, "mixed-run")
    timelines = build_timelines(load_stream(contract).points, contract)
    assert {item.anchor_ms for item in timelines} == {1717632003000}
    assert {item.anchor_source for item in timelines} == {"epoch"}
    assert {item.anchor_offset_ms for item in timelines} == {3000}
    by_group = {item.group_key: item for item in timelines}
    sensor_x = by_group["site-a/line-07/sensor-x"]
    assert sensor_x.alignment_offset_ms == 0.0
    assert sensor_x.slots == (0, 1, 2, 3, 4)
    sensor_y = by_group["site-a/line-08/sensor-y"]
    assert sensor_y.alignment_offset_ms == -3000.0
    assert sensor_y.missing_slots == (2,)
    assert sensor_y.expected_ms(2) == 1717632003000 + 2 * 60000
    sensor_z = by_group["site-b/line-09/sensor-z"]
    assert sensor_z.alignment_offset_ms == -3000.0


def test_group_first_anchor_is_the_default(demo_config):
    timeline = _timeline(demo_config, "gap-run")
    assert timeline.anchor_source == "group_first"
    assert timeline.basis_ms is None
    assert timeline.anchor_ms == timeline.unique_points[0].ts_ms
    assert timeline.anchor_offset_ms == 0
    assert timeline.grid_origin_ms == 0
    assert timeline.missing_slots == (3,)


def test_disorder_raw_order_is_preserved(demo_config):
    timeline = _timeline(demo_config, "disorder-run")
    raw_ts = [point.ts_ms for point in timeline.raw_points]
    sorted_ts = [point.ts_ms for point in timeline.unique_points]
    assert raw_ts[1] > raw_ts[2]
    assert sorted_ts == sorted(sorted_ts)
    assert len(raw_ts) == len(sorted_ts)


def test_build_timeline_requires_points():
    with pytest.raises(ValueError):
        build_timeline(("a",), [], 60000)
