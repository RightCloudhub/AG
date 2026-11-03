# 项目初始化（Setup）设计与优化

**状态**：已实施；当前实现与验证边界见 §7
**日期**：2026-11-02
**范围**：开发者环境初始化（解释器、依赖、`.env`、体检、文档入口）、锁文件与 CI 安装路径
**关联**：[tech-stack.md ADR-008](../../plan/engineering/tech-stack.md)、[rules.md §7](../../plan/engineering/rules.md)、[RUNBOOK](../ops/RUNBOOK.md)、[repo-structure.md](../../plan/engineering/repo-structure.md)

---

## 1. 当前入口

`./scripts/setup.sh` 是统一初始化入口：按 `.python-version` 与 `uv.lock` 创建或更新
`.venv`，默认安装 `dev,webapp`，仅在缺失时播种 `.env`，然后运行 `scripts/doctor.py`。
`--extras dev` 用于核心开发，`--locked` 要求锁文件保持最新；CI 同样使用 uv 的锁定安装。
`scripts/bootstrap_poc.sh` 由 `--with-demo-data` 调用，使用 `.venv` 解释器与控制台入口。

原始入口盘点与 S-01…S-08 缺陷记录见 [CHANGELOG](../../CHANGELOG.md#setup-original-audit)。

## 2. 目标与非目标

**目标（G）**

- G1 一条命令从零到可跑离线评测：`./scripts/setup.sh` → `source .venv/bin/activate` → `agr-query --no-llm "..."`。
- G2 可复现：`uv.lock` 生效，Python 小版本按 `.python-version` 固定；CI 与本地装同一组版本。
- G3 可诊断：环境不就绪时，一条命令说清缺什么、怎么补。
- G4 幂等：重复执行安全；绝不覆盖已有 `.env`。
- G5 零新增运行时工具：只用 `uv` + `bash` + CPython 标准库（不引入 make / just / nox / tox / devcontainer）。
- G6 文档单一事实源：安装命令只在一个地方定义，其余文档指回去。

**非目标**

- 不做 Docker/Qdrant/Neo4j 的自动供给（`live`/`hybrid` 仍由 `docker compose up -d` 或 `docs/ops/EXTERNAL_RUNTIMES.md` 的 tarball 方案负责）。
- 不做 devcontainer / Nix / 容器镜像。
- 不改依赖组划分（不把 `dev` 迁到 `[dependency-groups]`）——那会一次性打破全部文档与 CI 写法，收益低于风险。
- 不引入发布/打包流程（`uv build`、PyPI 发布）。
- 不改 `--no-llm` 离线评测路径的任何行为。

---

## 3. 设计

### 3.1 三层职责

```
scripts/setup.sh          环境层：解释器 + 依赖 + .env 播种 + 调用体检     （幂等，可重复执行）
scripts/doctor.py         体检层：只读检查并给出可执行的修复建议           （纯函数 + 无第三方依赖）
scripts/bootstrap_poc.sh  数据层：ingest → seed 图 → BM25 索引            （仅离线演示数据）
README.md 快速开始        文档层：唯一权威写法，其余文档指回此处
```

体检层刻意与安装层分离：装坏了也要能跑体检（`doctor.py` 只用标准库，不 import `agentic_graphrag` 之外的第三方运行时）。

### 3.2 `scripts/setup.sh`（单一入口）

| 参数 | 作用 | 默认 |
|---|---|---|
| `--extras LIST` | 逗号分隔的可选依赖组 | `dev,webapp`（完整单测与 Web 应用所需，见 CLAUDE.md） |
| `--all-extras` | 安装全部可选依赖 | 关 |
| `--python VERSION` | 覆盖解释器版本 | 读 `.python-version` |
| `--locked` | 严格使用已提交锁文件，锁文件过期即失败（uv 的 `--frozen` 不做此校验，见 §7） | 关（开发默认允许顺带刷新锁文件） |
| `--inexact` | 保留环境里不在锁文件中的额外包 | 关（默认精确同步，保证可复现） |
| `--offline` | 不访问网络（`uv --offline`） | 关 |
| `--with-demo-data` | 追加执行 `bootstrap_poc.sh` 建演示数据 | 关 |
| `--no-verify` | 跳过 doctor 体检 | 关 |
| `--dry-run` | 只打印将执行的命令 | 关 |
| `-h, --help` | 用法 | — |

执行顺序（每步幂等）：

1. 解析仓库根（脚本所在目录的父目录），`cd` 过去——所有路径与 cwd 无关。
2. 检查 `uv` 存在；缺失时**打印官方安装命令并退出 1**（不自行 `curl | sh`，避免静默执行远端脚本）。
3. 校验 `uv` 版本 ≥ `0.9.0`（低于此版本无 `uv sync --extra` 语义保证）。
4. `uv python install "$PY"`：`.python-version` 指定的解释器缺失时才真正下载（uv 会自动跳过已存在版本）。
5. `uv sync --extra <每个 extra>`（`--locked` / `--inexact` / `--offline` 按参数透传）；`uv sync` 会按 `.python-version` 创建/复用 `.venv` 并以 editable 方式安装本包，因此不再需要 `uv venv` + `uv pip install -e`。
6. `.env` 播种：不存在则 `cp .env.example .env` 并提示；**已存在则完全不动**，仅提示"未改动"。密钥仍为空，离线路径无需任何 key。
7. 体检：`"$VENV_PY" scripts/doctor.py`（`--no-verify` 跳过）。
8. 打印下一步：激活环境、离线提问、跑评测、起服务、起 Web 应用。

退出码：0 成功；1 前置条件缺失（无 uv / 版本过低 / 同步失败 / 体检有 error）；2 参数错误。

### 3.3 `scripts/doctor.py`（体检）

结构与可测性：纯函数 `collect_report(root, env) -> report`、`render_text()`、`render_json()`；只有 `main()` 做 IO（打印、探测网络端口）。默认零网络：仅在 `AGR_PROFILE` 为 `live`/`hybrid` 时才做 TCP 探活。

| 编号 | 检查 | 判定 | 失败级别 |
|---|---|---|---|
| ENV-01 | 解释器版本满足 `requires-python` | 解析 pyproject 下限 | error |
| ENV-02 | 解释器版本与 `.python-version` 主次版本一致 | — | warn |
| ENV-03 | 运行的是仓库 `.venv` 解释器 | `sys.prefix` 指向 `<root>/.venv` | warn |
| ENV-04 | `agentic_graphrag` 可导入且指向本仓库 `src/` | 路径比对 | error |
| DEP-01 | 必需依赖可导入（fastapi、uvicorn、pydantic、pydantic-settings、httpx、yaml、neo4j、qdrant-client、rank-bm25、langgraph、tiktoken、structlog、orjson、python-multipart） | 逐个 `find_spec` | error（列出缺失项） |
| DEP-02 | 开发工具（pytest、ruff、mypy） | — | warn → 建议 `uv sync --extra dev` |
| DEP-03 | Web 应用组（sqlalchemy、alembic、argon2、jwt、psycopg） | — | warn → `--extra webapp` |
| DEP-04 | 可选组 `otel` | — | info |
| DEP-05 | 可选组 `sqlite-checkpoint` | — | info |
| CFG-01 | `.env` 是否存在 | — | warn |
| CFG-02 | `AGR_ALLOW_LLM=1` 时 `LLM_API_KEY` 非空且非占位符 | 与启动期 fail-fast 同规则 | error |
| CFG-03 | `AGR_PROFILE` ∈ {offline, live, hybrid, eval} | — | error |
| CFG-04 | `.env` 里写了进程级 `AGR_*`（`AGR_REQUIRE_AUTH` / `AGR_INGEST_INLINE` / `AGR_API_KEYS` 等） | 提示"需 export 才生效" | info |
| DATA-01 | `data/processed/seed_triples.jsonl`、`pilot_triples.jsonl` | — | warn → 给出建图命令 |
| DATA-02 | `data/raw/` 非空 | — | info |
| LIVE-01 | `live`/`hybrid` 时 Neo4j / Qdrant TCP 探活 | 2 秒超时 | error；`offline` 时输出"已跳过" |
| WEB-01 | `node` 存在（零构建前端校验用） | — | info |
| WEB-02 | 端口 8000 是否被占用 | 仅提示 | info |

输出：人类可读分组（`环境 / 依赖 / 配置 / 数据 / 在线后端 / 前端工具`）+ `--json`（机器可读）、`--strict`（warn 也返回 1）、`--root PATH`（测试用）。退出码 0（无 error）/ 1（有 error）。

### 3.4 Python 与锁文件

`.python-version` 已入库，值为 `3.12`；`requires-python = ">=3.12"`，体检 ENV-02
检查二者一致。开发默认 `uv sync`，锁文件过期时允许刷新；CI 用 `--locked`，
锁文件过期即失败。`--frozen` 不校验锁文件新鲜度。

### 3.5 CI 安装路径（ADR-008）

| Job | 安装与执行 |
|---|---|
| `lint` | `uv sync --locked --extra dev` + `uv run --locked ruff ...` |
| `test` | `uv sync --locked --extra dev --extra webapp` + `uv run --locked mypy/pytest` |
| `eval-offline` | `uv sync --locked` + `uv run --locked agr-run-cases ...` |

`astral-sh/setup-uv` 钉版本 `0.13.6`，缓存键为 `uv.lock`。
Node 仅用于钉版 TypeScript 校验；前端无 npm 工程。

### 3.6 文档单一事实源

[README](../../README.md) 提供快速开始，AGENTS / CLAUDE / RUNBOOK / Web 指南
统一引用 `scripts/setup.sh` 与 `scripts/doctor.py`。历史安装方案见
[CHANGELOG](../../CHANGELOG.md#setup-ci-doc-migration)。

### 3.7 既有脚本职责

- `scripts/bootstrap_poc.sh` 由 `setup.sh --with-demo-data` 调用，使用 `.venv`
  解释器和 `agr-*` 入口生成演示数据；是否删除见 §8。
- `scripts/verify_all.sh` 运行门禁并写报告，`doctor.py` 负责环境体检。

### 3.8 测试与门禁

`tests/unit/test_setup_consistency.py`（离线、不依赖外部环境变量），断言：

- `.python-version` 存在，且其主次版本满足 `requires-python`；
- `scripts/setup.sh` 存在、可执行、`bash -n` 语法通过；
- `setup.sh` 中出现的每个 `--extra` 名字都存在于 `pyproject.toml` 的 `optional-dependencies`；
- `tests/unit` 与文档中不再出现 `uv pip install -e`（README、AGENTS.md、CLAUDE.md、RUNBOOK、WEBAPP 相关文档）；
- `scripts/doctor.py` 的纯函数在临时目录（无 `.env`、无数据）下返回结构化报告且不抛异常，且 `report_to_exit_code` 对含 error 的报告返回 1。

`ruff check` / `ruff format --check` 覆盖 `scripts/`，新脚本必须过（`check_code_metrics.py` 只扫 `src/agentic_graphrag`，脚本不受 300 行硬限，但仍按同风格写）。

---

## 4. 备选方案与取舍

| 方案 | 取舍 |
|---|---|
| Makefile / justfile / Taskfile | 需新增工具与文档面；`make` 目标无法优雅表达"锁文件过期则失败"这类条件。bash 入口已足够，且 CI 与本地可复用同一脚本。**不采用** |
| devcontainer / Nix | 解决的是"宿主机无 Python/uv"，但仍需首次安装 Docker/Nix；与"离线默认、零外部依赖"的项目基调冲突。**不采用** |
| nox / tox | 为多环境矩阵而生，本项目只有一个解释器版本。**不采用** |
| pip-tools / `pip install --require-hashes` | 与已入库的 `uv.lock` 重复；uv 已是事实标准工具。**不采用** |
| 把 `dev` 迁到 `[dependency-groups]` | uv 的现代写法，但会同时破坏 `.[dev]` / `.[dev,webapp]` 全部文档与 CI 语句，且与 `pip install -e ".[dev]"` 的既有用户习惯断裂。**本轮不做**，见 §8 |
| 保持现状 + 只修文档 | 不解决 S-01/S-02/S-03（可复现性问题）与 S-05（可诊断性）。**不采用** |

---

## 5. 风险

| 风险 | 缓解 |
|---|---|
| `uv lock` 刷新可能顺带改动版本 | 使用不带 `--upgrade` 的 `uv lock`（保留已锁定版本）；刷新后跑全量单测 + 离线评测下限确认无回归 |
| `uv sync` 默认精确同步会删掉环境里手工装的包 | 提供 `--inexact`；文档说明默认行为 |
| 体检脚本被误当作门禁 | 命名 `doctor`（诊断）而非 `check`；`--strict` 才升级 warn；门禁仍是 `verify_all.sh` + CI |
| `.python-version` 入库后 CI/开发强制 3.12 | 与 CI 既有的 `PYTHON_VERSION: "3.12"` 一致，属对齐而非变更 |

---

## 6. 实施步骤与验收

1. ADR-008（tech-stack.md）→ 2. `.python-version` + `.gitignore` → 3. `uv lock` → 4. `scripts/doctor.py` → 5. `scripts/setup.sh` → 6. `bootstrap_poc.sh` 修复 → 7. 文档同步 → 8. CI 改造 → 9. 测试 → 10. 验证。

**验收标准**

- 全新克隆上 `./scripts/setup.sh` 一步得到可跑环境；重复执行不报错、不覆盖 `.env`。
- `python scripts/doctor.py` 在依赖齐全时报 0，在缺依赖/缺 key/错 profile 时报 1 并给出修复命令。
- `uv lock --check` 通过；`uv sync --locked --extra dev --extra webapp` 在本地成功。
- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80`、`ruff check/format`、`mypy`、`python scripts/check_code_metrics.py` 全绿。
- 离线评测下限不回归：`agr-run-cases --no-llm --memory-graph` ≥ 30/31（poc）、≥196/200（g2）。

---

## 7. 当前实现与验证边界

依赖体检优先使用 `importlib.metadata`，再以 `find_spec` 兜底，以支持只有类型存根的包。
CI 解释器由 `.python-version` 和 uv 决定。体检分别报告可选 `otel` 与
`sqlite-checkpoint` 依赖；安装后的导入与运行仍需在对应环境验证。

`uv sync --locked` 会在锁文件过期时失败；`--frozen` 不校验锁文件的新鲜度。
本地初始化、幂等性、锁定安装与远端 CI 的验证结果见
[CHANGELOG](../../CHANGELOG.md#setup-implementation)。

仍需部署验证：Neo4j / Qdrant 在线探活与失败分支，以及可选 OTel / SQLite saver
安装后的导入和连接生命周期。

## 8. 后续可选事项（不在本轮）

- 将 `dev` 依赖迁移到 `[dependency-groups]`（uv 原生），同步改全部文档与 CI。
- 评估删除 `scripts/bootstrap_poc.sh`（一旦 `setup.sh --with-demo-data` 稳定覆盖其职责）。
- devcontainer（仅在需要"宿主机零依赖"时评估）。
