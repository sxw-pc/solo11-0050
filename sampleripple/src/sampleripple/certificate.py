"""Sensitivity certificate data model shared by engine, reports and ledger.

Records, per group and rule, how wide the governing contract tolerance may
move (integer ms or integer slots) before the rule's trigger state flips.
Rule-to-tolerance mapping (not one-to-one):

* GAP_ADJACENT        -> gap_tolerance_ms            (adjacent interval threshold)
* DRIFT_CUMULATIVE    -> drift_tolerance_ms          (trailing mean comparison)
* MISSING_SLOT        -> missing_allow_k             (whole-group silence count)
* DISORDER_LOCAL      -> disorder_window             (look-back window size)
* EDGE_SKEW_HEAD/TAIL -> edge_tolerance_ms           (first/last boundary offset)
* PERIOD_DRIFT_FIT    -> period_drift_tolerance_ms   (floating slope distance)
* DUPLICATE_POINT     -> no tolerance governs it; never adjustable

``jitter_tolerance_ms`` only supplies the fallback default for optional
tolerances; no detection rule reads it directly, so no rule maps to it.
"""

from __future__ import annotations

from dataclasses import dataclass

DIRECTION_DECREASE = "decrease"
DIRECTION_INCREASE = "increase"

REASON_POLICY = "governed_by_policy_not_tolerance"
REASON_NO_DATA = "no_structural_basis_in_timeline"
REASON_SATURATED_DOWN = "already_at_zero_tolerance"
REASON_SATURATED_UP = "fully_saturated_within_integral_range"
REASON_EXACT = "exact_integer_boundary"


@dataclass(frozen=True)
class FlipBoundary:
    """One integer adjustment that flips a rule, verified by re-evaluation."""

    direction: str
    delta: int
    adjusted_value: int
    current_value: int
    triggered_now: bool
    triggered_after: bool


@dataclass(frozen=True)
class RuleSensitivity:
    """Certificate row for one (group, rule)."""

    rule_id: str
    category: str
    parameter: str | None
    current_value: int | None
    triggered: bool
    stable_delta: int
    flippable: bool
    reason: str
    decrease: FlipBoundary | None
    increase: FlipBoundary | None


@dataclass(frozen=True)
class GroupSensitivity:
    stream_id: str
    group_key: str
    rules: tuple[RuleSensitivity, ...]

    @property
    def flippable(self) -> bool:
        return any(item.flippable for item in self.rules)


def _groups(certificate):
    """Flatten either a per-group tuple or the per-stream nested tuple."""

    return [
        group
        for item in certificate
        for group in (item if isinstance(item, tuple) else (item,))
    ]


def has_flip(certificate) -> bool:
    return any(group.flippable for group in _groups(certificate))
