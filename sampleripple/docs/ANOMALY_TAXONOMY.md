# 异常分类法

本文定义七类异常的公式、参数、索引和严重级别。设组内最早样本为锚点 `a`，标称周期为 `P`，相邻唯一样本时刻为 `t_i`，网格槽为 `slot(t) = round_half_up((t-a)/P)`。

所有异常都携带：

`stream_id`, `group_key`, `index`, `expected_ms`, `actual_ms`, `delta_ms`, `rule_id`, `category`, `severity`, `detail`, `expected_at`, `actual_at`。

其中 `delta_ms = actual_ms - expected_ms`；`expected_at` / `actual_at` 是 UTC ISO-8601 毫秒。时间轴期望值只是核验基准，不代表数据被修复。

## 1. GAP — `GAP_ADJACENT`

**判定：**

```text
interval = t_i - t_(i-1)              # 唯一样本，按时间排序
trigger  = interval > P + gap_tolerance_ms
expected = t_(i-1) + P
actual   = t_i
delta    = actual - expected
```

**索引：** 缺口后的唯一样本下标。  
**严重级别：** `warning` 当 `interval <= 2P`，否则 `error`。  
**含义：** 相邻实际落盘间隔明显超过契约。它与 `MISSING` 独立：一个看实际相邻间隔，一个看声明网格槽。

**误判提示：** `gap_tolerance_ms` 应覆盖正常采集抖动；如果容差小于正常抖动，会把普通延迟误报为缺口。

## 2. DRIFT — `DRIFT_CUMULATIVE`

**相位残差：**

```text
r_i = t_i - (a + slot(t_i) * P)
```

残差落在 `[-P/2, P/2]` 附近，表示第 i 个样本相对标称网格的相位偏移。

**判定：** 对位置 `i >= 2`，取最近至多 100 个点（`i-99..i`）的残差窗口：

```text
mean_i = sum(r_j for j in window) / len(window)
trigger = abs(mean_i) > drift_tolerance_ms
```

这是把累积相位偏移按窗口内点数归一化的结果；100 点窗口用于抑制单点尖峰，并给"每 100 点"的累计偏差一个固定观察尺度。等价地，它衡量窗口内的平均每点偏移。

**索引：** 当前唯一样本下标。  
**严重级别：** `warning` 当 `abs(mean_i) <= 2 * drift_tolerance_ms`，否则 `error`。  
**含义：** 采样相位持续偏离网格，典型原因是时钟速率误差或采集循环变慢。

**误判提示：** 固定相位但落在容差边缘的数据会形成边界敏感；窗口少于 3 个点不判定，避免两三点噪声。若相位偏移接近 `P/2`，最近槽会换槽，残差符号可能翻转。

## 3. DUPLICATE — `DUPLICATE_POINT`

**判定：** 同组内：

- 同一 `ts_ms` 出现多次；或
- 同一 `sequence_field` 值出现多次。

对首次出现之后的多余点逐点报一条。若时间戳与序号同时重复，只报一条，`detail` 为 `duplicate timestamp+sequence`。

**索引：** 时间排序后（含重复行）的行下标。  
`expected_ms`：首次同 ts/seq 点时刻；`actual_ms`：当前重复点；`delta_ms`：二者差。  
**严重级别：** `error`。  
**配置：** `duplicate_policy: ignore` 可关闭该类异常，但重复行仍参与实际点数统计。

**误判提示：** 两个设备共用同一分组键会造成跨设备重复；应在 `group_by` 中加入设备标识。序号字段若按周期重置而未分组，也会产生大量重复。

## 4. DISORDER — `DISORDER_LOCAL`

**判定：** 按原始输入顺序（加载序号，而非排序后顺序）逐点检查。对位置 `i`，回看前 `disorder_window` 个点；若存在前序时刻大于当前时刻，则报：

```text
expected = max(前看窗口内大于当前时刻的时间戳)
actual   = 当前点时间戳
delta    = actual - expected  # 负数
```

**索引：** 原始组内顺序下标。  
**严重级别：** `warning`。  
**配置：** `disorder_window: 0` 关闭局部乱序检测。窗口用于限定"可容忍的局部交换范围"，越界的大范围倒序不由该规则报告。

