"""Low-level field validators for sampling contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import AppError

COVERAGE_MODES = frozenset({"observed_span", "declared_window"})
WINDOW_FIELDS = ("coverage_window_start_ms", "coverage_window_end_ms")


def line_of(
    field_lines: Mapping[str, int], field: str, fallback: int | None
) -> int | None:
    return field_lines.get(field, fallback)


def config_error(
    code: str, message: str, config_path: str | Path, line: int | None
) -> AppError:
    return AppError(code, message, path=config_path, line=line)


def required_int(
    raw: Mapping[str, Any],
    field: str,
    *,
    minimum: int,
    config_path: str | Path,
    lines: Mapping[str, int],
    fallback: int | None,
    allow_zero: bool,
) -> int:
    if field not in raw:
        raise config_error(
            "CFG_MISSING_FIELD", f"missing required field {field!r}",
            config_path, fallback,
        )
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise config_error(
            "CFG_BAD_TYPE", f"{field} must be an integer", config_path,
            line_of(lines, field, fallback),
        )
    lower = minimum if allow_zero else max(1, minimum)
    if value < lower:
        code = "CFG_BAD_PERIOD" if field == "nominal_period_ms" else "CFG_BAD_TOLERANCE"
        raise config_error(
            code, f"{field} must be >= {lower}", config_path,
            line_of(lines, field, fallback),
        )
    return value


def required_str(
    raw: Mapping[str, Any],
    field: str,
    *,
    config_path: str | Path,
    lines: Mapping[str, int],
    fallback: int | None,
) -> str:
    if field not in raw:
        raise config_error(
            "CFG_MISSING_FIELD", f"missing required field {field!r}",
            config_path, fallback,
        )
    value = raw[field]
    if not isinstance(value, str) or not value.strip():
        raise config_error(
            "CFG_BAD_TYPE", f"{field} must be a non-empty string", config_path,
            line_of(lines, field, fallback),
        )
    return value.strip()


def enum_value(
    raw: Mapping[str, Any],
    field: str,
    allowed: frozenset[str],
    *,
    config_path: str | Path,
    lines: Mapping[str, int],
    fallback: int | None,
) -> str:
    value = required_str(
        raw, field, config_path=config_path, lines=lines, fallback=fallback
    )
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise config_error(
            "CFG_BAD_ENUM", f"{field} must be one of: {choices}", config_path,
            line_of(lines, field, fallback),
        )
    return value


def optional_tolerance(
    raw: Mapping[str, Any],
    field: str,
    default: int,
    config_path: str | Path,
    lines: Mapping[str, int],
    fallback: int | None,
) -> int:
    if field not in raw:
        return default
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise config_error(
            "CFG_BAD_TOLERANCE", f"{field} must be a non-negative integer",
            config_path, line_of(lines, field, fallback),
        )
    return value


def coverage_settings(
    raw: Mapping[str, Any],
    *,
    config_path: str | Path,
    lines: Mapping[str, int],
    fallback: int | None,
) -> tuple[str, int | None, int | None]:
    """Validate the coverage mode and its declared-window endpoints."""

    mode = str(raw.get("coverage_mode", "observed_span")).strip().lower()
    if mode not in COVERAGE_MODES:
        raise config_error(
            "CFG_BAD_ENUM",
            "coverage_mode must be one of: declared_window, observed_span",
            config_path, line_of(lines, "coverage_mode", fallback),
        )
    for field in WINDOW_FIELDS:
        if field in raw and (
            isinstance(raw[field], bool) or not isinstance(raw[field], int)
        ):
            raise config_error(
                "CFG_BAD_TYPE", f"{field} must be an integer",
                config_path, line_of(lines, field, fallback),
            )
    if mode == "declared_window":
        for field in WINDOW_FIELDS:
            if field not in raw:
                raise config_error(
                    "CFG_MISSING_FIELD",
                    f"{field} is required when coverage_mode is 'declared_window'",
                    config_path, line_of(lines, field, fallback),
                )
        start = raw["coverage_window_start_ms"]
        end = raw["coverage_window_end_ms"]
        if end < start:
            raise config_error(
                "CFG_BAD_TYPE",
                "coverage_window_end_ms must be >= coverage_window_start_ms",
                config_path, line_of(lines, "coverage_window_end_ms", fallback),
            )
        return mode, start, end
    present = next((field for field in WINDOW_FIELDS if field in raw), None)
    if present is not None:
        raise config_error(
            "CFG_BAD_TYPE",
            f"{present} is only allowed when coverage_mode is 'declared_window'",
            config_path, line_of(lines, present, fallback),
        )
    return mode, None, None
