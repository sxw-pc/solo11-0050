"""Seven-category anomaly detection over reconstructed timelines."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Contract
from .timeline import GroupTimeline
from .util.timeutil import nearest_boundary_ms

CATEGORIES = (
    "GAP",
    "DRIFT",
    "DUPLICATE",
    "DISORDER",
    "MISSING",
    "EDGE_SKEW",
    "PERIOD_DRIFT",
)
RULE_CATEGORY = {
    "GAP_ADJACENT": "GAP",
    "DRIFT_CUMULATIVE": "DRIFT",
    "DUPLICATE_POINT": "DUPLICATE",
    "DISORDER_LOCAL": "DISORDER",
    "MISSING_SLOT": "MISSING",
    "EDGE_SKEW_HEAD": "EDGE_SKEW",
    "EDGE_SKEW_TAIL": "EDGE_SKEW",
    "PERIOD_DRIFT_FIT": "PERIOD_DRIFT",
}
RULES = tuple(RULE_CATEGORY)
SEVERITIES = ("info", "warning", "error", "critical")
DRIFT_WINDOW = 100


@dataclass(frozen=True)
class Anomaly:
    stream_id: str
    group_key: str
    index: int
    expected_ms: int
    actual_ms: int
    delta_ms: int
    rule_id: str
    category: str
    severity: str
    detail: str
    fit_period_ms: float | None = None

    def __post_init__(self) -> None:
        if self.rule_id not in RULE_CATEGORY:
            raise ValueError(f"unknown rule id: {self.rule_id}")
        if self.category != RULE_CATEGORY[self.rule_id]:
            raise ValueError("rule/category mismatch")


def _anomaly(
    contract: Contract,
    timeline: GroupTimeline,
    rule_id: str,
    index: int,
    expected_ms: int,
    actual_ms: int,
    severity: str,
    detail: str,
    fit_period_ms: float | None = None,
) -> Anomaly:
    return Anomaly(
        stream_id=contract.stream_id,
        group_key=timeline.group_key,
        index=index,
        expected_ms=expected_ms,
        actual_ms=actual_ms,
        delta_ms=actual_ms - expected_ms,
        rule_id=rule_id,
        category=RULE_CATEGORY[rule_id],
        severity=severity,
        detail=detail,
        fit_period_ms=fit_period_ms,
    )


def _duplicates(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    if contract.duplicate_policy == "ignore":
        return []
    found: list[Anomaly] = []
    first_ts: dict[int, int] = {}
    first_seq: dict[str, int] = {}
    for index, point in enumerate(timeline.points):
        markers: list[str] = []
        expected = point.ts_ms
        if point.ts_ms in first_ts:
            markers.append("timestamp")
            expected = first_ts[point.ts_ms]
        else:
            first_ts[point.ts_ms] = point.ts_ms
        if point.sequence is not None:
            if point.sequence in first_seq:
                markers.append("sequence")
                if "timestamp" not in markers:
                    expected = first_seq[point.sequence]
            else:
                first_seq[point.sequence] = point.ts_ms
        if markers:
            found.append(
                _anomaly(
                    contract, timeline, "DUPLICATE_POINT", index,
                    expected, point.ts_ms, "error",
                    "duplicate " + "+".join(markers),
                )
            )
    return found


def _disorder(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    if contract.disorder_window <= 0:
        return []
    found: list[Anomaly] = []
    raw = timeline.raw_points
    for index in range(1, len(raw)):
        current = raw[index]
        lookback = raw[max(0, index - contract.disorder_window) : index]
        greater = [point.ts_ms for point in lookback if point.ts_ms > current.ts_ms]
        if greater:
            expected = max(greater)
            found.append(
                _anomaly(
                    contract, timeline, "DISORDER_LOCAL", index,
                    expected, current.ts_ms, "warning",
                    "local timestamp inversion inside disorder_window",
                )
            )
    return found


def _gaps(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    found: list[Anomaly] = []
    threshold = contract.nominal_period_ms + contract.gap_tolerance_ms
    unique = timeline.unique_points
    for index in range(1, len(unique)):
        interval = unique[index].ts_ms - unique[index - 1].ts_ms
        if interval > threshold:
            expected = unique[index - 1].ts_ms + contract.nominal_period_ms
            severity = (
                "warning" if interval <= 2 * contract.nominal_period_ms else "error"
            )
            found.append(
                _anomaly(
                    contract, timeline, "GAP_ADJACENT", index,
                    expected, unique[index].ts_ms, severity,
                    f"adjacent interval {interval}ms exceeds {threshold}ms",
                )
            )
    return found


def _missing(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    if contract.missing_policy == "ignore" or not timeline.missing_slots:
        return []
    if (
        contract.missing_policy == "allow_k"
        and len(timeline.missing_slots) <= contract.missing_allow_k
    ):
        return []
    severity = "error" if contract.missing_policy == "strict" else "warning"
    found: list[Anomaly] = []
    for slot in timeline.missing_slots:
        expected = timeline.expected_ms(slot)
        found.append(
            _anomaly(
                contract, timeline, "MISSING_SLOT", slot, expected, expected,
                severity,
                f"absent slot; missing_count={len(timeline.missing_slots)} "
                f"policy={contract.missing_policy}",
            )
        )
    return found


def _edges(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    found: list[Anomaly] = []
    first = timeline.unique_points[0]
    first_boundary = nearest_boundary_ms(first.ts_ms, contract.nominal_period_ms)
    if abs(first.ts_ms - first_boundary) > contract.edge_tolerance_ms:
        found.append(
            _anomaly(
                contract, timeline, "EDGE_SKEW_HEAD", 0,
                first_boundary, first.ts_ms, "warning",
                "first sample is off the nominal epoch boundary",
            )
        )
    last_index = len(timeline.unique_points) - 1
    if last_index > 0:
        last = timeline.unique_points[-1]
        last_boundary = nearest_boundary_ms(last.ts_ms, contract.nominal_period_ms)
        if abs(last.ts_ms - last_boundary) > contract.edge_tolerance_ms:
            found.append(
                _anomaly(
                    contract, timeline, "EDGE_SKEW_TAIL", last_index,
                    last_boundary, last.ts_ms, "warning",
                    "last sample is off the nominal epoch boundary",
                )
            )
    return found


def _drift(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    residual_pairs = [
        (index, point, point.ts_ms - timeline.expected_ms(slot))
        for index, (point, slot) in enumerate(
            zip(timeline.unique_points, timeline.slots)
        )
    ]
    found: list[Anomaly] = []
    for position in range(2, len(residual_pairs)):
        window = residual_pairs[max(0, position - DRIFT_WINDOW + 1) : position + 1]
        mean = sum(residual for _, _, residual in window) / len(window)
        if abs(mean) <= contract.drift_tolerance_ms:
            continue
        index, point, residual = residual_pairs[position]
        expected = timeline.expected_ms(timeline.slots[position])
        severity = (
            "warning" if abs(mean) <= 2 * contract.drift_tolerance_ms else "error"
        )
        found.append(
            _anomaly(
                contract, timeline, "DRIFT_CUMULATIVE", index,
                expected, point.ts_ms, severity,
                f"trailing_mean_residual_ms={mean:.3f}",
            )
        )
    return found


def _period_drift(contract: Contract, timeline: GroupTimeline) -> list[Anomaly]:
    fit = timeline.period_fit_ms
    last_slot = timeline.fit_last_slot
    if fit is None or last_slot is None:
        return []
    if abs(fit - contract.nominal_period_ms) <= contract.period_drift_tolerance_ms:
        return []
    last = timeline.fit_points[-1]
    expected = timeline.expected_ms(last_slot)
    label = (
        "least_squares"
        if timeline.period_estimator == "least_squares"
        else "robust"
    )
    return [
        _anomaly(
            contract, timeline, "PERIOD_DRIFT_FIT", len(timeline.fit_points) - 1,
            expected, last.ts_ms, "error",
            f"{label}_period_ms={fit:.3f}", fit_period_ms=fit,
        )
    ]


def anomaly_sort_key(anomaly: Anomaly) -> tuple[object, ...]:
    return (
        anomaly.group_key,
        anomaly.index,
        RULES.index(anomaly.rule_id),
        anomaly.expected_ms,
        anomaly.actual_ms,
        anomaly.detail,
    )


def detect_anomalies(contract: Contract, timeline: GroupTimeline) -> tuple[Anomaly, ...]:
    found = (
        _duplicates(contract, timeline)
        + _disorder(contract, timeline)
        + _gaps(contract, timeline)
        + _missing(contract, timeline)
        + _edges(contract, timeline)
        + _drift(contract, timeline)
        + _period_drift(contract, timeline)
    )
    return tuple(sorted(found, key=anomaly_sort_key))
