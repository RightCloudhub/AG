# 项目初始化（Setup）设计与优化

**状态**：已实施（设计先行，实施记录见 §7）
**日期**：2026-11-02
**范围**：开发者环境初始化（解释器、依赖、`.env`、体检、文档入口）、锁文件与 CI 安装路径
**关联**：[tech-stack.md ADR-008](../../plan/engineering/tech-stack.md)、[rules.md §7](../../plan/engineering/rules.md)、[RUNBOOK](../ops/RUNBOOK.md)、[repo-structure.md](../../plan/engineering/repo-structure.md)

---

## 1. 背景与现状

仓库当前的「初始化」没有单一入口，步骤以命令片段形式散落在多份文档中；依赖安装一律走 `uv pip install -e`（pip 兼容接口），**不读取已入库的 `uv.lock`**，因此"锁定文件"对开发与 CI 都不生效。

### 1.1 现有入口盘点

| 位置 | 现有做法 | 问题 |
|---|---|---|
| `README.md` 快速开始 | `uv venv .venv && source .venv/bin/activate` → `uv pip install -e ".[dev]"` | 不含 webapp/otel；与 AGENTS.md 写法不同 |
| `CLAUDE.md` 命令段 | `uv pip install -e ".[dev]"` | 缺 venv 创建步骤 |
| `AGENTS.md` | `uv pip install -e ".[dev]"` | 同上 |
| `docs/product/WEBAPP_GUIDE.md`、`docs/ops/WEBAPP.md` | `uv venv .venv` → `uv pip install -e ".[dev,webapp]"` | 第三套写法 |
| `docs/ops/RUNBOOK.md` | 「服务启动」直接从 `source .venv/bin/activate` 起 | 未覆盖环境创建 |
| `.github/workflows/ci.yml` | `pip install -e ".[dev,webapp]"` / `pip install -e .` | 不使用锁文件，CI 版本集与本地可不同 |
| `scripts/verify_all.sh` | 门禁包（lint/测试/评测/报告），非体检 | 重、写 `reports/`，不是"环境是否就绪"的正确工具 |
| `scripts/bootstrap_poc.sh` | ingest → seed 图 → BM25 索引 | 全仓无任何引用；使用裸 `python`（不保证是 `.venv`） |

### 1.2 缺陷清单

| 编号 | 缺陷 | 证据 |
|---|---|---|
| S-01 | 锁文件形同虚设：安装语句全走 `uv pip install -e` / `pip install -e`，均不读 `uv.lock` | `grep -rn 'uv pip install -e'` 命中 README、CLAUDE.md、AGENTS.md、WEBAPP_GUIDE.md、WEBAPP.md；CI 两处 `pip install -e` |
| S-02 | 锁文件已过期：`uv.lock` 落后于 `pyproject.toml` | `uv lock --check` → `The lockfile at uv.lock needs to be updated`（lock 2026-10-24，pyproject 2026-11-01） |
| S-03 | Python 版本未钉住，且被显式忽略：无 `.python-version`，`.gitignore` 中含 `.python-version` | 本机系统 Python 3.14.9，`.venv` 是 uv 托管的 3.12.13；新机器 `uv venv` 会造出 3.14 环境 |
| S-04 | 可选依赖组无统一入口：`.[dev]` / `.[dev,webapp]` / `.[otel]` 三种组合散落 9 处 | `grep -rn 'pip install -e "'` |
| S-05 | 无环境体检：解释器版本、依赖、`.env`、seed 数据、在线后端可达性全靠报错反推 | 无 `doctor` 类脚本 |
| S-06 | `.env` 靠手工复制，且 `AGR_*` 的"写进 `.env` 不生效"是常见坑 | `.env.example` 头部注释要求手工 `set -a; . ./.env; set +a` |
| S-07 | `scripts/bootstrap_poc.sh` 为死代码：无引用、裸 `python`、绕过 `agr-*` 入口、结尾提示 `Run: pytest -q` | `grep -rn bootstrap_poc` 仅命中自身 |
| S-08 | 文档分散导致漂移：同一件事 6 份文档、3 种写法 | §1.1 |

---

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

### 3.2 `scripts/setup.sh`（新增，单一入口）

| 参数 | 作用 | 默认 |
|---|---|---|
| `--extras LIST` | 逗号分隔的可选依赖组 | `dev,webapp`（完整单测与 Web 应用所需，见 CLAUDE.md） |
| `--all-extras` | 安装全部可选依赖 | 关 |
| `--python VERSION` | 覆盖解释器版本 | 读 `.python-version` |
| `--locked` | 严格使用已提交锁文件，锁文件过期即失败（uv 的 `--frozen` 不做此校验，见 §7.1） | 关（开发默认允许顺带刷新锁文件） |
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

