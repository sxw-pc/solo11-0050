"""Deterministic human-readable Markdown report."""

from __future__ import annotations

from typing import Any, Iterable

TOP_N = 20


def _cell(value: object) -> str:
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text


def _table(headers: list[str], rows: Iterable[Iterable[object]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return lines


def _all_anomalies(report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for stream in report["streams"]:
        items.extend(stream["anomalies"])
    return items


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# SampleRipple 采样时间轴核验报告",
        "",
        f"- 工具版本：{report['tool']['name']} {report['tool']['version']}",
        f"- 配置哈希：`{report['config_hash']}`",
        f"- 总体状态：**{summary['status']}**",
        f"- 流 / 分组：{summary['stream_count']} / {summary['group_count']}",
        f"- 实际点 / 唯一点 / 预期点：{summary['actual_points']} / "
        f"{summary['unique_points']} / {summary['expected_points']}",
        f"- 异常总数：{summary['anomaly_count']}",
        "",
        "## 异常类别汇总",
        "",
    ]
    lines.extend(
        _table(
            ["类别", "数量"],
            [[key, value] for key, value in summary["by_category"].items()],
        )
    )
    lines.extend(["", "## 按规则聚合", ""])
    lines.extend(
        _table(
            ["rule_id", "数量"],
            [[key, value] for key, value in summary["by_rule"].items()],
        )
    )
    lines.extend(["", "## 分组摘要", ""])
    group_rows = []
    for stream in report["streams"]:
        for group in stream["groups"]:
            group_rows.append(
                [
                    stream["stream_id"],
                    group["group_key"],
                    group["status"],
                    group["actual_points"],
                    group["unique_points"],
                    group["expected_points"],
                    group["missing_slots"],
                    f"{group['alignment_offset_ms']:.3f}",
                    group["anomaly_count"],
                ]
            )
    lines.extend(
        _table(
            [
                "stream", "group", "状态", "实际", "唯一", "预期",
                "缺失槽", "对齐偏移(ms)", "异常",
            ],
            group_rows,
        )
    )
    anomalies = _all_anomalies(report)
    lines.extend(["", f"## Top-{TOP_N} 异常", ""])
    if not anomalies:
        lines.append("未发现异常。")
    else:
        shown = anomalies[:TOP_N]
        lines.extend(
            _table(
                [
                    "stream", "group", "index", "类别", "rule_id", "severity",
                    "expected_at", "actual_at", "delta_ms",
                ],
                [
                    [
                        item["stream_id"], item["group_key"], item["index"],
                        item["category"], item["rule_id"], item["severity"],
                        item["expected_at"], item["actual_at"], item["delta_ms"],
                    ]
                    for item in shown
                ],
            )
        )
        if len(anomalies) > TOP_N:
            lines.extend(
                ["", f"> 仅展示前 {TOP_N} 条，共 {len(anomalies)} 条；"
                 "完整证据见 report.json。"]
            )
    lines.extend(["", "---", "", "本报告完全由本地确定性规则生成，不含运行时钟。", ""])
    return "\n".join(lines)
