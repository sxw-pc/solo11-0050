"""Sampling-contract parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contract_fields import (
    config_error,
    coverage_settings,
    enum_value,
    line_of,
    optional_tolerance,
    required_int,
    required_str,
)
from .util import timeutil

TIME_FORMATS = frozenset({"epoch_ms", "iso8601"})
INPUT_FORMATS = frozenset({"csv", "jsonl", "auto"})
MISSING_POLICIES = frozenset({"strict", "allow_k", "ignore"})
DUPLICATE_POLICIES = frozenset({"report", "ignore"})

REQUIRED_FIELDS = (
    "stream_id",
    "source",
    "time_field",
    "time_format",
    "group_by",
    "nominal_period_ms",
    "jitter_tolerance_ms",
    "gap_tolerance_ms",
    "missing_policy",
    "disorder_window",
    "duplicate_policy",
)
OPTIONAL_FIELDS = (
    "format",
    "sequence_field",
    "missing_allow_k",
    "drift_tolerance_ms",
    "period_drift_tolerance_ms",
    "edge_tolerance_ms",
    "coverage_mode",
    "coverage_window_start_ms",
    "coverage_window_end_ms",
)
KNOWN_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)


@dataclass(frozen=True)
class Contract:
    """A fully validated declaration for one periodic collection stream."""

    stream_id: str
    source: Path
    display_source: str
    base_dir: Path
    input_format: str
    time_field: str
    time_format: str
    sequence_field: str | None
    group_by: tuple[str, ...]
    nominal_period_ms: int
    jitter_tolerance_ms: int
    gap_tolerance_ms: int
    missing_policy: str
    missing_allow_k: int
    disorder_window: int
    duplicate_policy: str
    drift_tolerance_ms: int
    period_drift_tolerance_ms: int
    edge_tolerance_ms: int
    coverage_mode: str
    coverage_window_start_ms: int | None
    coverage_window_end_ms: int | None

    def display_path(self, path: Path) -> str:
        return display_path(path, self.base_dir)


def display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _error(
    code: str, message: str, config_path: str | Path, line: int | None
) -> Any:
    return config_error(code, message, config_path, line)


def build_contract(
    raw: object,
    *,
    config_path: str | Path,
    base_dir: Path,
    field_lines: Mapping[str, int],
    entry_line: int | None,
) -> Contract:
    """Validate one mapping from the ``streams`` sequence."""

    if not isinstance(raw, Mapping):
        raise _error(
            "CFG_BAD_TYPE", "each stream must be a mapping", config_path, entry_line
        )
    unknown = sorted(str(key) for key in set(raw) - KNOWN_FIELDS)
    if unknown:
        field = str(unknown[0])
        raise _error(
            "CFG_UNKNOWN_FIELD", f"unknown stream field {field!r}", config_path,
            line_of(field_lines, field, entry_line),
        )
    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise _error(
                "CFG_MISSING_FIELD", f"missing required field {field!r}",
                config_path, entry_line,
            )

    common = {"config_path": config_path, "lines": field_lines, "fallback": entry_line}
    stream_id = required_str(raw, "stream_id", **common)
    source_text = required_str(raw, "source", **common)
    source = Path(source_text)
    if not source.is_absolute():
        source = base_dir / source
    source = source.resolve()
    if not source.exists():
        raise _error(
            "CFG_SOURCE_NOT_FOUND", f"source does not exist: {source_text}",
            config_path, line_of(field_lines, "source", entry_line),
        )
    if not (source.is_file() or source.is_dir()):
        raise _error(
            "CFG_BAD_PATH", f"source is not a file or directory: {source_text}",
            config_path, line_of(field_lines, "source", entry_line),
        )

    time_field = required_str(raw, "time_field", **common)
    time_format = required_str(raw, "time_format", **common)
    if not timeutil.is_time_format(time_format):
        raise _error(
            "CFG_BAD_TIME_FORMAT",
            "time_format must be 'epoch_ms' or 'iso8601'",
            config_path,
            line_of(field_lines, "time_format", entry_line),
        )

    group_raw = raw.get("group_by")
    if not isinstance(group_raw, list) or not group_raw:
        raise _error(
            "CFG_BAD_GROUP_BY",
            "group_by must be a non-empty list of field names",
            config_path,
            line_of(field_lines, "group_by", entry_line),
        )
    group_by: list[str] = []
    for value in group_raw:
        if not isinstance(value, str) or not value.strip():
            raise _error(
                "CFG_BAD_GROUP_BY", "every group_by entry must be a non-empty string",
                config_path, line_of(field_lines, "group_by", entry_line),
            )
        group_by.append(value.strip())
    if len(set(group_by)) != len(group_by):
        raise _error(
            "CFG_BAD_GROUP_BY", "group_by entries must be unique", config_path,
            line_of(field_lines, "group_by", entry_line),
        )
    if time_field in group_by:
        raise _error(
            "CFG_BAD_GROUP_BY", "time_field cannot also be a group_by field",
            config_path, line_of(field_lines, "group_by", entry_line),
        )

    sequence_field: str | None = None
    if "sequence_field" in raw and raw["sequence_field"] is not None:
        value = raw["sequence_field"]
        if not isinstance(value, str) or not value.strip():
            raise _error(
                "CFG_BAD_TYPE", "sequence_field must be a non-empty string or null",
                config_path, line_of(field_lines, "sequence_field", entry_line),
            )
        sequence_field = value.strip()
    if sequence_field is not None and (
        sequence_field in group_by or sequence_field == time_field
    ):
        raise _error(
            "CFG_BAD_GROUP_BY",
            "time_field, sequence_field and group_by must name distinct columns",
            config_path,
            line_of(field_lines, "sequence_field", entry_line),
        )

    input_format = str(raw.get("format", "auto")).strip().lower()
    if input_format not in INPUT_FORMATS:
        raise _error(
            "CFG_BAD_ENUM", "format must be one of: auto, csv, jsonl", config_path,
            line_of(field_lines, "format", entry_line),
        )
    if input_format == "auto" and source.is_file() and source.suffix.lower() not in {
        ".csv", ".jsonl"
    }:
        raise _error(
            "CFG_BAD_ENUM", "auto format can only infer .csv or .jsonl files",
            config_path, line_of(field_lines, "source", entry_line),
        )

    period = required_int(
        raw, "nominal_period_ms", minimum=1, config_path=config_path,
        lines=field_lines, fallback=entry_line, allow_zero=False,
    )
    jitter = required_int(
        raw, "jitter_tolerance_ms", minimum=0, config_path=config_path,
        lines=field_lines, fallback=entry_line, allow_zero=True,
    )
    gap = required_int(
        raw, "gap_tolerance_ms", minimum=0, config_path=config_path,
        lines=field_lines, fallback=entry_line, allow_zero=True,
    )
    missing_policy = enum_value(
        raw, "missing_policy", MISSING_POLICIES, **common
    )
    duplicate_policy = enum_value(
        raw, "duplicate_policy", DUPLICATE_POLICIES, **common
    )
    disorder_window = required_int(
        raw, "disorder_window", minimum=0, config_path=config_path,
        lines=field_lines, fallback=entry_line, allow_zero=True,
    )
    allow_k = 0
    if "missing_allow_k" in raw:
        value = raw["missing_allow_k"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _error(
                "CFG_BAD_TYPE", "missing_allow_k must be a non-negative integer",
                config_path, line_of(field_lines, "missing_allow_k", entry_line),
            )
        allow_k = value
    drift = optional_tolerance(
        raw, "drift_tolerance_ms", jitter, config_path, field_lines, entry_line
    )
    period_drift = optional_tolerance(
        raw, "period_drift_tolerance_ms", jitter, config_path, field_lines, entry_line
    )
    edge = optional_tolerance(
        raw, "edge_tolerance_ms", jitter, config_path, field_lines, entry_line
    )
    coverage_mode, coverage_start, coverage_end = coverage_settings(
        raw, config_path=config_path, lines=field_lines, fallback=entry_line
    )
    return Contract(
        stream_id=stream_id,
        source=source,
        display_source=display_path(source, base_dir),
        base_dir=base_dir,
        input_format=input_format,
        time_field=time_field,
        time_format=time_format,
        sequence_field=sequence_field,
        group_by=tuple(group_by),
        nominal_period_ms=period,
        jitter_tolerance_ms=jitter,
        gap_tolerance_ms=gap,
        missing_policy=missing_policy,
        missing_allow_k=allow_k,
        disorder_window=disorder_window,
        duplicate_policy=duplicate_policy,
        drift_tolerance_ms=drift,
        period_drift_tolerance_ms=period_drift,
        edge_tolerance_ms=edge,
        coverage_mode=coverage_mode,
        coverage_window_start_ms=coverage_start,
        coverage_window_end_ms=coverage_end,
    )