### 3.3 `scripts/doctor.py`（新增，体检）

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

### 3.4 Python 版本与锁文件

- 新增 `.python-version` = `3.12`，并**从 `.gitignore` 移除**该行（当前被忽略，无法入库）。`requires-python = ">=3.12"` 保持；`uv` 据此选择解释器，`doctor` ENV-02 校验二者一致。
- `uv.lock` 用 `uv lock` 刷新（保留已锁定版本，只补齐 `pyproject` 变更），入库。实测刷新前锁文件缺少整个 `webapp` 组（`uv lock` 新增 sqlalchemy / pyjwt / psycopg 等 5 项），即 S-02 已实际造成 CI 与本地版本漂移。此后开发默认 `uv sync`（锁文件过期时顺带刷新并提示），CI 用 `--locked`（过期即失败，缺陷 S-02 不会再现；uv 的 `--frozen` 实测不校验时效，见 §7.1）。

### 3.5 CI 对齐（需 ADR-008）

| Job | 现状 | 改为 |
|---|---|---|
| `lint` | `pip install ruff` | `uv sync --locked --extra dev` + `uv run --locked ruff ...` |
| `test` | `pip install -e ".[dev,webapp]"` | `uv sync --locked --extra dev --extra webapp` + `uv run --locked mypy/pytest` |
| `eval-offline` | `pip install -e .` | `uv sync --locked` + `uv run --locked agr-run-cases ...` |

`astral-sh/setup-uv` 钉版本（`0.13.6`，与本地一致），缓存键为 `uv.lock`。Node 侧不变（仍是临时钉版 `npx tsc`，无 npm 工程）。理由是 rules.md §7「改技术选型先写 ADR」：把"依赖解析与安装工具"从 pip 明确收敛到 uv，属于技术选型变更，故先落 ADR-008 再改 CI。

### 3.6 文档单一事实源

| 文档 | 改法 |
|---|---|
| `README.md` 快速开始 | 改为 `./scripts/setup.sh` 两行 + 后续命令；保留 Web 应用变体（`./scripts/setup.sh --extras dev,webapp`） |
| `CLAUDE.md` `AGENTS.md` | 安装语句替换为 `./scripts/setup.sh` / `uv sync --extra dev`，并注明可跑 `python scripts/doctor.py` |
| `docs/ops/RUNBOOK.md` | 「服务启动」前补"环境准备见 README 快速开始 / `scripts/setup.sh`"；`.[otel]` 写法改成 `./scripts/setup.sh --extras dev,otel` |
| `docs/ops/WEBAPP.md`、`docs/product/WEBAPP_GUIDE.md` | 安装段收敛为 `./scripts/setup.sh --extras dev,webapp` |
| `docs/README.md` | 索引新增本文件 |
| `plan/engineering/repo-structure.md` | `scripts/` 说明补 `setup.sh`（环境）/`doctor.py`（体检） |

### 3.7 既有脚本处置

- `scripts/bootstrap_poc.sh`：保留并修复——改用 `.venv/bin/python`（存在时）或 `python3`、改用 `agr-ingest` / `agr-build-graph` / `agr-index` 控制台入口、结尾提示改为 `agr-run-cases --no-llm`；由 `setup.sh --with-demo-data` 调用，从而不再是死代码。（删除是可选后续项，见 §8。）
- `scripts/verify_all.sh`：不动（它是门禁包，职责不同）。已在设计上避免与 `doctor.py` 重叠：门禁跑测试并写报告，体检只读环境。

### 3.8 测试与门禁

新增 `tests/unit/test_setup_consistency.py`（离线、不依赖外部环境变量），断言：

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
| CI 改用 uv 后无法本地验证 GitHub 运行 | 本地验证等价命令（`uv sync --locked --extra dev --extra webapp`）与 YAML 语法；在实施记录中如实标注"CI 侧未经远端运行验证" |
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

## 7. 实施记录（2026-11-02）

**新增**：`scripts/setup.sh`（单一入口）· `scripts/doctor.py`（体检，17 项检查）· `.python-version`（`3.12`）·
`tests/unit/test_setup_consistency.py`（12 条守卫）· 本设计文档。
**修改**：`uv.lock`（刷新，补入整个 `webapp` 组）· `.gitignore`（不再忽略 `.python-version`）·
`scripts/bootstrap_poc.sh`（改用 `.venv` 解释器与控制台入口）· `.github/workflows/ci.yml`（三个 job 改 uv + 锁文件）·
`.env.example`、`README.md`、`AGENTS.md`、`CLAUDE.md`、`docs/README.md`、`docs/ops/{RUNBOOK,WEBAPP}.md`、
`docs/product/WEBAPP_GUIDE.md`、`plan/engineering/{rules,repo-structure,tech-stack}.md`、`docs/reviews/IMPORTANT.md`。

