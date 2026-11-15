"""Build the canonical report object without runtime-dependent fields."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from . import __version__
from .anomalies import CATEGORIES, RULES, SEVERITIES, Anomaly
from .certificate import FlipBoundary, GroupSensitivity, RuleSensitivity, has_flip
from .engine import StreamRun, Verification
from .timeline import GroupTimeline
from .util.timeutil import iso_from_ms


def _category_counts(anomalies: Iterable[Anomaly]) -> dict[str, int]:
    counts = Counter(anomaly.category for anomaly in anomalies)
    return {category: counts.get(category, 0) for category in CATEGORIES}


def _rule_counts(anomalies: Iterable[Anomaly]) -> dict[str, int]:
    counts = Counter(anomaly.rule_id for anomaly in anomalies)
    return {rule: counts.get(rule, 0) for rule in RULES}


def _severity_counts(anomalies: Iterable[Anomaly]) -> dict[str, int]:
    counts = Counter(anomaly.severity for anomaly in anomalies)
    return {severity: counts.get(severity, 0) for severity in SEVERITIES}


def anomaly_dict(anomaly: Anomaly) -> dict[str, object]:
    data: dict[str, object] = {
        "actual_at": iso_from_ms(anomaly.actual_ms),
        "actual_ms": anomaly.actual_ms,
        "category": anomaly.category,
        "delta_ms": anomaly.delta_ms,
        "detail": anomaly.detail,
        "expected_at": iso_from_ms(anomaly.expected_ms),
        "expected_ms": anomaly.expected_ms,
        "group_key": anomaly.group_key,
        "index": anomaly.index,
        "rule_id": anomaly.rule_id,
        "severity": anomaly.severity,
        "stream_id": anomaly.stream_id,
    }
    if anomaly.fit_period_ms is not None:
        data["fit_period_ms"] = anomaly.fit_period_ms
    return data


def _boundary_dict(boundary: FlipBoundary) -> dict[str, object]:
    return {
        "adjusted_value": boundary.adjusted_value,
        "current_value": boundary.current_value,
        "delta": boundary.delta,
        "direction": boundary.direction,
        "triggered_after": boundary.triggered_after,
        "triggered_now": boundary.triggered_now,
    }


def _rule_sensitivity_dict(row: RuleSensitivity) -> dict[str, object]:
    data: dict[str, object] = {
        "category": row.category,
        "decrease": _boundary_dict(row.decrease) if row.decrease else None,
        "flippable": row.flippable,
        "increase": _boundary_dict(row.increase) if row.increase else None,
        "parameter": row.parameter,
        "reason": row.reason,
        "rule_id": row.rule_id,
        "stable_delta": row.stable_delta,
        "triggered": row.triggered,
    }
    if row.current_value is not None:
        data["current_value"] = row.current_value
    return data


def _sensitivity_group_dict(group: GroupSensitivity) -> dict[str, object]:
    return {
        "flip_count": sum(1 for row in group.rules if row.flippable),
        "group_key": group.group_key,
        "rules": [_rule_sensitivity_dict(row) for row in group.rules],
        "stream_id": group.stream_id,
    }


def _sensitivity_certificate(
    verification: Verification,
) -> dict[str, object] | None:
    if not any(has_flip(certificate) for certificate in verification.sensitivity):
        return None
    groups = [
        _sensitivity_group_dict(group)
        for certificate in verification.sensitivity
        for group in certificate
    ]
    flippable = [
        row
        for certificate in verification.sensitivity
        for group in certificate
        for row in group.rules
        if row.flippable
    ]
    by_rule: dict[str, int] = {}
    for row in flippable:
        by_rule[row.rule_id] = by_rule.get(row.rule_id, 0) + 1
    return {
        "entry_count": len(groups),
        "flip_count": len(flippable),
        "groups": groups,
        "rule_aggregate": {rule: by_rule.get(rule, 0) for rule in RULES},
    }


def _group_dict(timeline: GroupTimeline, anomalies: tuple[Anomaly, ...]) -> dict[str, object]:
    first = timeline.unique_points[0]
    last = timeline.unique_points[-1]
    return {
        "actual_points": len(timeline.points),
        "alignment_offset_ms": timeline.alignment_offset_ms,
        "anomaly_count": len(anomalies),
        "by_category": _category_counts(anomalies),
        "expected_points": timeline.expected_points,
        "first_at": iso_from_ms(first.ts_ms),
        "first_ms": first.ts_ms,
        "group_key": timeline.group_key,
        "last_at": iso_from_ms(last.ts_ms),
        "last_ms": last.ts_ms,
        "missing_slots": len(timeline.missing_slots),
        "period_fit_ms": timeline.period_fit_ms,
        "status": "ok" if not anomalies else "anomalous",
        "unique_points": len(timeline.unique_points),
    }


def _stream_dict(stream: StreamRun) -> dict[str, object]:
    contract = stream.contract
    anomalies_by_group: dict[str, list[Anomaly]] = {}
    for anomaly in stream.anomalies:
        anomalies_by_group.setdefault(anomaly.group_key, []).append(anomaly)
    groups = [
        _group_dict(timeline, tuple(anomalies_by_group.get(timeline.group_key, ())))
        for timeline in stream.timelines
    ]
    actual_points = sum(item["actual_points"] for item in groups)
    unique_points = sum(item["unique_points"] for item in groups)
    expected_points = sum(item["expected_points"] for item in groups)
    return {
        "anomalies": [anomaly_dict(anomaly) for anomaly in stream.anomalies],
        "group_by": list(contract.group_by),
        "groups": groups,
        "nominal_period_ms": contract.nominal_period_ms,
        "stream_id": contract.stream_id,
        "summary": {
            "actual_points": actual_points,
            "anomaly_count": len(stream.anomalies),
            "by_category": _category_counts(stream.anomalies),
            "by_rule": _rule_counts(stream.anomalies),
            "by_severity": _severity_counts(stream.anomalies),
            "expected_points": expected_points,
            "group_count": len(groups),
            "missing_slots": sum(item["missing_slots"] for item in groups),
            "status": "ok" if not stream.anomalies else "anomalous",
            "unique_points": unique_points,
        },
    }


def build_report(verification: Verification) -> dict[str, object]:
    """Assemble the complete machine-readable report."""

    streams = [_stream_dict(stream) for stream in verification.streams]
    anomalies = verification.anomalies
    total_groups = sum(item["summary"]["group_count"] for item in streams)
    total_actual = sum(item["summary"]["actual_points"] for item in streams)
    total_unique = sum(item["summary"]["unique_points"] for item in streams)
    total_expected = sum(item["summary"]["expected_points"] for item in streams)
    total_missing = sum(item["summary"]["missing_slots"] for item in streams)
    certificate = _sensitivity_certificate(verification)
    report: dict[str, object] = {
        "anomalies": [anomaly_dict(anomaly) for anomaly in anomalies],
        "config_hash": verification.config.config_hash,
        "report_version": 1,
        "streams": streams,
        "summary": {
            "actual_points": total_actual,
            "anomaly_count": len(anomalies),
            "by_category": _category_counts(anomalies),
            "by_rule": _rule_counts(anomalies),
            "by_severity": _severity_counts(anomalies),
            "expected_points": total_expected,
            "group_count": total_groups,
            "missing_slots": total_missing,
            "status": "ok" if not anomalies else "anomalous",
            "stream_count": len(streams),
            "unique_points": total_unique,
        },
        "tool": {"name": "sampleripple", "version": __version__},
    }
    if certificate is not None:
        report["sensitivity_certificate"] = certificate
    return report
