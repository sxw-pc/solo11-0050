"""Low-level field validators for sampling contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import AppError


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
