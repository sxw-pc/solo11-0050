# SampleRipple 设计说明

## 1. 目标与不变量

SampleRipple 在完全离线、只读的前提下，把"周期采样契约"与"实际落盘样本"逐组对齐，输出可审计异常证据。四条不变量：

1. 输入样本只读，运行前后 sha256 必须一致。
2. `report.json` 与 `report.md` 不含当前时间、随机数或运行路径；同输入同配置字节一致。
3. 台账只追加，不更新、不删除历史运行与异常。
4. 核验过程不发起网络请求；HTTP 服务只绑定回环地址且只提供 GET。

## 2. 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py` | 读取 YAML、路径解析、顶层 schema、错误行号、配置哈希 |
| `contract_fields.py` | 契约字段级类型与范围校验 |
| `contracts.py` | 单个采集流契约模型与完整 schema 校验 |
| `loaders.py` | 只读发现/解析 CSV、JSONL，分组字段与时间字段标准化 |
| `timeline.py` | 分组、原始顺序保留、时间排序、网格槽重建、拟合 |
| `anomalies.py` | 七类异常判定、稳定 rule_id 与严重级别 |
| `engine.py` | 编排加载、重建、分类，形成内存核验结果 |
| `report_json.py` | 生成无运行时钟的结构化报告对象 |
| `report_markdown.py` | 生成确定性 Markdown 摘要 |
| `ledger.py` | SQLite 建表、追加运行与异常、分页查询 |
| `golden.py` | 结构化 baseline 对比、容差、Markdown diff |
| `server.py` | 回环只读 HTTP 路由与分页 |
| `cli.py` | 四个子命令、退出码、输出文件和 run_meta |
| `util/timeutil.py` | UTC ISO-8601 毫秒与 epoch_ms 解析/格式化 |
| `util/hashing.py` | 配置、样本、报告 sha256 |
| `util/jsonutil.py` | 排序键、固定浮点、LF 的规范 JSON 写入 |

禁止模块级可变业务状态；配置、契约、时间轴、异常均为不可变 dataclass 或只读 tuple。

## 3. 数据流

```text
configs/*.yaml
      |
      v
 [load_config] --(CFG_* 错误, 行号)--> stderr / exit 2
      |
      v
 AppConfig (streams, ledger, server, config_hash)
      |
      +----> discover_files: 文件或递归目录 -> 排序后的 .csv/.jsonl
      |
      v
 [load_stream] --(LOAD_* 错误, 文件:行)--> stderr / exit 2
      |
      v
 tuple[Point]  group / ts_ms / sequence / ordinal / provenance
      |
      v
 [build_timelines] 按 group_key 分区 -> 保留 raw order + 时间排序
      |
      v
 [detect_anomalies] 7 类判定 -> tuple[Anomaly]
      |
      +----> build_report ----> out/report.json + out/report.md
      |
      +----> record_run ------> SQLite runs + anomalies (append only)
      |
      +----> run_meta.json (started_at, config hash, file sha256, run_id)
```

`golden` 走同一加载/重建/分类路径，仅构建内存报告并与 baseline 比较，不写台账。
`serve` 只读取配置指定的台账，不触发加载器。

## 4. 时间轴重建

设一个分组内的样本集合为 `S`，标称周期 `P = nominal_period_ms`。

1. **保留原始顺序**：按加载序号 `ordinal` 排列，得到 `raw_points`。该顺序只用于乱序检测。
2. **排序**：按 `(ts_ms, sequence, ordinal)` 升序排列为 `points`。重复时间戳仍保留，便于重复检测。
3. **唯一点**：按 `ts_ms` 保留首次出现，得到 `unique_points`，用于网格、缺口、漂移等统计。
4. **锚点**：`anchor = min(ts_ms)`，即组内最早样本时刻；`last = max(ts_ms)`。
5. **预期点数**：`expected_points = floor((last - anchor) / P) + 1`。它只覆盖观测跨度，不推测尾部未来样本。
6. **槽位**：`slot(t) = round_half_up((t - anchor) / P)`；每个唯一点映射到最近的锚点网格槽。
7. **缺失槽**：`[0, expected_points)` 中没有唯一点落入的槽。
8. **对齐偏移**：对每个唯一点，计算它到最近 Unix epoch 标称边界
   `b(t) = round_half_up(t / P) * P` 的有符号残差，取中位数，即
   `alignment_offset_ms`。该指标衡量整体相位是否踩在标称边界上。
