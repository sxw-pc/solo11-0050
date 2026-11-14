"""Deterministic per-group timeline reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable

from .contracts import Contract
from .loaders import Point
from .util.timeutil import nearest_boundary_ms, round_half_up


@dataclass(frozen=True)
class GroupTimeline:
    group: tuple[str, ...]
    group_key: str
    raw_points: tuple[Point, ...]
    points: tuple[Point, ...]
    unique_points: tuple[Point, ...]
    slots: tuple[int, ...]
    anchor_ms: int
    last_ms: int
    expected_points: int
    missing_slots: tuple[int, ...]
    alignment_offset_ms: float
    period_fit_ms: float | None
    nominal_period_ms: int

    def expected_ms(self, slot: int) -> int:
        return self.anchor_ms + slot * self.nominal_period_ms


def _chronological_key(point: Point) -> tuple[int, str, int]:
    return (point.ts_ms, point.sequence or "", point.ordinal)


def _slot_for(timestamp_ms: int, anchor_ms: int, period_ms: int) -> int:
    return round_half_up(timestamp_ms - anchor_ms, period_ms)


def _alignment_offset(points: Iterable[Point], period_ms: int) -> float:
    residuals = [
        point.ts_ms - nearest_boundary_ms(point.ts_ms, period_ms)
        for point in points
    ]
    return float(median(residuals)) if residuals else 0.0


def _period_fit(unique_points: tuple[Point, ...], slots: tuple[int, ...]) -> float | None:
    if len(unique_points) < 2 or len(set(slots)) < 2:
        return None
    xs = [float(slot) for slot in slots]
    ys = [float(point.ts_ms - unique_points[0].ts_ms) for point in unique_points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return None
    return numerator / denominator


def build_timeline(group: tuple[str, ...], points: Iterable[Point], period_ms: int) -> GroupTimeline:
    raw_points = tuple(sorted(points, key=lambda point: point.ordinal))
    if not raw_points:
        raise ValueError("cannot build a timeline without points")
    ordered = tuple(sorted(raw_points, key=_chronological_key))
    unique_points: list[Point] = []
    seen_timestamps: set[int] = set()
    for point in ordered:
        if point.ts_ms not in seen_timestamps:
            unique_points.append(point)
            seen_timestamps.add(point.ts_ms)
    unique = tuple(unique_points)
    anchor = unique[0].ts_ms
    last = unique[-1].ts_ms
    expected_points = (last - anchor) // period_ms + 1
    slots = tuple(_slot_for(point.ts_ms, anchor, period_ms) for point in unique)
    present = set(slots)
    missing = tuple(slot for slot in range(expected_points) if slot not in present)
    return GroupTimeline(
        group=group,
        group_key="/".join(group),
        raw_points=raw_points,
        points=ordered,
        unique_points=unique,
        slots=slots,
        anchor_ms=anchor,
        last_ms=last,
        expected_points=expected_points,
        missing_slots=missing,
        alignment_offset_ms=_alignment_offset(unique, period_ms),
        period_fit_ms=_period_fit(unique, slots),
        nominal_period_ms=period_ms,
    )


def build_timelines(points: Iterable[Point], contract: Contract) -> tuple[GroupTimeline, ...]:
    grouped: dict[str, list[Point]] = {}
    group_tuples: dict[str, tuple[str, ...]] = {}
    for point in points:
        grouped.setdefault(point.group_key, []).append(point)
        group_tuples.setdefault(point.group_key, point.group)
    timelines = [
        build_timeline(
            group_tuples[key], grouped[key], contract.nominal_period_ms
        )
        for key in sorted(grouped)
    ]
    return tuple(timelines)
