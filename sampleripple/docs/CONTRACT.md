# 契约与配置规范

配置文件是 UTF-8 YAML，使用 `yaml.safe_load` 解析。未知字段会被拒绝，避免拼写错误静默生效。所有错误带有稳定错误码；字段错误尽可能带配置行号。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `version` | int | 是 | — | 当前只接受 `1` |
| `base_dir` | string | 否 | 配置所在目录 | `source`、`ledger.db_path` 相对路径的基准；必须存在且为目录 |
| `ledger` | mapping | 否 | `{db_path: var/ledger.db}` | 只追加 SQLite 台账 |
| `ledger.db_path` | string | 否 | `var/ledger.db` | 相对 `base_dir`；不能是目录 |
| `server` | mapping | 否 | 回环 8765 | HTTP 只读服务 |
| `server.host` | string | 否 | `127.0.0.1` | 只允许 `127.0.0.1`、`localhost`、`::1` |
| `server.port` | int | 否 | `8765` | 1..65535 |
| `streams` | list | 是 | — | 至少一个采集流契约 |

配置哈希是配置文件原始字节的 `sha256:` 前缀 SHA-256，不包含运行时环境。

## 2. 采集流字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `stream_id` | string | 是 | 全配置唯一，用于报告和台账 |
| `source` | string | 是 | 单文件或目录；目录递归发现 `*.csv` / `*.jsonl` |
| `format` | enum | 否 | `auto`（默认）/ `csv` / `jsonl` |
| `time_field` | string | 是 | 时间列/字段名 |
| `time_format` | enum | 是 | `epoch_ms` 或 `iso8601` |
| `sequence_field` | string/null | 否 | 样本序号字段；缺失时 `null` |
| `group_by` | list[string] | 是 | 非空、无重复；不能包含时间或序号字段 |
| `nominal_period_ms` | int | 是 | 标称周期，`> 0` |
| `jitter_tolerance_ms` | int | 是 | 基础抖动容忍，`>= 0`；也是三个可选容忍的默认值 |
| `gap_tolerance_ms` | int | 是 | 相邻间隔告警容忍，`>= 0` |
| `missing_policy` | enum | 是 | `strict` / `allow_k` / `ignore` |
| `missing_allow_k` | int | 否 | `>= 0`，仅 `allow_k` 生效，默认 0 |
| `disorder_window` | int | 是 | `>= 0`；0 表示关闭局部乱序检查 |
| `duplicate_policy` | enum | 是 | `report` / `ignore` |
| `drift_tolerance_ms` | int | 否 | DRIFT 阈值；默认 `jitter_tolerance_ms` |
| `period_drift_tolerance_ms` | int | 否 | 周期拟合偏差阈值；默认 `jitter_tolerance_ms` |
| `edge_tolerance_ms` | int | 否 | 首末边界偏移阈值；默认 `jitter_tolerance_ms` |
| `period_estimator` | enum | 否 | 周期估计模式：`robust`（默认）/ `least_squares` |

`time_field`、`sequence_field`、`group_by` 必须指向不同字段。`group_by` 字段值按声明顺序以 `/` 连接为 `group_key`。

## 3. 数据格式

### 3.1 CSV

- 编码：UTF-8 或 UTF-8-SIG（BOM 自动剥离）。
- 第一行必须是唯一、非空的表头。
- 必须包含 `group_by`、`time_field`、声明的 `sequence_field` 对应列。
- 空行跳过；字段数多于表头、必需列缺失、空分组值或时间非法均报错。
- 数值时间接受整数或整数字符串；ISO-8601 接受带 `Z`、偏移或朴素 UTC 字符串。

### 3.2 JSONL

- 每行一个 JSON object；空行跳过。
- 必需字段缺失、值为 null、类型非法、时间非法均带行号报错。
- 分组字段与序号字段只接受非空字符串或整数；整数值标准化为十进制字符串。
- 未声明的额外字段被忽略。

### 3.3 ISO-8601

- 时区统一转换为 UTC；无时区按 UTC 解释。
- 输出统一为 `YYYY-MM-DDTHH:MM:SS.mmmZ`。
- 亚毫秒部分截断，不四舍五入。

## 4. 容忍策略

- `missing_policy: strict`：任一网格槽缺失都产生 `MISSING_SLOT`，严重级别 `error`。
- `missing_policy: allow_k`：缺失数 `<= missing_allow_k` 时不报；否则逐槽报 `warning`。
- `missing_policy: ignore`：不产生缺失异常。
- `duplicate_policy: report`：同组同时间戳或同序号的多余点逐点报错。
- `duplicate_policy: ignore`：不产生重复异常，但重复行仍计入 `actual_points`，统计中保留。

`GAP` 与 `MISSING` 可以同时触发：前者看相邻实际间隔，后者看声明网格槽。二者语义不同，不互相抑制。

## 4.1 周期估计模式（`period_estimator`）

周期拟合（`period_fit_ms` 与 `PERIOD_DRIFT_FIT` 判定）先按槽位对唯一样本去重：同一槽位只保留输入顺序中的第一条代表点，多余残差不重复进入估计。估计量随后在这份代表点集合上计算，并以同一集合的末槽位 `last_slot` 作为异常引用点，保证报告数值与判定口径自洽。