9. **周期拟合**：以槽位为 `x`、`t - anchor` 为 `y` 做最小二乘斜率，得到
   `period_fit_ms`。少于两个不同槽位时为 `null`。
10. **分组键**：`group_by` 字段值按配置顺序用 `/` 连接。示例数据只用无歧义占位符。

重建不修改、不回填任何样本；乱序和重复都作为证据输出。

## 5. 异常判定

| 类别 | 主索引 | 预期时刻 `expected_ms` | 实际时刻 `actual_ms` |
| --- | --- | --- | --- |
| `GAP` | 缺口后唯一点下标 | 前一点 + `P` | 缺口后唯一点 |
| `DRIFT` | 当前唯一点下标 | 锚点网格槽时刻 | 当前唯一点 |
| `DUPLICATE` | 排序后行下标 | 首次同 ts/seq 点时刻 | 当前重复点 |
| `DISORDER` | 原始组内下标 | `disorder_window` 内最大前序时间 | 当前逆序点 |
| `MISSING` | 缺失槽号 | 槽时刻 | 同预期时刻（占位，`delta_ms=0`） |
| `EDGE_SKEW` | 0 或末唯一点下标 | 最近标称边界 | 首/末样本 |
| `PERIOD_DRIFT` | 末唯一点下标 | 锚点 + `P * last_slot` | 末唯一点 |

`severity` 固定为 `info` / `warning` / `error` / `critical` 之一；当前实现只产生 `warning` 与 `error`。每个异常还带 `expected_at` / `actual_at`（UTC ISO-8601 毫秒）和人类可读 `detail`。

公式、阈值和误判提示见 [ANOMALY_TAXONOMY.md](ANOMALY_TAXONOMY.md)。

## 6. SQLite 台账

台账只追加。`verify` 在一个事务中插入一条 `runs` 记录和全部逐异常记录。没有 UPDATE 或 DELETE 路径。

```sql
CREATE TABLE runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    stream_count INTEGER NOT NULL,
    group_count INTEGER NOT NULL,
    actual_points INTEGER NOT NULL,
    anomaly_count INTEGER NOT NULL
);

CREATE TABLE anomalies (
    anomaly_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stream_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    point_index INTEGER NOT NULL,
    expected_ms INTEGER NOT NULL,
    actual_ms INTEGER NOT NULL,
    delta_ms INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

辅助索引：`(run_id, rule_id)` 与 `(run_id)`。HTTP 和 `runs` 子命令只执行查询。

## 7. 确定性保证

- 配置中的流按声明顺序处理；组按 `group_key` 排序；异常按 `(group_key, index, rule 顺序, expected, actual, detail)` 排序。
- 文件发现按相对 posix 路径小写排序；行按文件、行号顺序赋 `ordinal`。
- JSON 对象键排序，浮点统一 `%.3f`，整数不转浮点，字符串 UTF-8 非转义，换行固定 LF，文件末尾有换行。
- 所有时间在报告中使用 UTC ISO-8601 带三位毫秒；便于机器处理的 `_ms` 字段保留整数。
- 报告不写 `started_at`、耗时、run_id、绝对路径或随机量。
- 运行元信息单独写入 `run_meta.json`，只有它记录 `started_at`；golden 不比较它。
- 输出不依赖 locale、时区环境或当前时间。

## 8. HTTP 服务

`serve` 在启动前校验地址属于 `{127.0.0.1, localhost, ::1}`，其余地址拒绝。路由：

- `GET /healthz`
- `GET /runs?limit=1..100&offset>=0`
- `GET /runs/{run_id}/anomalies?rule_id=...&limit=...&offset=...`

未知路由或未知 run 返回 404；非 GET 返回 405；参数错误返回 400。响应不包含源数据原始行，也不提供写路径。

## 9. 运行产物与归档

`verify` 每次写：

- `out/report.json`：结构化、可回归。
- `out/report.md`：摘要。
- `out/run_meta.json`：配置哈希、输入文件与 sha256、报告哈希、`started_at`、run_id。
- 配置指定 SQLite 台账中的新 run。

`fixtures/golden/baseline.json` 只保存一次已审阅的 `report.json`，其来源由 `manifest.json` 记录。`out/` 与 `var/` 不入库。
