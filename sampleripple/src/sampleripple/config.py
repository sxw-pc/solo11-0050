"""YAML configuration loading with stable error codes and best-effort lines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from .contract_fields import config_error
from .contracts import Contract, build_contract, display_path
from .errors import AppError
from .util.hashing import sha256_bytes

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class LedgerConfig:
    db_path: Path
    display_path: str


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    config_hash: str
    base_dir: Path
    streams: tuple[Contract, ...]
    ledger: LedgerConfig
    server: ServerConfig


def _index_lines(node: Node, prefix: str, out: dict[str, int]) -> None:
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            key = str(key_node.value)
            path = f"{prefix}.{key}" if prefix else key
            out[path] = key_node.start_mark.line + 1
            _index_lines(value_node, path, out)
    elif isinstance(node, SequenceNode):
        for index, item in enumerate(node.value):
            path = f"{prefix}.{index}"
            out[path] = item.start_mark.line + 1
            _index_lines(item, path, out)


def _line(lines: Mapping[str, int], path: str) -> int | None:
    return lines.get(path)


def _mapping(value: Any, name: str, path: Path, line: int | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise config_error("CFG_BAD_TYPE", f"{name} must be a mapping", path, line)
    return value


def _check_loopback(host: str) -> str:
    normalized = host.strip().lower()
    if normalized not in LOOPBACK_HOSTS:
        raise ValueError("host must be loopback: 127.0.0.1, localhost, or ::1")
    return normalized


def _server_config(
    raw: object, lines: Mapping[str, int], config_path: Path
) -> ServerConfig:
    data = _mapping(raw, "server", config_path, _line(lines, "server"))
    unknown = sorted(set(data) - {"host", "port"})
    if unknown:
        raise config_error(
            "CFG_UNKNOWN_FIELD", f"unknown server field {unknown[0]!r}",
            config_path, _line(lines, f"server.{unknown[0]}"),
        )
    host = data.get("host", "127.0.0.1")
    if not isinstance(host, str):
        raise config_error("CFG_BAD_HOST", "server.host must be a string",
                           config_path, _line(lines, "server.host"))
    try:
        host = _check_loopback(host)
    except ValueError as exc:
        raise config_error("CFG_BAD_HOST", str(exc), config_path,
                           _line(lines, "server.host")) from exc
    port = data.get("port", 8765)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise config_error("CFG_BAD_PORT", "server.port must be 1..65535",
                           config_path, _line(lines, "server.port"))
    return ServerConfig(host=host, port=port)


def _ledger_config(
    raw: object, lines: Mapping[str, int], config_path: Path, base_dir: Path
) -> LedgerConfig:
    data = _mapping(raw, "ledger", config_path, _line(lines, "ledger"))
    unknown = sorted(set(data) - {"db_path"})
    if unknown:
        raise config_error(
            "CFG_UNKNOWN_FIELD", f"unknown ledger field {unknown[0]!r}",
            config_path, _line(lines, f"ledger.{unknown[0]}"),
        )
    text = data.get("db_path", "var/ledger.db")
    if not isinstance(text, str) or not text.strip():
        raise config_error("CFG_BAD_TYPE", "ledger.db_path must be a non-empty string",
                           config_path, _line(lines, "ledger.db_path"))
    path = Path(text.strip())
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if path.exists() and path.is_dir():
        raise config_error("CFG_BAD_PATH", "ledger.db_path points to a directory",
                           config_path, _line(lines, "ledger.db_path"))
    return LedgerConfig(db_path=path, display_path=display_path(path, base_dir))


def load_config(path: str | Path) -> AppConfig:
    """Load and fully validate a configuration file."""

    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise AppError("CFG_FILE_NOT_FOUND", "configuration file not found",
                       path=config_path)
    raw_bytes = config_path.read_bytes()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("CFG_ENCODING", "configuration must be UTF-8",
                       path=config_path) from exc
    try:
        loaded = yaml.safe_load(text)
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise AppError(
            "CFG_YAML_SYNTAX", f"invalid YAML: {getattr(exc, 'problem', exc)}",
            path=config_path, line=(mark.line + 1) if mark else None,
        ) from exc
    if not isinstance(loaded, Mapping):
        raise AppError("CFG_NOT_MAPPING", "configuration root must be a mapping",
                       path=config_path)
    lines: dict[str, int] = {}
    if root is not None:
        _index_lines(root, "", lines)
    unknown = sorted(
        str(key)
        for key in set(loaded) - {"version", "base_dir", "ledger", "server", "streams"}
    )
    if unknown:
        raise config_error(
            "CFG_UNKNOWN_FIELD", f"unknown top-level field {unknown[0]!r}",
            config_path, _line(lines, unknown[0]),
        )
    if "version" not in loaded:
        raise AppError("CFG_MISSING_FIELD", "missing top-level field 'version'",
                       path=config_path)
    version = loaded["version"]
    if isinstance(version, bool) or version != 1:
        raise config_error("CFG_BAD_VERSION", "version must be 1", config_path,
                           _line(lines, "version"))

    base_raw = loaded.get("base_dir", ".")
    if not isinstance(base_raw, str) or not base_raw.strip():
        raise config_error("CFG_BAD_TYPE", "base_dir must be a non-empty string",
                           config_path, _line(lines, "base_dir"))
    base_dir = Path(base_raw.strip())
    if not base_dir.is_absolute():
        base_dir = config_path.parent / base_dir
    base_dir = base_dir.resolve()
    if not base_dir.is_dir():
        raise config_error("CFG_BAD_PATH", "base_dir is not a directory",
                           config_path, _line(lines, "base_dir"))

    ledger_raw = loaded.get("ledger", {"db_path": "var/ledger.db"})
    ledger = _ledger_config(ledger_raw, lines, config_path, base_dir)
    server_raw = loaded.get("server", {})
    server = _server_config(server_raw, lines, config_path)

    streams_raw = loaded.get("streams")
    if not isinstance(streams_raw, list) or not streams_raw:
        raise config_error("CFG_EMPTY_STREAMS", "streams must be a non-empty list",
                           config_path, _line(lines, "streams"))
    contracts: list[Contract] = []
    seen: set[str] = set()
    for index, item in enumerate(streams_raw):
        prefix = f"streams.{index}"
        field_lines: dict[str, int] = {}
        if isinstance(item, Mapping):
            for key in item:
                found = _line(lines, f"{prefix}.{key}")
                if found is not None:
                    field_lines[str(key)] = found
        contract = build_contract(
            item,
            config_path=config_path,
            base_dir=base_dir,
            field_lines=field_lines,
            entry_line=_line(lines, prefix),
        )
        if contract.stream_id in seen:
            raise config_error(
                "CFG_DUPLICATE_STREAM",
                f"duplicate stream_id {contract.stream_id!r}",
                config_path,
                _line(lines, prefix),
            )
        seen.add(contract.stream_id)
        contracts.append(contract)
    return AppConfig(
        config_path=config_path,
        config_hash=sha256_bytes(raw_bytes),
        base_dir=base_dir,
        streams=tuple(contracts),
        ledger=ledger,
        server=server,
    )
