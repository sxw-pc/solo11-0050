# SampleRipple 交付说明

- 交付日期：2026-11-14
- 交付路径：`solo11-0050/sampleripple/`
- 运行环境：Python 3.13.15，Windows 11；运行期仅依赖 PyYAML 6.x，测试依赖 pytest 8.x
- 交付范围：F1–F8 全部功能、六类 fixture、七类异常、JSON/Markdown 报告、SQLite 追加台账、golden 对比、回环只读 HTTP、文档与自动测试
- 说明：本文件不属于 golden 对比输入，也不参与报告生成

## 1. 最终验收输出

以下命令均在项目根目录执行。`out/`、`var/` 为最终验收时临时生成，验收后已删除。

### `verify`

```text
$ python -m sampleripple verify --config configs/demo.yaml --out out/
verify OK streams=6 groups=9 points=56 anomalies=15 run_id=1 report=out/report.json
```

### `pytest -q`

```text
$ pytest -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 3.97s
```

本机全局安装的 pytest-asyncio 会在收集阶段打印一条与本项目无关的弃用警告；不影响测试结果。总运行时间约 4 秒，满足 ≤20 秒目标。

### `golden`

```text
$ python -m sampleripple golden --config configs/demo.yaml --baseline fixtures/golden/baseline.json
golden PASS differences=0 diff=out/golden_diff.md
```

### 行数约束

```text
$ python scripts/check_loc.py
python_files=32 total_lines=3724
per_file_max=300 total_budget=2500..4500
OK: line budgets satisfied
```

## 2. 确定性与基准溯源

对同一配置和 fixture 独立执行两次 `verify`，报告字节一致：

```text
5f7cf6a40b1ed8ca4b6678bd964898b0f3e5fa89534527c6ee573098f1ccb933  out/report.json
5f7cf6a40b1ed8ca4b6678bd964898b0f3e5fa89534527c6ee573098f1ccb933  out-det/report.json
20c519c6f536002f6117c060f00f683235b7917823db3be8ab7ab1ae204b8243  out/report.md
20c519c6f536002f6117c060f00f683235b7917823db3be8ab7ab1ae204b8243  out-det/report.md
```

每次运行仅 `run_meta.json` 含运行时刻、run_id 等运行时字段，且 golden 比较明确忽略它。

基准与配置指纹：

```text
report.config_hash:            sha256:26c9dfc7266e3c6276bbd868929d7f27f9e62fcbe32abd9951c314f831691c5f
manifest.config_hash:          sha256:26c9dfc7266e3c6276bbd868929d7f27f9e62fcbe32abd9951c314f831691c5f
manifest.baseline_sha256:      sha256:fff09bb5c4493d454bd7b594363afbf60b3c149e54adfdd2aa3347d7cda9a765
baseline.json 实际 SHA-256:    sha256:fff09bb5c4493d454bd7b594363afbf60b3c149e54adfdd2aa3347d7cda9a765
tool:                          sampleripple 0.1.0
```

`fixtures/golden/` 下只保留已审阅的 `baseline.json` 与 `manifest.json`。基线共 15 条异常：
GAP 2、DRIFT 6、DUPLICATE 1、DISORDER 1、MISSING 2、EDGE_SKEW 2、PERIOD_DRIFT 1。
`clean-grid` 流为 0 异常，用于检查误报。

## 3. 交付内容核对

- CLI：`serve`、`verify`、`golden`、`runs`；退出码 0/1/2/3 有稳定语义。
- 配置：YAML 严格字段校验、未知字段拒绝、稳定错误码、尽量准确的配置行号。
- 数据：CSV/JSONL、单文件/递归目录、UTF-8/UTF-8-SIG、epoch_ms/ISO-8601。
- 异常：七类全部触发；固定规则 ID、严重级别、分组、期望/实际时刻、毫秒差与说明字段。
- 报告：固定键序、三位小数、UTC 毫秒时间、LF、UTF-8；JSON 与 Markdown 均可复现。
- 台账：SQLite `runs`/`anomalies` 只追加，支持 run 查询与 rule 过滤。
- golden：结构化比较、绝对浮点容差 1e-3、缺失/篡改失败并返回退出码 1。
- HTTP：仅回环、只读 GET，分页、run/rule 过滤，404/400/405 均有稳定错误码。
- 安全：运行期无外网/DNS 请求；输入只读打开，验证前后 SHA-256 校验；全部数据为合成占位符，无密钥、真实公司名、真实域名或非回环 IP。

## 4. HTTP 运行说明

默认配置监听 `127.0.0.1:8765`。当前验收机器上 8765 已被无关的本机进程占用，因此直接运行 `serve --config configs/demo.yaml` 会按设计输出 `SRV_BIND` 并以退出码 2 结束；这不是项目缺陷。自动测试在临时回环端口启动同一服务器，已验证：

- `GET /healthz` → 200
- `GET /runs` → 200
- `GET /runs/{id}/anomalies` → 200
- 未知路由/run → 404
- 非法分页 → 400
- 非 GET → 405
- 非回环监听地址 → `SRV_BAD_HOST`

在 8765 空闲的环境中，配置中的默认端口即可直接使用。

## 5. 交付清理

最终清理已删除 `out/`、`out-det/`、`var/`、`.pytest_cache/`、全部 `__pycache__/` 与 `src/sampleripple.egg-info/`。仓库只保留源码、配置、合成 fixture、已审阅 golden 基准、测试和文档，不包含运行产物。
