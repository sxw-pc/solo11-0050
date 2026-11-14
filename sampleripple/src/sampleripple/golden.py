"""Structured golden-baseline comparison with fixed float tolerance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .engine import run_verification
from .errors import AppError
from .report_json import build_report
from .util.jsonutil import read_json, write_text

DEFAULT_TOLERANCE_MS = 1e-3
IGNORED_KEYS = frozenset({"run_meta", "started_at", "generated_at"})


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str
    baseline: object
    current: object


@dataclass(frozen=True)
class GoldenResult:
    passed: bool
    differences: tuple[Difference, ...]
    diff_path: Path
    compared_fields: int


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _short(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > 180:
        text = text[:177] + "..."
    return text


def compare_values(
    baseline: object,
    current: object,
    *,
    tolerance: float = DEFAULT_TOLERANCE_MS,
    path: str = "$",
    differences: list[Difference] | None = None,
) -> list[Difference]:
    """Recursively compare two JSON graphs."""

    found = differences if differences is not None else []
    if isinstance(baseline, dict) and isinstance(current, dict):
        baseline_keys = set(baseline) - IGNORED_KEYS
        current_keys = set(current) - IGNORED_KEYS
        for key in sorted(baseline_keys - current_keys):
            found.append(Difference(f"{path}.{key}", "missing_current", baseline[key], None))
        for key in sorted(current_keys - baseline_keys):
            found.append(Difference(f"{path}.{key}", "extra_current", None, current[key]))
        for key in sorted(baseline_keys & current_keys):
            compare_values(
                baseline[key], current[key], tolerance=tolerance,
                path=f"{path}.{key}", differences=found,
            )
        return found
    if isinstance(baseline, list) and isinstance(current, list):
        common = min(len(baseline), len(current))
        for index in range(common):
            compare_values(
                baseline[index], current[index], tolerance=tolerance,
                path=f"{path}[{index}]", differences=found,
            )
        for index in range(common, len(baseline)):
            found.append(
                Difference(f"{path}[{index}]", "missing_current", baseline[index], None)
            )
        for index in range(common, len(current)):
            found.append(
                Difference(f"{path}[{index}]", "extra_current", None, current[index])
            )
        return found
    if _is_number(baseline) and _is_number(current):
        if abs(float(baseline) - float(current)) > tolerance:
            found.append(Difference(path, "value", baseline, current))
        return found
    if type(baseline) is not type(current):
        found.append(Difference(path, "type", baseline, current))
    elif baseline != current:
        found.append(Difference(path, "value", baseline, current))
    return found


def render_diff(
    result: GoldenResult, *, baseline_path: Path, max_rows: int = 200
) -> str:
    lines = [
        "# SampleRipple Golden Diff",
        "",
        f"- status: **{'PASS' if result.passed else 'FAIL'}**",
        f"- baseline: `{baseline_path.as_posix()}`",
        f"- compared_fields: {result.compared_fields}",
        f"- difference_count: {len(result.differences)}",
        f"- tolerance_ms: {DEFAULT_TOLERANCE_MS:.3f}",
        "",
    ]
    if result.passed:
        lines.append("No structural differences.")
    else:
        lines.extend(
            [
                "| Path | Kind | Baseline | Current |",
                "| --- | --- | --- | --- |",
            ]
        )
        for difference in result.differences[:max_rows]:
            lines.append(
                "| {path} | {kind} | {baseline} | {current} |".format(
                    path=difference.path.replace("|", "\\|"),
                    kind=difference.kind,
                    baseline=_short(difference.baseline).replace("|", "\\|"),
                    current=_short(difference.current).replace("|", "\\|"),
                )
            )
        if len(result.differences) > max_rows:
            lines.extend(
                ["", f"> Showing first {max_rows} of "
                 f"{len(result.differences)} differences."]
            )
    lines.append("")
    return "\n".join(lines)


def run_golden(
    config: AppConfig,
    baseline_path: str | Path,
    diff_path: str | Path,
    *,
    tolerance: float = DEFAULT_TOLERANCE_MS,
) -> GoldenResult:
    """Run verification in memory and compare it to a reviewed baseline."""

    baseline_file = Path(baseline_path)
    if not baseline_file.is_file():
        raise AppError("GOLDEN_MISSING", "golden baseline not found", path=baseline_file)
    try:
        baseline = read_json(baseline_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(
            "GOLDEN_BAD_JSON", f"cannot read golden baseline: {exc}",
            path=baseline_file,
        ) from exc
    current = build_report(run_verification(config))
    differences = compare_values(baseline, current, tolerance=tolerance)
    result = GoldenResult(
        passed=not differences,
        differences=tuple(differences),
        diff_path=Path(diff_path),
        compared_fields=_count_fields(current),
    )
    write_text(Path(diff_path), render_diff(result, baseline_path=baseline_file))
    return result


def _count_fields(value: object) -> int:
    if isinstance(value, dict):
        return sum(1 + _count_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_fields(item) for item in value)
    return 1
