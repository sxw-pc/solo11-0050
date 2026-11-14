"""Read-only CSV and JSONL sample loaders."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import Contract
from .errors import AppError
from .util.timeutil import parse_time_ms


@dataclass(frozen=True)
class Point:
    group: tuple[str, ...]
    group_key: str
    ts_ms: int
    sequence: str | None
    ordinal: int
    source_file: str
    line_no: int


@dataclass(frozen=True)
class LoadedSample:
    files: tuple[Path, ...]
    points: tuple[Point, ...]


def _format_for(path: Path, declared: str) -> str:
    if declared != "auto":
        return declared
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".jsonl":
        return "jsonl"
    raise AppError(
        "LOAD_UNKNOWN_FORMAT", "cannot infer format from file extension",
        path=path.as_posix(),
    )


def discover_files(contract: Contract) -> tuple[Path, ...]:
    """Return the sorted, read-only input file set for a contract."""

    source = contract.source
    if source.is_file():
        return (source,)
    wanted = {".csv", ".jsonl"}
    if contract.input_format == "csv":
        wanted = {".csv"}
    elif contract.input_format == "jsonl":
        wanted = {".jsonl"}
    files = [
        path for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in wanted
    ]
    files.sort(
        key=lambda path: (
            contract.display_path(path).lower(), contract.display_path(path)
        )
    )
    if not files:
        raise AppError(
            "LOAD_NO_FILES", "source directory contains no matching samples",
            path=contract.display_source,
        )
    return tuple(files)


def _text_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("must be a string or integer")
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, int):
        text = str(value)
    else:
        raise ValueError("must be a string or integer")
    if not text:
        raise ValueError("must not be empty")
    if "\x00" in text:
        raise ValueError("must not contain NUL")
    return text


def _group_values(
    values: Iterable[Any], path: str, line: int, field: str
) -> tuple[str, ...]:
    parts: list[str] = []
    for value in values:
        try:
            parts.append(_text_value(value))
        except ValueError as exc:
            raise AppError(
                "LOAD_BAD_GROUP", f"{field}: {exc}", path=path, line=line
            ) from exc
    return tuple(parts)


def _time_value(value: Any, contract: Contract, path: str, line: int) -> int:
    try:
        return parse_time_ms(value, contract.time_format)
    except ValueError as exc:
        raise AppError(
            "LOAD_BAD_TIME", f"{contract.time_field}: {exc}", path=path, line=line
        ) from exc


def _sequence_value(
    value: Any, contract: Contract, path: str, line: int
) -> str | None:
    if contract.sequence_field is None:
        return None
    if value is None or (isinstance(value, str) and not value.strip()):
        raise AppError(
            "LOAD_BAD_SEQUENCE", f"{contract.sequence_field} is empty",
            path=path, line=line,
        )
    try:
        return _text_value(value)
    except ValueError as exc:
        raise AppError(
            "LOAD_BAD_SEQUENCE", f"{contract.sequence_field}: {exc}",
            path=path, line=line,
        ) from exc


def _make_point(
    contract: Contract,
    raw_values: dict[str, Any],
    *,
    path: str,
    line: int,
    ordinal: int,
) -> Point:
    required = list(contract.group_by) + [contract.time_field]
    if contract.sequence_field is not None:
        required.append(contract.sequence_field)
    for field in required:
        if field not in raw_values or raw_values[field] is None:
            raise AppError(
                "LOAD_MISSING_FIELD", f"missing field {field!r}", path=path, line=line
            )
    group = _group_values(
        (raw_values[field] for field in contract.group_by),
        path, line, "group_by",
    )
    key = "/".join(group)
    ts_ms = _time_value(raw_values[contract.time_field], contract, path, line)
    sequence = _sequence_value(
        raw_values.get(contract.sequence_field)
        if contract.sequence_field is not None else None,
        contract, path, line,
    )
    return Point(
        group=group,
        group_key=key,
        ts_ms=ts_ms,
        sequence=sequence,
        ordinal=ordinal,
        source_file=path,
        line_no=line,
    )


def _load_csv(
    path: Path, contract: Contract, start_ordinal: int
) -> tuple[list[Point], int]:
    display = contract.display_path(path)
    points: list[Point] = []
    ordinal = start_ordinal
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise AppError("LOAD_IO", f"cannot open CSV: {exc}", path=display) from exc
    with handle:
        try:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            if not headers:
                raise AppError("LOAD_BAD_HEADER", "CSV header is missing", path=display)
            if any(not str(name).strip() for name in headers):
                raise AppError(
                    "LOAD_BAD_HEADER", "CSV contains an empty column name",
                    path=display, line=1,
                )
            if len(set(headers)) != len(headers):
                raise AppError(
                    "LOAD_BAD_HEADER", "CSV contains duplicate column names",
                    path=display, line=1,
                )
            required = set(contract.group_by) | {contract.time_field}
            if contract.sequence_field is not None:
                required.add(contract.sequence_field)
            missing = sorted(required - set(headers))
            if missing:
                raise AppError(
                    "LOAD_MISSING_COLUMN",
                    f"CSV header is missing columns: {', '.join(missing)}",
                    path=display, line=1,
                )
            for row in reader:
                line_no = reader.line_num
                if row is None:
                    continue
                if None in row:
                    raise AppError(
                        "LOAD_BAD_ROW", "CSV row has more values than the header",
                        path=display, line=line_no,
                    )
                if all(
                    value is None or not str(value).strip()
                    for value in row.values()
                ):
                    continue
                point = _make_point(
                    contract, row, path=display, line=line_no, ordinal=ordinal
                )
                points.append(point)
                ordinal += 1
        except csv.Error as exc:
            raise AppError(
                "LOAD_BAD_CSV", f"CSV parse error: {exc}", path=display
            ) from exc
        except UnicodeDecodeError as exc:
            raise AppError(
                "LOAD_ENCODING", "CSV must be UTF-8 or UTF-8-SIG", path=display
            ) from exc
    return points, ordinal


def _load_jsonl(
    path: Path, contract: Contract, start_ordinal: int
) -> tuple[list[Point], int]:
    display = contract.display_path(path)
    points: list[Point] = []
    ordinal = start_ordinal
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise AppError("LOAD_IO", f"cannot open JSONL: {exc}", path=display) from exc
    with handle:
        try:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AppError(
                        "LOAD_BAD_JSON", f"invalid JSON: {exc.msg}",
                        path=display, line=line_no,
                    ) from exc
                if not isinstance(record, dict):
                    raise AppError(
                        "LOAD_BAD_JSON", "JSONL record must be an object",
                        path=display, line=line_no,
                    )
                point = _make_point(
                    contract, record, path=display, line=line_no, ordinal=ordinal
                )
                points.append(point)
                ordinal += 1
        except UnicodeDecodeError as exc:
            raise AppError(
                "LOAD_ENCODING", "JSONL must be UTF-8 or UTF-8-SIG", path=display
            ) from exc
    return points, ordinal


def load_stream(contract: Contract) -> LoadedSample:
    """Load every point in a stream, preserving deterministic file/row order."""

    files = discover_files(contract)
    all_points: list[Point] = []
    ordinal = 0
    for path in files:
        file_format = _format_for(path, contract.input_format)
        if file_format == "csv":
            points, ordinal = _load_csv(path, contract, ordinal)
        elif file_format == "jsonl":
            points, ordinal = _load_jsonl(path, contract, ordinal)
        else:
            raise AppError(
                "LOAD_UNKNOWN_FORMAT", f"unsupported format {file_format!r}",
                path=contract.display_path(path),
            )
        all_points.extend(points)
    if not all_points:
        raise AppError(
            "LOAD_EMPTY", "stream contains no sample rows", path=contract.display_source
        )
    return LoadedSample(files=files, points=tuple(all_points))
