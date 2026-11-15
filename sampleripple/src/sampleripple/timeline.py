"""Deterministic per-group timeline reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable

from .contracts import Contract
from .loaders import Point
from .util.timeutil import nearest_boundary_ms, round_half_up

ANCHOR_GROUP_FIRST = "group_first"
ANCHOR_EPOCH = "epoch"


@dataclass(frozen=True)
class GroupTimeline:
    group: tuple[str, ...]
    group_key: str
    raw_points: tuple[Point, ...]
    points: tuple[Point, ...]
    unique_points: tuple[Point, ...]
    slots: tuple[int, ...]
    anchor_ms: int
    anchor_source: str
    basis_ms: int | None
    last_ms: int
    expected_points: int
    missing_slots: tuple[int, ...]
    alignment_offset_ms: float
    period_fit_ms: float | None
    nominal_period_ms: int

    def expected_ms(self, slot: int) -> int:
        return self.anchor_ms + slot * self.nominal_period_ms

    @property
    def grid_origin_ms(self) -> int:
        """Origin of the residual grid used for alignment and edge checks."""

        return self.basis_ms if self.basis_ms is not None else 0

    @property
    def anchor_offset_ms(self) -> int:
        """Signed offset of the anchor from the absolute nominal epoch grid."""

        return self.anchor_ms - nearest_boundary_ms(
            self.anchor_ms, self.nominal_period_ms
        )


def _chronological_key(point: Point) -> tuple[int, str, int]:
    return (point.ts_ms, point.sequence or "", point.ordinal)


def _slot_for(timestamp_ms: int, anchor_ms: int, period_ms: int) -> int:
    return round_half_up(timestamp_ms - anchor_ms, period_ms)


def _alignment_offset(
    points: Iterable[Point], period_ms: int, origin_ms: int
) -> float:
    residuals = [
        (point.ts_ms - origin_ms)
        - nearest_boundary_ms(point.ts_ms - origin_ms, period_ms)
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


def build_timeline(
    group: tuple[str, ...],
    points: Iterable[Point],
    period_ms: int,
    epoch_align_ms: int | None = None,
) -> GroupTimeline:
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
    if epoch_align_ms is None:
        # Legacy behavior: every group anchors on its own first sample and the
        # expected window is derived from the raw observed span.  Frozen for
        # backward compatibility with pre-epoch contracts and goldens.
        anchor = unique[0].ts_ms
        anchor_source = ANCHOR_GROUP_FIRST
        expected_points = (unique[-1].ts_ms - anchor) // period_ms + 1
        slots = tuple(_slot_for(point.ts_ms, anchor, period_ms) for point in unique)
        present = set(slots)
        missing = tuple(slot for slot in range(expected_points) if slot not in present)
    else:
        # Stream-level nominal clock: every group of the stream shares the
        # declared epoch basis, so slot numbers, expected_ms and residuals are
        # measured on one ruler and are comparable across groups.
        anchor = epoch_align_ms
        anchor_source = ANCHOR_EPOCH
        slots = tuple(_slot_for(point.ts_ms, anchor, period_ms) for point in unique)
        present = set(slots)
        first_slot = slots[0]
        last_slot = slots[-1]
        expected_points = last_slot - first_slot + 1
        missing = tuple(
            slot for slot in range(first_slot, last_slot + 1) if slot not in present
        )
    return GroupTimeline(
        group=group,
        group_key="/".join(group),
        raw_points=raw_points,
        points=ordered,
        unique_points=unique,
        slots=slots,
        anchor_ms=anchor,
        anchor_source=anchor_source,
        basis_ms=epoch_align_ms,
        last_ms=unique[-1].ts_ms,
        expected_points=expected_points,
        missing_slots=missing,
        alignment_offset_ms=_alignment_offset(
            unique, period_ms, epoch_align_ms if epoch_align_ms is not None else 0
        ),
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
            group_tuples[key],
            grouped[key],
            contract.nominal_period_ms,
            contract.epoch_align_ms,
        )
        for key in sorted(grouped)
    ]
    return tuple(timelines)
