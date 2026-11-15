"""Exact integer flip solver for the seven rule families.

For every group timeline and rule, the solver finds the smallest integer
adjustment of the rule-governing contract tolerance that changes whether the
rule fires.  The predicate is re-evaluated on a contract copy so the solver
uses the exact same rule code as verification.

Every predicate is monotonic in its governing integer parameter, which allows
an exact integer binary search.  PERIOD_DRIFT compares an integer tolerance
against a floating slope distance, so every critical integer is *verified* by
evaluating both sides (the current integer keeps the conclusion and the
adjacent integer changes it) instead of rounding the float difference.  The
rule-to-tolerance mapping lives in :mod:`sampleripple.certificate`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from . import anomalies
from .certificate import (
    REASON_EXACT,
    REASON_NO_DATA,
    REASON_POLICY,
    REASON_SATURATED_DOWN,
    REASON_SATURATED_UP,
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    FlipBoundary,
    GroupSensitivity,
    RuleSensitivity,
)
from .contracts import Contract
from .timeline import GroupTimeline
from .util.timeutil import nearest_boundary_ms


@dataclass(frozen=True)
class _RuleSpec:
    parameter: str | None
    rule_fn: Callable[[Contract, GroupTimeline], list[anomalies.Anomaly]] | None
    # Structural parameter value at which the predicate is fully saturated.
    limit_fn: Callable[[Contract, GroupTimeline], int] | None


def _fires(contract: Contract, timeline: GroupTimeline, rule_id: str) -> bool:
    spec = _RULE_SPECS[rule_id]
    if spec.rule_fn is None:
        return False
    return any(item.rule_id == rule_id for item in spec.rule_fn(contract, timeline))


def _gap_limit(contract: Contract, timeline: GroupTimeline) -> int:
    unique = timeline.unique_points
    widest = max(
        (unique[i].ts_ms - unique[i - 1].ts_ms for i in range(1, len(unique))),
        default=0,
    )
    return max(0, widest - contract.nominal_period_ms)


def _drift_limit(contract: Contract, timeline: GroupTimeline) -> int:
    residuals = [
        point.ts_ms - timeline.expected_ms(slot)
        for point, slot in zip(timeline.unique_points, timeline.slots)
    ]
    if len(residuals) < 3:
        return 0
    ceiling = 0
    for position in range(2, len(residuals)):
        window = residuals[max(0, position - anomalies.DRIFT_WINDOW + 1): position + 1]
        ceiling = max(ceiling, int(abs(sum(window) / len(window))) + 1)
    return ceiling


def _edge_limit(contract: Contract, timeline: GroupTimeline) -> int:
    return max(
        (
            abs(point.ts_ms - nearest_boundary_ms(
                point.ts_ms, contract.nominal_period_ms
            ))
            for point in timeline.unique_points
        ),
        default=0,
    )


def _period_limit(contract: Contract, timeline: GroupTimeline) -> int:
    if timeline.period_fit_ms is None:
        return 0
    return max(0, int(abs(timeline.period_fit_ms - contract.nominal_period_ms)) + 1)


_RULE_SPECS: dict[str, _RuleSpec] = {
    "GAP_ADJACENT": _RuleSpec("gap_tolerance_ms", anomalies._gaps, _gap_limit),
    "DRIFT_CUMULATIVE": _RuleSpec(
        "drift_tolerance_ms", anomalies._drift, _drift_limit
    ),
    "DUPLICATE_POINT": _RuleSpec(None, anomalies._duplicates, None),
    "DISORDER_LOCAL": _RuleSpec(
        "disorder_window",
        anomalies._disorder,
        lambda contract, timeline: max(1, len(timeline.raw_points) - 1),
    ),
    # At k == missing_count the allow_k predicate silences the whole group.
    "MISSING_SLOT": _RuleSpec(
        "missing_allow_k",
        anomalies._missing,
        lambda contract, timeline: len(timeline.missing_slots),
    ),
    "EDGE_SKEW_HEAD": _RuleSpec(
        "edge_tolerance_ms", anomalies._edges, _edge_limit
    ),
    "EDGE_SKEW_TAIL": _RuleSpec(
        "edge_tolerance_ms", anomalies._edges, _edge_limit
    ),
    "PERIOD_DRIFT_FIT": _RuleSpec(
        "period_drift_tolerance_ms", anomalies._period_drift, _period_limit
    ),
}


def _contract_at(contract: Contract, parameter: str, value: int) -> Contract:
    return replace(contract, **{parameter: value})


def _boundary(
    contract, timeline, rule_id, parameter, current, adjusted, direction
) -> FlipBoundary:
    # Exactness guard: the current integer keeps the conclusion while the
    # adjacent adjusted integer changes it.  This also validates the float
    # PERIOD_DRIFT comparison without ever rounding the slope distance.
    now = _fires(contract, timeline, rule_id)
    after = _fires(_contract_at(contract, parameter, adjusted), timeline, rule_id)
    if now == after:
        raise AssertionError(
            f"sensitivity boundary not exact for {rule_id} {direction}: "
            f"{current} -> {adjusted}"
        )
    return FlipBoundary(
        direction=direction,
        delta=abs(adjusted - current),
        adjusted_value=adjusted,
        current_value=current,
        triggered_now=now,
        triggered_after=after,
    )


def _binary_flip(
    contract, timeline, rule_id, parameter, current, extreme, direction
) -> FlipBoundary | None:
    """Find the nearest integer between ``current`` and ``extreme`` that flips.

    The predicate is monotonic over the integer parameter range, so a binary
    search locates the transition; ``_boundary`` then re-verifies both sides
    (the current integer keeps the conclusion, the adjacent one changes it).
    """

    state_now = _fires(contract, timeline, rule_id)
    state_extreme = _fires(
        _contract_at(contract, parameter, extreme), timeline, rule_id
    )
    if extreme == current or state_extreme == state_now:
        return None
    low, high = current, extreme
    while abs(high - low) > 1:
        mid = (low + high) // 2
        state_mid = _fires(
            _contract_at(contract, parameter, mid), timeline, rule_id
        )
        # ``low`` always keeps the current conclusion; ``high`` is flipped.
        if state_mid == state_now:
            low = mid
        else:
            high = mid
    return _boundary(
        contract, timeline, rule_id, parameter, current, high, direction
    )


def _rule_sensitivity(
    contract: Contract, timeline: GroupTimeline, rule_id: str
) -> RuleSensitivity:
    spec = _RULE_SPECS[rule_id]
    triggered = _fires(contract, timeline, rule_id)
    category = anomalies.RULE_CATEGORY[rule_id]

    def inert(parameter: str | None, current: int | None, reason: str) -> RuleSensitivity:
        return RuleSensitivity(
            rule_id, category, parameter, current, triggered, -1, False,
            reason, None, None,
        )

    if spec.parameter is None:
        return inert(None, None, REASON_POLICY)
    parameter = spec.parameter
    current = int(getattr(contract, parameter))
    if parameter == "missing_allow_k" and contract.missing_policy != "allow_k":
        return inert(parameter, current, REASON_POLICY)
    if not timeline.missing_slots and rule_id == "MISSING_SLOT":
        return inert(parameter, current, REASON_NO_DATA)
    if rule_id == "PERIOD_DRIFT_FIT" and timeline.period_fit_ms is None:
        return inert(parameter, current, REASON_NO_DATA)
    assert spec.limit_fn is not None
    saturation = spec.limit_fn(contract, timeline)
    decrease = (
        _binary_flip(
            contract, timeline, rule_id, parameter, current, 0,
            DIRECTION_DECREASE,
        )
        if current > 0
        else None
    )
    increase = _binary_flip(
        contract, timeline, rule_id, parameter, current, saturation,
        DIRECTION_INCREASE,
    )
    deltas = [item.delta for item in (decrease, increase) if item is not None]
    if not deltas:
        reason = REASON_SATURATED_DOWN if current <= 0 else REASON_SATURATED_UP
        return inert(parameter, current, reason)
    return RuleSensitivity(
        rule_id, category, parameter, current, triggered, min(deltas) - 1,
        True, REASON_EXACT, decrease, increase,
    )


def build_group_sensitivity(
    contract: Contract, timeline: GroupTimeline
) -> GroupSensitivity:
    rows = tuple(
        _rule_sensitivity(contract, timeline, rule_id) for rule_id in anomalies.RULES
    )
    return GroupSensitivity(contract.stream_id, timeline.group_key, rows)


def build_sensitivity(
    contract: Contract, timelines: tuple[GroupTimeline, ...]
) -> tuple[GroupSensitivity, ...]:
    return tuple(
        build_group_sensitivity(contract, timeline) for timeline in timelines
    )
