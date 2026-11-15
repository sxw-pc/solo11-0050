from __future__ import annotations

import pytest

from sampleripple.loaders import Point, load_stream
from sampleripple.timeline import build_timeline, build_timelines


def _point(ordinal: int, ts_ms: int, *, sequence: str | None = None) -> Point:
    return Point(
        group=("g",),
        group_key="g",
        ts_ms=ts_ms,
        sequence=sequence,
        ordinal=ordinal,
        source_file="synthetic.csv",
        line_no=ordinal + 2,
    )


def _grid(n: int, period: int = 60000, *, offset: int = 0) -> list[Point]:
    return [_point(i, offset + i * period) for i in range(n)]


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
    assert timeline.alignment_offset_ms == 1350.0


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


def test_default_estimator_is_robust(demo_config):
    timeline = _timeline(demo_config, "clean-grid")
    assert timeline.period_estimator == "robust"


def test_robust_fit_resists_a_single_whole_period_outlier():
    # Ten clean grid points plus an eleventh whose offset is close to a full
    # nominal period. Least squares chases the leverage point; the median slope
    # of pairwise fits cannot be moved by the pairs touching that one point.
    points = _grid(10) + [_point(10, 10 * 60000 + 25000)]
    robust = build_timeline(("g",), points, 60000, "robust")
    squares = build_timeline(("g",), points, 60000, "least_squares")
    assert robust.period_fit_ms == pytest.approx(60000.0)
    assert squares.period_fit_ms == pytest.approx(61136.364, abs=1e-3)
    assert squares.period_fit_ms != pytest.approx(robust.period_fit_ms)


def test_robust_and_least_squares_agree_on_a_clean_grid():
    points = _grid(8)
    robust = build_timeline(("g",), points, 60000, "robust")
    squares = build_timeline(("g",), points, 60000, "least_squares")
    assert robust.period_fit_ms == pytest.approx(60000.0)
    assert squares.period_fit_ms == pytest.approx(60000.0)


def test_fit_deduplicates_points_sharing_a_slot():
    # Two records land in slot 1 (60000 and 60300); only the first enters the fit.
    points = _grid(2) + [
        _point(2, 60300, sequence="1b"),
        _point(3, 120000),
        _point(4, 180000),
    ]
    robust = build_timeline(("g",), points, 60000, "robust")
    squares = build_timeline(("g",), points, 60000, "least_squares")
    assert len(robust.unique_points) == 5
    assert robust.fit_slots == (0, 1, 2, 3)
    assert len(robust.fit_points) == 4
    assert robust.fit_points[1].ts_ms == 60000
    assert robust.fit_last_slot == 3
    # The jittered duplicate must not pull either estimate off the nominal grid.
    assert robust.period_fit_ms == pytest.approx(60000.0)
    assert squares.period_fit_ms == pytest.approx(60000.0)


def test_slot_dedup_changes_legacy_least_squares_on_collision():
    # A late record collides with an existing slot (full period plus jitter);
    # before slot-level dedup its residual entered the fit a second time.
    points = _grid(4) + [
        _point(4, 4 * 60000 + 61500),
        _point(5, 5 * 60000),
        _point(6, 6 * 60000),
    ]
    squares = build_timeline(("g",), points, 60000, "least_squares")
    assert [point.ts_ms for point in squares.unique_points] == [
        0, 60000, 120000, 180000, 300000, 301500, 360000,
    ]
    assert squares.slots == (0, 1, 2, 3, 5, 5, 6)
    assert squares.fit_slots == (0, 1, 2, 3, 5, 6)
    assert squares.fit_points[-2].ts_ms == 300000
    assert squares.fit_last_slot == 6
    assert squares.period_fit_ms == pytest.approx(60000.0)


def test_least_squares_fit_matches_legacy_formula_on_drift_fixture(demo_config):
    contract = _stream(demo_config, "drift-run")
    points = load_stream(contract).points
    robust = build_timelines(points, contract)[0]
    squares = build_timeline(
        robust.group, points, contract.nominal_period_ms, "least_squares"
    )
    assert squares.period_fit_ms == pytest.approx(60300.0)
    assert robust.period_fit_ms == pytest.approx(squares.period_fit_ms)


def test_fit_is_none_without_two_distinct_slots():
    points = [_point(0, 1000), _point(1, 1050)]
    for estimator in ("robust", "least_squares"):
        timeline = build_timeline(("g",), points, 60000, estimator)
        assert timeline.period_fit_ms is None
        assert timeline.fit_last_slot is None


def test_robust_fit_is_deterministic():
    points = _grid(5) + [_point(5, 5 * 60000 + 9000)]
    first = build_timeline(("g",), points, 60000, "robust")
    second = build_timeline(("g",), points, 60000, "robust")
    assert first.period_fit_ms == second.period_fit_ms
    assert first.fit_slots == second.fit_slots
