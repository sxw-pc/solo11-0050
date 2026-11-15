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


def _boundary_text(boundary: dict[str, Any] | None) -> str:
    if boundary is None:
        return "never"
    sign = "+" if boundary["direction"] == "increase" else "-"
    return f"{sign}{boundary['delta']} (={boundary['adjusted_value']})"


def _sensitivity_sections(report: dict[str, Any]) -> list[str]:
    certificate = report.get("sensitivity_certificate")
    if certificate is None:
        return []
    lines = ["", "## 敏感度证书", ""]
    lines.append(
        "证书逐分组逐规则给出整数容差翻转边界：`stable_delta` 为结论保持不变的"
        "最大调整幅度，`decrease`/`increase` 为发生翻转的最小整数调整量"
        "（括号内为调整后的参数值），`never` 表示该方向在结构上不可翻转。"
    )
    lines.append("")
    aggregate_rows = [
        [rule_id, count]
        for rule_id, count in certificate["rule_aggregate"].items()
    ]
    lines.append("### 按规则聚合（可翻转条目数）")
    lines.append("")
    lines.extend(_table(["rule_id", "可翻转分组数"], aggregate_rows))
    rows = []
    for group in certificate["groups"]:
        for rule in group["rules"]:
            if not rule["flippable"]:
                continue
            rows.append(
                [
                    group["stream_id"],
                    group["group_key"],
                    rule["rule_id"],
                    rule["category"],
                    rule["parameter"],
                    rule["current_value"],
                    "是" if rule["triggered"] else "否",
                    rule["stable_delta"],
                    _boundary_text(rule["decrease"]),
                    _boundary_text(rule["increase"]),
                ]
            )
    lines.extend(["", "### 翻转边界（推荐审查阈值）", ""])
    lines.extend(
        _table(
            [
                "stream", "group", "rule_id", "类别", "参数", "当前值",
                "当前触发", "稳定幅度", "下调翻转", "上调翻转",
            ],
            rows,
        )
    )
    non_flippable = [
        (group["stream_id"], group["group_key"], rule["rule_id"], rule["reason"])
        for group in certificate["groups"]
        for rule in group["rules"]
        if not rule["flippable"]
    ]
    if non_flippable:
        lines.extend(["", "### 不可翻转条目", ""])
        lines.extend(
            _table(
                ["stream", "group", "rule_id", "原因"],
                non_flippable,
            )
        )
    return lines


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
    lines.extend(_sensitivity_sections(report))
    lines.extend(["", "---", "", "本报告完全由本地确定性规则生成，不含运行时钟。", ""])
    return "\n".join(lines)
