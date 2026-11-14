"""Timestamp parsing and formatting with a single UTC millisecond contract."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_EPOCH_RE = re.compile(r"^-?\d+$")
_TIME_FORMATS = frozenset({"epoch_ms", "iso8601"})


def parse_epoch_ms(value: object) -> int:
    """Parse an integer or canonical integer string as epoch milliseconds."""

    if isinstance(value, bool) or value is None:
        raise ValueError("epoch_ms must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _EPOCH_RE.fullmatch(text):
            return int(text)
    raise ValueError("epoch_ms must be an integer or integer string")


def parse_iso8601_ms(value: object) -> int:
    """Parse ISO-8601 into epoch milliseconds.

    Naive datetimes are interpreted as UTC.  Sub-millisecond digits are
    truncated, never rounded, so the conversion is deterministic.
    """

    if not isinstance(value, str):
        raise ValueError("iso8601 timestamp must be a string")
    text = value.strip()
    if not text:
        raise ValueError("iso8601 timestamp is empty")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid iso8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    delta = parsed - _EPOCH
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def parse_time_ms(value: object, time_format: str) -> int:
    """Parse a declared contract timestamp format."""

    if time_format == "epoch_ms":
        return parse_epoch_ms(value)
    if time_format == "iso8601":
        return parse_iso8601_ms(value)
    raise ValueError(f"unsupported time format: {time_format!r}")


def iso_from_ms(value: int) -> str:
    """Render epoch milliseconds as UTC ISO-8601 with exactly three digits."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timestamp must be an integer number of milliseconds")
    try:
        moment = _EPOCH + timedelta(milliseconds=value)
    except OverflowError as exc:
        raise ValueError("timestamp is outside the supported range") from exc
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
        f".{value % 1000:03d}Z"
    )


def is_time_format(value: object) -> bool:
    return isinstance(value, str) and value in _TIME_FORMATS


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer half-up division for non-negative numerators and denominators."""

    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator < 0:
        return -round_half_up(-numerator, denominator)
    return (2 * numerator + denominator) // (2 * denominator)


def nearest_boundary_ms(timestamp_ms: int, period_ms: int) -> int:
    """Round a timestamp to the nearest nominal epoch boundary."""

    if period_ms <= 0:
        raise ValueError("period must be positive")
    if timestamp_ms >= 0:
        steps = round_half_up(timestamp_ms, period_ms)
        return steps * period_ms
    # Keep the round-half-up rule while handling pre-epoch values.
    steps = (2 * abs(timestamp_ms) + period_ms) // (2 * period_ms)
    return -steps * period_ms


def utc_now_ms() -> int:
    """Current UTC epoch milliseconds (only for run metadata)."""

    return int(datetime.now(timezone.utc).timestamp() * 1000)