**误判提示：** 多文件加载时，文件按路径排序，文件间时间边界可能自然回绕；应按天/设备分文件，或把日期纳入 group_by。乱序不会修改后续排序结果。

## 5. MISSING — `MISSING_SLOT`

**判定：** 槽区间由契约的 `coverage_mode` 决定，锚点 `a` 在 `observed_span` 下为组内最早样本，在 `declared_window` 下为 `coverage_window_start_ms`：

```text
observed_span:  expected_points = floor((last - a) / P) + 1
declared_window: expected_points = floor((coverage_window_end_ms - a) / P) + 1
missing_slots   = {0..expected_points-1} \ {slot(t) for t in unique_points}
```

`declared_window` 下缺失槽覆盖整个声明窗，窗口内整段缺采不会被实测跨度掩盖；占槽与缺失互斥。声明窗与实测跨度无交集、或某点映射到负槽/越界槽时以 `COVERAGE_WINDOW_MISMATCH` 失败，不静默丢点。

- `strict`：任一缺失报 `error`。
- `allow_k`：缺失数 `<= missing_allow_k` 不报；超限时逐槽报 `warning`。
- `ignore`：不报。

**索引：** 缺失槽号。`expected_ms` 为槽时刻；由于没有实际样本，`actual_ms` 使用同一占位值，`delta_ms=0`，`detail` 标记 absent slot。  
**含义：** 从不完整跨度推导出的网格槽没有样本，与尾部未知未来无关。

**误判提示：** 跨越 DST 的本地时间如果被错误存储为 epoch 分钟，会产生幻影缺失。仅按观测跨度推断，不会把尾部未到的点当作缺失。

## 6. EDGE_SKEW — `EDGE_SKEW_HEAD` / `EDGE_SKEW_TAIL`

**判定：**

```text
boundary(t) = round_half_up(t / P) * P      # 最近 Unix epoch 标称边界
head_skew   = abs(first - boundary(first))
tail_skew   = abs(last  - boundary(last))
trigger     = skew > edge_tolerance_ms
```

首样本总检查；组内唯一点多于一个时检查末样本。  
**索引：** 首条为 0，末条为末唯一样本下标。  
**索引以外的字段：** `expected_ms = boundary(t)`，`actual_ms = t`。  
**严重级别：** `warning`。  
**含义：** 组首/末没有落在周期边界上，下游按边界切窗会错位。

**误判提示：** 用 Unix epoch 边界是刻意选择；若业务周期从任意起点开始，应把 `edge_tolerance_ms` 设为大于该相位偏移，或另行设计契约。不要把它与 `alignment_offset_ms` 混为一谈：后者是整组相位残差中位数。

## 7. PERIOD_DRIFT — `PERIOD_DRIFT_FIT`

对唯一样本做最小二乘：

```text
x_j = slot(t_j)
y_j = t_j - a
slope = sum((x_j - x_bar)(y_j - y_bar)) / sum((x_j - x_bar)^2)
trigger = abs(slope - P) > period_drift_tolerance_ms
```

至少需要两个不同槽位；否则不判定。  
**索引：** 末唯一样本下标。`expected_ms = a + P * last_slot`，`actual_ms = last`，`delta_ms = actual - expected`，并附带 `fit_period_ms`。  
**严重级别：** `error`。  
**含义：** 观测到的真实周期速率偏离标称周期，即使每个相邻间隔都在 `gap_tolerance_ms` 内也可能触发。

**误判提示：** 极短序列的拟合方差小，抖动容易被放大成斜率偏差；应结合 `DRIFT_CUMULATIVE` 和更长窗口判断。缺失槽不会扭曲 `x`，因为拟合以槽位而不是实际行号为横轴。

## 8. 严重级别

| 级别 | 当前用途 |
| --- | --- |
| `info` | 预留，当前不产生 |
| `warning` | 局部乱序、边界偏移、可容忍缺失、轻度间隔/漂移 |
| `error` | 重复、严格缺失、显著间隔/漂移、周期拟合失败 |
| `critical` | 预留，当前不产生 |

## 9. 排序与聚合

同一组内异常按：

```text
(group_key, index, rule 定义顺序, expected_ms, actual_ms, detail)
```

排序。报告按类别和规则输出固定键集合（包含 0 计数），因此 clean 数据也会显示完整的零值表。
