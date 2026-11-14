"""SHA-256 helpers used for config, fixtures, and report provenance."""

from __future__ import annotations

import hashlib
from pathlib import Path

_PREFIX = "sha256:"


def sha256_bytes(data: bytes) -> str:
    return _PREFIX + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return _PREFIX + digest.hexdigest()


def strip_prefix(value: str) -> str:
    return value[len(_PREFIX) :] if value.startswith(_PREFIX) else value