### 7.1 验证结果

| 验证 | 命令 | 结果 |
|---|---|---|
| 锁文件生效 | `uv lock --check` | 通过（刷新前为 `needs to be updated`） |
| 一键初始化 | `./scripts/setup.sh --locked` | 通过；随后 `doctor` 报 `error=0 warn=0` |
| 幂等性 | 连续两次运行，比对 `.env` 摘要 | `.env` 摘要不变；第二次输出「已存在 .env，未改动」 |
| 环境体检 | `.venv/bin/python scripts/doctor.py` | 全部 ok/info；`--json` 输出可解析（见测试） |
| 锁文件过期即失败 | 临时目录副本中新增一条依赖后 `uv lock --check` / `uv sync --locked` | 两者均退出 1 并提示 `The lockfile … needs to be updated` |
| 反例（重要） | 同一副本执行 `uv sync --frozen` | **不报错**，反而按新依赖解析并开始安装 → CI/脚本改用 `--locked`（见 §7.2） |
| 数据引导 | `bash scripts/bootstrap_poc.sh` | 通过；`git status data/` 无跟踪文件变更 |
| 单测 + 覆盖率 | `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80` | 627 passed，覆盖率 89.56% |
| 新增守卫测试 | `pytest tests/unit/test_setup_consistency.py -q` | 14 passed（含 CI 必须使用 `--locked` 的守卫） |
| 类型 / Lint / 指标 | `mypy` · `ruff check` · `ruff format --check` · `check_code_metrics.py` | 全部通过（293 文件 / 407 文件 / src 全量） |
| 离线评测下限 | `agr-run-cases --no-llm --memory-graph`（poc 31 / g2 200） | poc 31/31、g2 200/200，均高于下限 30/196 |

### 7.2 与设计的偏差（实现后回填）

| 项 | 设计 | 实现 | 原因 |
|---|---|---|---|
| 依赖检查方式 | 「逐个 `find_spec`」 | `importlib.metadata` 优先，`find_spec` 兜底 | `types-PyYAML` 一类「只装类型存根」的包没有同名可导入模块，`find_spec` 会误报缺失（实测误报 2 项） |
| `setup.sh` 默认可选组 | `dev` | `dev,webapp` | 完整单测与 Web 应用都需要 webapp 组；只装核心依赖改用 `--extras dev`（README 已注明） |
| CI 解释器来源 | 保留 `actions/setup-python` + `PYTHON_VERSION` | 移除 `setup-python`，解释器由 `.python-version` + uv 决定 | 本地与 CI 同源，避免两处版本声明漂移 |
| 体检检查项编号 | DEP-04 含 otel 与 sqlite-checkpoint | 拆为 DEP-04（otel）/ DEP-05（sqlite-checkpoint） | 分组报告更清晰 |
| 锁校验标志 | CI / `setup.sh` 用 `uv sync --frozen` | 一律改 `--locked`（`setup.sh` 参数名同步改为 `--locked`） | 对照实验发现 `uv sync --frozen` 在锁文件过期时**不会**失败（退出 0 并继续安装），只有 `--locked` 报错；按原设计与 CI 意图（过期即失败）必须用 `--locked` |

### 7.3 未验证项（如实记录）

- **CI 远端运行**：本地只验证了等价命令（`uv sync --locked --extra dev --extra webapp`）与 YAML 解析；GitHub Actions 实际执行未在本地验证。挂账见 `docs/reviews/IMPORTANT.md` §6。
- **在线后端探活**：`AGR_PROFILE=offline` 下 `LIVE-01` 走跳过分支；Neo4j / Qdrant 的失败分支未在本机验证（本机无 Docker 服务）。
- **`otel` / `sqlite-checkpoint` 组**：未安装，体检按 design 输出 info，未验证安装后的导入。

## 8. 后续可选事项（不在本轮）

- 将 `dev` 依赖迁移到 `[dependency-groups]`（uv 原生），同步改全部文档与 CI。
- 评估删除 `scripts/bootstrap_poc.sh`（一旦 `setup.sh --with-demo-data` 稳定覆盖其职责）。
- devcontainer（仅在需要"宿主机零依赖"时评估）。
