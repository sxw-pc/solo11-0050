# SampleRipple 采样时间轴核验报告

- 工具版本：sampleripple 0.1.0
- 配置哈希：`sha256:4cbf225a385ff38a2e2e08196207c307798d88446cd0bf267c2f38860b49afc2`
- 总体状态：**ok**
- 流 / 分组：1 / 2
- 实际点 / 唯一点 / 预期点：12 / 12 / 12
- 异常总数：0

## 异常类别汇总

| 类别 | 数量 |
| --- | --- |
| GAP | 0 |
| DRIFT | 0 |
| DUPLICATE | 0 |
| DISORDER | 0 |
| MISSING | 0 |
| EDGE_SKEW | 0 |
| PERIOD_DRIFT | 0 |

## 按规则聚合

| rule_id | 数量 |
| --- | --- |
| GAP_ADJACENT | 0 |
| DRIFT_CUMULATIVE | 0 |
| DUPLICATE_POINT | 0 |
| DISORDER_LOCAL | 0 |
| MISSING_SLOT | 0 |
| EDGE_SKEW_HEAD | 0 |
| EDGE_SKEW_TAIL | 0 |
| PERIOD_DRIFT_FIT | 0 |

## 分组摘要

| stream | group | 状态 | 实际 | 唯一 | 预期 | 缺失槽 | 对齐偏移(ms) | 异常 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| clean-grid | site-a/line-07/sensor-x | ok | 6 | 6 | 6 | 0 | 0.000 | 0 |
| clean-grid | site-b/line-08/sensor-y | ok | 6 | 6 | 6 | 0 | 0.000 | 0 |

## Top-20 异常

未发现异常。

---

本报告完全由本地确定性规则生成，不含运行时钟。
