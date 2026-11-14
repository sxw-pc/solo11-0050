#!/usr/bin/env python3
"""Check the per-file and total line-count budgets for Python code."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_ROOTS = ("src", "tests", "scripts")
EXCLUDED = {"__pycache__", ".pytest_cache", "out", "var", ".venv"}


def python_files() -> list[Path]:
    files: list[Path] = []
    for name in CODE_ROOTS:
        root = ROOT / name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in EXCLUDED for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-file", type=int, default=300)
    parser.add_argument("--min-total", type=int, default=2500)
    parser.add_argument("--max-total", type=int, default=4500)
    args = parser.parse_args(argv)
    files = python_files()
    counts = [(path, count_lines(path)) for path in files]
    total = sum(count for _, count in counts)
    oversized = [(path, count) for path, count in counts if count > args.max_file]
    for path, count in counts:
        marker = " !! over limit" if count > args.max_file else ""
        print(f"{count:5d}  {path.relative_to(ROOT).as_posix()}{marker}")
    print()
    print(f"python_files={len(counts)} total_lines={total}")
    print(f"per_file_max={args.max_file} total_budget={args.min_total}..{args.max_total}")
    failures = 0
    if oversized:
        failures += len(oversized)
        print("FAIL: files exceed the per-file line budget")
    if not args.min_total <= total <= args.max_total:
        failures += 1
        print("FAIL: total Python line count is outside the budget")
    if failures:
        return 1
    print("OK: line budgets satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
