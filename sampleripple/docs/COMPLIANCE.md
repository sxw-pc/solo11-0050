# 合规与审计声明

## 1. 离线声明

SampleRipple 的核验、报告、台账、golden 对比和测试均不需要外部网络：

- 运行期依赖只有标准库与 `PyYAML`。
- `verify`、`golden`、`runs` 不创建套接字，不访问 URL，不做 DNS。
- 输入路径必须是本地已存在的文件或目录；配置中的 URL 文本会被当作本地路径并在存在性检查时拒绝。
- 没有遥测、崩溃上报、许可证校验、包下载或远程配置。
- `serve` 是唯一的套接字入口；它只允许绑定 `127.0.0.1`、`localhost`、`::1`，且仅提供 GET。`localhost` 在绑定前归一化为 `127.0.0.1`。
- HTTP 测试通过回环 `127.0.0.1` 进行，不访问外部地址。

## 2. 只读声明

- CSV 使用 `open(..., "r", encoding="utf-8-sig", newline="")`；JSONL 同理。
- 加载器不包含写、改名、删除、截断源文件的操作。
- `verify` 在加载前后分别计算每个源文件的 sha256；不一致时以 `INPUT_MUTATED` 退出。
- 源文件哈希、配置哈希和报告哈希写入独立的 `run_meta.json`，便于归档比对。
- 运行产物只写到：
  - `--out` 指定目录（默认 `out/`）；
  - 配置声明的 SQLite 台账路径；
  - golden 指定的 diff 路径（默认 `out/golden_diff.md`）。
- 仓库提交的 `fixtures/golden/` 是已审阅基准；`out/`、`var/` 被 `.gitignore` 排除。

## 3. 台账只追加

`runs` 与 `anomalies` 只在 `verify` 的事务中执行 INSERT。代码不包含对台账的 UPDATE / DELETE。重复运行会产生新的 `run_id`。HTTP 服务与 `runs` 子命令只执行 SELECT。

## 4. 合成数据声明

- 所有 fixture、配置、文档示例均为本地合成的占位数据。
- 分组值使用 `site-a`、`line-07`、`sensor-x` 等虚构标识。
- 不包含真实设备序列号、真实公司名、真实域名或真实公网 IP。
- 出现的网络地址只有回环地址 `127.0.0.1` / `::1` / `localhost`。
- 测试中的非回环拒绝用例使用保留的 `.invalid` 主机名，不触发解析。
- 不包含密钥、令牌、证书、账号或任何凭据；运行不需要环境变量凭据。

## 5. 第三方依赖

| 用途 | 依赖 | 说明 |
| --- | --- | --- |
| 运行期 YAML 解析 | `PyYAML>=6,<7` | 唯一第三方运行期依赖 |
| HTTP | `http.server` | 标准库，不引入 Web 框架 |
| SQLite | `sqlite3` | 标准库，不引入 ORM |
| 测试 | `pytest` | 仅测试期 |

## 6. 建议的合规验证方法

以下命令都在仓库根目录执行，不会联网：

```bash
# 1) 静态确认运行期没有外部 HTTP 客户端
python - <<'PY'
from pathlib import Path
needles = ("requests", "httpx", "urllib.request", "aiohttp")
for path in Path("src/sampleripple").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    hits = [n for n in needles if n in text]
    print(path, hits)
PY

# 2) 确认输入哈希在 verify 前后不变
python - <<'PY'
from pathlib import Path
from hashlib import sha256
for p in sorted(Path("fixtures/samples").rglob("*")):
    if p.is_file():
        print(p, sha256(p.read_bytes()).hexdigest())
PY
python -m sampleripple verify --config configs/demo.yaml --out out/
python - <<'PY'
# 再次执行上面的哈希打印，逐行比较输出
PY

# 3) 确认 HTTP 监听地址不是非回环
python - <<'PY'
import yaml
cfg = yaml.safe_load(open("configs/demo.yaml", encoding="utf-8"))
print(cfg["server"])
PY

# 4) 运行测试
pytest -q
```

如系统提供网络命名空间或防火墙，可在断网环境中执行 `verify` 与 `pytest`；除 HTTP 用例使用回环外，测试不依赖任何外部连接。

## 7. 已知边界

- 不解析私有二进制协议。
- 不做数据修复、回填、重采样或插值。
- 不做鉴权，因为服务只监听本机回环且仅只读。
- 不防并发写台账；设计上单进程 `verify`。
- HTTP 服务标准库会自动生成 HTTP `Date` 响应头；业务 JSON body 不含运行时钟。
