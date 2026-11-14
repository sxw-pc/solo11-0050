"""Canonical JSON writer.

Keys are sorted, indentation is fixed, line endings are LF, and every float is
rendered with exactly three decimals.  Two equal object graphs therefore
produce byte-identical files on every platform.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON output cannot contain NaN or infinity")
        if abs(value) < 0.0005:
            value = 0.0
        return f"{value:.3f}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _render(value: Any, level: int) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            encoded = json.dumps(key, ensure_ascii=False)
            items.append(
                " " * (level + 2) + encoded + ": " + _render(value[key], level + 2)
            )
        return "{\n" + ",\n".join(items) + "\n" + " " * level + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [" " * (level + 2) + _render(item, level + 2) for item in value]
        return "[\n" + ",\n".join(items) + "\n" + " " * level + "]"
    return _scalar(value)


def dumps(value: Any) -> str:
    """Serialize a JSON-compatible graph with the canonical form."""

    return _render(value, 0) + "\n"


def dump_bytes(value: Any) -> bytes:
    return dumps(value).encode("utf-8")


def write_json(path: str | Path, value: Any) -> bytes:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dump_bytes(value)
    with target.open("wb") as handle:
        handle.write(payload)
    return payload


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)
