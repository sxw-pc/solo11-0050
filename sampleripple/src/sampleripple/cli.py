"""Command-line entry points for verify, golden, serve, and runs."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import AppConfig, load_config
from .engine import run_verification
from .errors import AppError
from .golden import run_golden
from .ledger import list_runs, record_run
from .loaders import discover_files
from .report_json import build_report
from .report_markdown import render_markdown
from .server import create_server
from .util.hashing import sha256_bytes, sha256_file
from .util.jsonutil import write_json, write_text
from .util.timeutil import iso_from_ms, utc_now_ms

EXIT_OK = 0
EXIT_GOLDEN_FAIL = 1
EXIT_USAGE_OR_INPUT = 2
EXIT_IO = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sampleripple",
        description="Offline audit of periodic sampling timelines.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="verify configured sample streams")
    verify.add_argument("--config", required=True, help="path to YAML configuration")
    verify.add_argument("--out", default="out", help="output directory (default: out)")

    golden = subparsers.add_parser("golden", help="compare output to a reviewed baseline")
    golden.add_argument("--config", required=True, help="path to YAML configuration")
    golden.add_argument("--baseline", required=True, help="baseline report JSON")
    golden.add_argument(
        "--diff-out", default="out/golden_diff.md", help="Markdown diff output path"
    )

    serve = subparsers.add_parser("serve", help="serve the ledger over loopback HTTP")
    serve.add_argument("--config", required=True, help="path to YAML configuration")

    runs = subparsers.add_parser("runs", help="list historical verification runs")
    runs.add_argument("--config", required=True, help="path to YAML configuration")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--offset", type=int, default=0)
    runs.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    return parser


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _preflight_inputs(config: AppConfig) -> dict[Path, str]:
    digests: dict[Path, str] = {}
    for contract in config.streams:
        for path in discover_files(contract):
            if path not in digests:
                digests[path] = sha256_file(path)
    return digests


def _input_records(config: AppConfig, digests: dict[Path, str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path, digest in sorted(
        digests.items(), key=lambda item: config.streams[0].display_path(item[0])
    ):
        display = config.streams[0].display_path(path)
        records.append(
            {
                "bytes": path.stat().st_size,
                "path": display,
                "sha256": digest,
            }
        )
    return records


def command_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    before = _preflight_inputs(config)
    started_at = iso_from_ms(utc_now_ms())
    verification = run_verification(config)
    after_paths = {
        path
        for contract in config.streams
        for path in discover_files(contract)
    }
    if after_paths != set(before):
        raise AppError(
            "INPUT_MUTATED", "source file set changed during verification",
        )
    after = {
        path: sha256_file(path)
        for path in sorted(before, key=lambda item: item.as_posix())
    }
    for path, digest in before.items():
        if after.get(path) != digest:
            raise AppError(
                "INPUT_MUTATED", "source sample changed during verification",
                path=config.streams[0].display_path(path),
            )
    report = build_report(verification)
    out_dir = Path(args.out)
    report_path = out_dir / "report.json"
    markdown_path = out_dir / "report.md"
    meta_path = out_dir / "run_meta.json"
    payload = write_json(report_path, report)
    write_text(markdown_path, render_markdown(report))
    report_sha = sha256_bytes(payload)
    config_path = _display_path(config.config_path)
    run_id = record_run(
        config.ledger.db_path,
        started_at=started_at,
        config_hash=config.config_hash,
        config_path=config_path,
        report_sha256=report_sha,
        report=report,
        anomalies=verification.anomalies,
    )
    meta = {
        "config_hash": config.config_hash,
        "config_path": config_path,
        "input_files": _input_records(config, after),
        "outputs": {
            "report_json": _display_path(report_path),
            "report_markdown": _display_path(markdown_path),
        },
        "report_sha256": report_sha,
        "run_id": run_id,
        "started_at": started_at,
        "tool": {"name": "sampleripple", "version": __version__},
    }
    write_json(meta_path, meta)
    summary = report["summary"]
    print(
        "verify OK "
        f"streams={summary['stream_count']} groups={summary['group_count']} "
        f"points={summary['actual_points']} anomalies={summary['anomaly_count']} "
        f"run_id={run_id} report={_display_path(report_path)}"
    )
    return EXIT_OK


def command_golden(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = run_golden(config, args.baseline, args.diff_out)
    if result.passed:
        print(f"golden PASS differences=0 diff={_display_path(result.diff_path)}")
        return EXIT_OK
    print(
        f"golden FAIL differences={len(result.differences)} "
        f"diff={_display_path(result.diff_path)}"
    )
    return EXIT_GOLDEN_FAIL


def command_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    server = create_server(config)
    host, port = server.server_address[:2]
    if ":" in str(host):
        print(f"serving http://[{host}]:{port} (loopback only)", flush=True)
    else:
        print(f"serving http://{host}:{port} (loopback only)", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def command_runs(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    runs, total = list_runs(
        config.ledger.db_path, limit=max(1, args.limit), offset=max(0, args.offset)
    )
    if args.json:
        print(
            json.dumps(
                {"limit": args.limit, "offset": args.offset, "runs": runs, "total": total},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_OK
    if not runs:
        print("No runs recorded.")
        return EXIT_OK
    print("run_id  started_at                streams groups points anomalies")
    for run in runs:
        print(
            f"{run['run_id']:<7} {run['started_at']:<23} "
            f"{run['stream_count']:<7} {run['group_count']:<6} "
            f"{run['actual_points']:<6} {run['anomaly_count']}"
        )
    print(f"total={total}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    commands = {
        "verify": command_verify,
        "golden": command_golden,
        "serve": command_serve,
        "runs": command_runs,
    }
    try:
        return commands[args.command](args)
    except AppError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_INPUT
    except (OSError, sqlite3.Error) as exc:
        print(f"ERROR IO {exc}", file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    raise SystemExit(main())