- `robust`（默认）：Theil–Sen 估计量，取全部代表点两两斜率的中位数。单个离群点（例如相对标称偏移一个完整周期）最多贡献一小部分斜率，无法拉动中位数；输出由排序决定，确定且无随机步骤。
- `least_squares`：保留历史口径，对代表点做槽位–时间最小二乘线性回归，斜率即拟合周期。

两种模式的输入都是槽位去重后的同一份代表点集合；少于两个不同槽位时不产出拟合值，也不触发 `PERIOD_DRIFT_FIT`。模式只影响周期估计与由其派生的判定，不改变槽位、缺失、对齐等其他口径。

## 5. 输入安全

- 加载器只调用读模式 `open(..., "r")`，不支持写入、修复或原地更新。
- 文件发现结果按稳定路径排序；组和行序确定。
- 开跑前与读完后分别计算输入 sha256；如发生变化，`verify` 以 `INPUT_MUTATED` 失败。
- `auto` 模式只接受 `.csv` / `.jsonl`；显式 `format` 对单文件生效，目录仍按扩展名筛选。

## 6. 示例

```yaml
version: 1
base_dir: ..
ledger:
  db_path: var/ledger.db
server:
  host: 127.0.0.1
  port: 8765
streams:
  - stream_id: sensor-edge
    source: fixtures/samples/clean
    format: csv
    time_field: ts
    time_format: epoch_ms
    sequence_field: seq
    group_by: [site, line, sensor]
    nominal_period_ms: 60000
    jitter_tolerance_ms: 500
    gap_tolerance_ms: 1000
    missing_policy: strict
    disorder_window: 2
    duplicate_policy: report
```

## 7. 错误码

### 配置

| 错误码 | 含义 |
| --- | --- |
| `CFG_FILE_NOT_FOUND` | 配置文件不存在 |
| `CFG_ENCODING` | 配置不是 UTF-8 |
| `CFG_YAML_SYNTAX` | YAML 语法错误；带尽量准确的行号 |
| `CFG_NOT_MAPPING` | 配置根不是 mapping |
| `CFG_UNKNOWN_FIELD` | 顶层、流、ledger 或 server 中出现未知字段 |
| `CFG_MISSING_FIELD` | 缺少必填字段 |
| `CFG_BAD_VERSION` | `version` 不是 1 |
| `CFG_BAD_TYPE` | 字段类型或空值不合法 |
| `CFG_BAD_PERIOD` | 周期不是正整数 |
| `CFG_BAD_TOLERANCE` | 容忍值为负或类型非法 |
| `CFG_BAD_ENUM` | 枚举值或 format 不合法 |
| `CFG_BAD_GROUP_BY` | group_by 为空/重复/与时间序号冲突 |
| `CFG_BAD_TIME_FORMAT` | 时间格式不是 `epoch_ms` / `iso8601` |
| `CFG_SOURCE_NOT_FOUND` | source 不存在 |
| `CFG_BAD_PATH` | base_dir 或 source 路径类型错误 |
| `CFG_EMPTY_STREAMS` | streams 为空 |
| `CFG_DUPLICATE_STREAM` | stream_id 重复 |
| `CFG_BAD_HOST` | 非回环监听地址 |
| `CFG_BAD_PORT` | 端口不在 1..65535 |

### 样本加载

| 错误码 | 含义 |
| --- | --- |
| `LOAD_NO_FILES` | 目录中没有匹配的样本文件 |
| `LOAD_UNKNOWN_FORMAT` | 无法从扩展名推导格式 |
| `LOAD_IO` | 文件无法打开 |
| `LOAD_BAD_HEADER` | CSV 表头缺失、空列名或重复列名 |
| `LOAD_MISSING_COLUMN` | CSV 缺必需列 |
| `LOAD_BAD_ROW` | CSV 行列数异常 |
| `LOAD_BAD_CSV` | CSV 解析错误 |
| `LOAD_ENCODING` | 文件不是 UTF-8/UTF-8-SIG |
| `LOAD_BAD_JSON` | JSONL 语法错误或不是 object |
| `LOAD_MISSING_FIELD` | JSONL/CSV 必需字段缺失或为空 |
| `LOAD_BAD_GROUP` | 分组字段值类型非法 |
| `LOAD_BAD_TIME` | 时间字段无法按声明格式解析 |
| `LOAD_BAD_SEQUENCE` | 序号字段为空或类型非法 |
| `LOAD_EMPTY` | 流中没有任何样本 |

### 运行期与入口

| 错误码 | 含义 |
| --- | --- |
| `INPUT_MUTATED` | 源文件在核验期间发生变化 |
| `GOLDEN_MISSING` | golden baseline 文件不存在 |
| `GOLDEN_BAD_JSON` | baseline 不是可解析 JSON |
| `SRV_BAD_HOST` | 服务绑定时地址不是回环 |
| `SRV_BAD_PORT` | 服务绑定端口非法 |
| `SRV_BIND` | 回环端口绑定失败（如端口占用） |
| `HTTP_BAD_QUERY` | HTTP 分页参数非法 |
| `HTTP_NOT_FOUND` | HTTP 路由不存在 |
| `RUN_NOT_FOUND` | HTTP 请求的 run 不存在 |
| `METHOD_NOT_ALLOWED` | HTTP 非 GET 请求 |
