# SampleRipple

**一句话定位：** SampleRipple 是一个纯本地、只读、确定性的周期性采样时间轴核验引擎，把标称采样时刻与实际落盘时刻之间的抖动、缺口、重复、乱序与边界错位变成可归档、可回归、可审计的证据。

## 边界

**它是：**

- 对已落盘 CSV / JSONL 时序样本做逐组时间轴重建与七类异常分类的核验工具。
- 输出字节稳定 JSON、Markdown 摘要、运行元信息与追加式 SQLite 台账的审计工具。
- 提供 golden 结构化回归卡点和本机只读 HTTP 查询入口的小型数据工程工具。

**它不是：**

- 时序数据库、监控系统、告警平台、看板或前端 CRUD。
- 数据修复、回填、插值或重采样执行器；它只核验并留证。
- 分布式调度器，也不解析设备私有二进制协议或接入真实消息队列。
- 网络服务；唯一可监听的地址是回环地址。

## 快速开始

要求 Python 3.10+。运行期只需要 `PyYAML`，测试只需要 `pytest`。

```bash
python -m pip install -e ".[test]"

# 核验 demo 契约，写出 out/report.json、out/report.md、out/run_meta.json
python -m sampleripple verify --config configs/demo.yaml --out out/

# 与已审阅基准做结构化回归；通过返回 0，回归失败返回 1
python -m sampleripple golden --config configs/demo.yaml \
  --baseline fixtures/golden/baseline.json

# 启动只读本机 HTTP 服务（默认 127.0.0.1:8765）
python -m sampleripple serve --config configs/demo.yaml

# 查看历史运行
python -m sampleripple runs --config configs/demo.yaml

# 全量测试
pytest -q
```

服务启动后，在另一个终端查询：

```bash
curl http://127.0.0.1:8765/healthz
curl "http://127.0.0.1:8765/runs?limit=10&offset=0"
curl "http://127.0.0.1:8765/runs/1/anomalies?rule_id=GAP_ADJACENT"
```

## 配置示例

完整字段见 [docs/CONTRACT.md](docs/CONTRACT.md)。最小片段：

```yaml
version: 1
base_dir: ..                 # 配置内相对路径的基准目录
ledger:
  db_path: var/ledger.db     # 只追加台账
server:
  host: 127.0.0.1            # 非回环地址会被拒绝
  port: 8765
streams:
  - stream_id: clean-grid
    source: fixtures/samples/clean   # 文件或目录；目录递归发现 .csv/.jsonl
    format: csv
    time_field: ts
    time_format: epoch_ms           # epoch_ms 或 iso8601
    sequence_field: seq
    group_by: [site, line, sensor]
    nominal_period_ms: 60000
    jitter_tolerance_ms: 500
    gap_tolerance_ms: 1000
    missing_policy: strict           # strict / allow_k / ignore
    disorder_window: 2
    duplicate_policy: report         # report / ignore
    drift_tolerance_ms: 500
    period_drift_tolerance_ms: 200
    edge_tolerance_ms: 500
```

`source` 与 `ledger.db_path` 的相对路径以 `base_dir` 为基准；未声明 `base_dir` 时以配置文件所在目录为基准。所有示例键值均为合成占位符。

## 异常类别

| 类别 | 稳定 rule_id | 判定摘要 |
| --- | --- | --- |
| `GAP` | `GAP_ADJACENT` | 相邻唯一样本间隔大于标称周期 + `gap_tolerance_ms` |
| `DRIFT` | `DRIFT_CUMULATIVE` | 最近至多 100 个点的有符号相位残差均值超过 `drift_tolerance_ms` |
| `DUPLICATE` | `DUPLICATE_POINT` | 同组同时间戳或同 sequence 重复出现 |
| `DISORDER` | `DISORDER_LOCAL` | 原始输入顺序在 `disorder_window` 内出现时间逆序 |
| `MISSING` | `MISSING_SLOT` | 按时间跨度声明的网格槽缺失，超过 `missing_policy` 容忍度 |
| `EDGE_SKEW` | `EDGE_SKEW_HEAD` / `EDGE_SKEW_TAIL` | 组首/末样本偏离最近的标称边界超过 `edge_tolerance_ms` |
| `PERIOD_DRIFT` | `PERIOD_DRIFT_FIT` | 最小二乘拟合周期与标称周期偏差超过 `period_drift_tolerance_ms` |

公式、严重级别与误判提示见 [docs/ANOMALY_TAXONOMY.md](docs/ANOMALY_TAXONOMY.md)。

## 输出

```text
out/
├── report.json     # 完整结构化证据，字节稳定，含分组摘要、异常、全局统计
├── report.md       # 人可读摘要、类别计数、Top-20 异常、按 rule 聚合
└── run_meta.json   # 运行元信息；只有该文件记录 started_at
```

`report.json` 不包含当前时间、随机值或运行目录。输入文件 sha256、配置哈希与 `started_at` 只写入 `run_meta.json`；golden 只比较 `report.json` 结构。台账在配置的 SQLite 文件中追加，不更新旧记录。

## 退出码

| 退出码 | 语义 |
| --- | --- |
| `0` | 命令成功；`verify` 即使发现异常也算成功产出证据 |
| `1` | `golden` 结构化回归失败 |
| `2` | 配置、输入数据、路由参数或参数用法错误 |
| `3` | 输出或台账 I/O 失败 |

## 目录结构

```text
sampleripple/
├── pyproject.toml
├── README.md
├── DELIVERY.md
├── configs/                 # demo / strict 契约
├── docs/                    # 设计、契约、异常分类、合规声明
├── fixtures/
│   ├── samples/             # 六组合成 fixture
│   └── golden/              # 已审阅 baseline 与 manifest
├── scripts/check_loc.py     # 单文件行数与代码总量自检
├── src/sampleripple/        # 标准库 + PyYAML 实现
└── tests/                   # pytest 回归套件
```

## 确定性与合规

- 相同输入与配置生成字节相同的 `report.json` / `report.md`：键排序、浮点固定 3 位小数、UTC ISO-8601 毫秒、LF 换行。
- 输入文件只以读模式打开；运行前后哈希必须一致。核验运行不写源数据。
- 核验与测试期间无外部网络请求；只有 `serve` 显式绑定回环地址。
- 台账只追加。`out/`、`var/` 是运行产物目录，不纳入仓库。

合规验证方法见 [docs/COMPLIANCE.md](docs/COMPLIANCE.md)。
