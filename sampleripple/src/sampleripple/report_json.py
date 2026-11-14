"""Build the canonical report object without runtime-dependent fields."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from . import __version__
from .anomalies import CATEGORIES, RULES, SEVERITIES, Anomaly
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
    return {
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
