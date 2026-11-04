# CHANGELOG

本文件集中记录历史变更、旧状态快照与当时的验证结果。正文中的日期、代码行号、
测试数量及未完成声明均属于原始记录；含后续批注的原文按其起始审查日期归档。
仅注明月份的记录不推定具体日期。归档正文的章节编号仍对应原文。
当前行为见 [文档索引](docs/README.md)，当前缺口与验收边界见
[IMPORTANT.md](docs/reviews/IMPORTANT.md)。

更新约定：文档和状态表只维护最新结论；变更过程、旧指标与关闭记录写在这里。

## 2026-11-04

### 图谱双模、概括问答与对外接入

实现及验收：[GRAPH_PLATFORM_EXTENSION.md](docs/reviews/GRAPH_PLATFORM_EXTENSION.md)。
运行与恢复：[GRAPH_PLATFORM.md](docs/ops/GRAPH_PLATFORM.md)。

- 知识库作用域贯穿存储、任务、查询、缓存、来源和审计；默认库兼容旧请求，完整 shadow
  generation、持久化写入意图、CAS 发布、恢复与删除优先的回滚落地。
- 开放实体/关系抽取、描述/向量/别名消歧、LLM 登记表规划、Schema 必审候选与可恢复写回；
  新 Schema 通过人工草案审核和影子升级，不自动启用旧公司域规则。
- 文档/社区摘要、Leiden 与受影响社区重算、Global map-reduce/verifier、子集覆盖和 partial
  结果；七角色模型目录、能力测试、草稿/版本发布与管理界面。
- typed REST/OpenAPI/SSE、版本化原文 resolver、查询/独立检索审计回执、取消及预算结算；
  独立 SDK/MCP 的锁文件、类型/覆盖率/打包和 CI job。
- 修复摘要刷新/卸载/切库竞争、Unicode 原文分页、删除终态清理与后台任务恢复边界。
  检索按单次遍历复用已评分邻接层，图读取保留存储隔离；同次取证复用重复文档读取，
  后续请求重新检查，删除/新增不会使用过期读取结果。
- 复审修复：原文解析失败仍按 citation 检查删除并保存撤回谱系；自由字典使用兼容输出模式；
  必审批次在图重启后恢复已批准事实，retry/reconcile 可继续发布；Open 库拒绝 Schema-only
  必审策略；普通配置保存保留固定模型 revision；缓存流复用响应头中的 query ID 和审计回执。

验证：核心 **1169 passed / 4 既有 xfailed / 91.82%**；Ruff、format、strict mypy、
代码指标、checkJs、Vue 模板及契约漂移检查通过。SDK **39 / 98.90%**，
MCP **22 / 95.06%**；独立 wheel 环境与官方 MCP 客户端完成完整 stdio/HTTP 取证。
浏览器在 1440/768/390 下保存 23 张最终场景截图，0 FAIL、12 已说明的 WARN、无溢出或控制台错误。
同日复审后：核心 **1185 passed / 4 xfailed / 91.88%**；六项修复及三张设置控件回归截图
见交付验收第 5 节，独立客户端与离线下限复跑通过。

离线：POC/triage 各 **31/31**，G2 **200/200**，probes **54/54、20/20**，
dev **153/153**，金标 **0 flagged**。最终同图配对基线/候选均 200/200，McNemar **p=1**，
chain P95 **65→64 ms**、LLM 调用均值 **0→0**，不声称效果提升。
另一轮完整服务调用（含原文/审计持久化、不含 HTTP）P95 **61.77→65.90 ms**，
明确保留新增耐久取证的开销，不能把 chain 计时当端到端延迟。

18 项中 14 项工程完成，P5-EXT-01/15/17/18 保留真实领域基线、复核校准、实际供应商、
Claude/Cursor 和目标部署/容量验收。ADR-010～012、Spec、README、CLAUDE、路线图及
IMPORTANT 同步。SQLite 单主机、CAS 不自动 rebase、CLI operation ID 全局唯一、
缺 embedder 的变更描述待 reindex，以及无 SSE 持久化重放等差异均已登记。
基线早于契约导出，机械旧版 schema 对比此次跳过；本地通过不表示远端 CI 或产品门禁通过。

## 2026-11-03

<a id="important-snapshot-13"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**AgentState v3 类型重构（已实施）**：chain / evidence / register / memory 直接传递对象，`RegisterRuntimeState` 汇集旧登记表通道，追加 reducer、先复制再修改、v1/v2 checkpoint 迁移及边界序列化已交付；651 单测通过 / 覆盖率 89.74%，336 条离线报告与基线一致；四跳基准与完整验证记录见 [`TYPED_AGENT_STATE_PR.md`](docs/reviews/TYPED_AGENT_STATE_PR.md)，架构同步 §2。

<a id="integration-updates"></a>

### 登记表、重排、解析、摄取与改写鲁棒性

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

- [x] 2026-11-03 需求登记表边界修复：旧支撑引用永不删除、Verifier 独立模型配置与并发校验、累计否决退休及审计、跨跳收敛提醒与异常 WARNING；默认关闭及 `--no-llm` 不变；696 单测通过、覆盖率 89.53%、Ruff/mypy/代码指标通过，离线 G2 基线/候选均 200/200（McNemar p=1）；真实模型效果仍待配对评估。
- [~] 2026-11-03 cross-encoder 重排：已补齐池扩展、批处理/截断/重试/熔断、图策略与 run 内缓存；无 TEI/live 预算，保持默认 none，待真实准确率与 P95 验收，见 [rerank_eval.md](reports/rerank_eval.md)。
- [x] 2026-11-03 BL-08：统一解析端口、签名/编码校验、MinerU 适配器及含 breadcrumb 的 token 分块已完成；714 单测、89.82% 覆盖率，五组离线评测均无分数变化（0 pp），CI 下限未改；见 [验证记录](docs/reviews/INGEST_PARSE_CHUNK.md)。
- 2026-11-03：摄取新增 UUID/版本/hash 去重、SQLite chunk checkpoint、partial/reconcile、租户隔离删除、批量 embedding 重试、模型 reindex 与断点续传；旧 ID 迁移和 live 模型切换操作见 RUNBOOK，离线 adapter 回归不替代 Neo4j/Qdrant 线上验收。
- 2026-11-03 [~] Paraphrase robustness: 40 dev groups + 2 graph-verified 4-hop supplement groups; Chinese 20%→95%, typos 7.5%→97.5%, strict consistency 0%→7.5%; four planner xfails and reordered/casual gaps remain; live skipped (no key). [Results and validation](reports/paraphrase_robustness.md).

<a id="runtime-change-6"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

增加可选 TEI rerank profile、CPU 镜像与模型卷，记录重排故障回退、本地适配器及未通过 live 验收的默认关闭策略

## 2026-11-02

<a id="important-snapshot-12"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**文档同步（代码复核）**：BL-01/03/12/13/14 状态由「挂账」改为已解挂（对齐 `BUSINESS_LOGIC.md` §5）；`ARCH_OPTIMIZATION_V2.md` 标注当时的离线基线与现 CI 门禁差异；`DESIGN_VS_IMPLEMENTATION.md` 增补 D1–D5 复核；`EXTERNAL_RUNTIMES.md` §3.1 改为记录静态 ESM 加载；`evaluation.md` / `repo-structure.md` / `cicd-observability.md` / `testing-strategy.md` / `ARCHITECTURE.md` / `RUNBOOK.md` / `README.md` / `CLAUDE.md` 按当前代码校正

<a id="setup-ci"></a>

### uv 安装路径的远端 CI 验证

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

| 项 | 状态 | 验证记录 | 解挂需要 |
|---|---|---|---|
| CI 改用 uv 的远端验证 | `[x]` | 2026-11-02 推送后 GitHub Actions run [41475678852](https://github.com/RightCloudhub/AG/actions/runs/41475678852) 三个 job（lint / 单测+覆盖率 / 离线评测）全绿；本地此前已用等价命令验证（`uv sync --locked --extra dev --extra webapp`）| — |

<a id="runtime-change-5"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

文档同步：§3.1 改为记录当前加载方式（静态 ESM import，无 CDN 兜底与 `VUE_VERSION`），修正 vendor 说明文档相对链接

<a id="setup-original-audit"></a>

### 初始化入口与锁文件的原始问题

来源：[docs/design/SETUP_DESIGN.md](docs/design/SETUP_DESIGN.md)。

#### 1. 背景与现状

仓库当前的「初始化」没有单一入口，步骤以命令片段形式散落在多份文档中；依赖安装一律走 `uv pip install -e`（pip 兼容接口），**不读取已入库的 `uv.lock`**，因此"锁定文件"对开发与 CI 都不生效。

##### 1.1 现有入口盘点

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

##### 1.2 缺陷清单

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

<a id="setup-implementation"></a>

### 初始化实现、设计偏差与验证

来源：[docs/design/SETUP_DESIGN.md](docs/design/SETUP_DESIGN.md)。

#### 7. 实施记录（2026-11-02）

**新增**：`scripts/setup.sh`（单一入口）· `scripts/doctor.py`（体检，17 项检查）· `.python-version`（`3.12`）·
`tests/unit/test_setup_consistency.py`（12 条守卫）· 本设计文档。
**修改**：`uv.lock`（刷新，补入整个 `webapp` 组）· `.gitignore`（不再忽略 `.python-version`）·
`scripts/bootstrap_poc.sh`（改用 `.venv` 解释器与控制台入口）· `.github/workflows/ci.yml`（三个 job 改 uv + 锁文件）·
`.env.example`、`README.md`、`AGENTS.md`、`CLAUDE.md`、`docs/README.md`、`docs/ops/{RUNBOOK,WEBAPP}.md`、
`docs/product/WEBAPP_GUIDE.md`、`plan/engineering/{rules,repo-structure,tech-stack}.md`、`docs/reviews/IMPORTANT.md`。

##### 7.1 验证结果

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

##### 7.2 与设计的偏差（实现后回填）

| 项 | 设计 | 实现 | 原因 |
|---|---|---|---|
| 依赖检查方式 | 「逐个 `find_spec`」 | `importlib.metadata` 优先，`find_spec` 兜底 | `types-PyYAML` 一类「只装类型存根」的包没有同名可导入模块，`find_spec` 会误报缺失（实测误报 2 项） |
| `setup.sh` 默认可选组 | `dev` | `dev,webapp` | 完整单测与 Web 应用都需要 webapp 组；只装核心依赖改用 `--extras dev`（README 已注明） |
| CI 解释器来源 | 保留 `actions/setup-python` + `PYTHON_VERSION` | 移除 `setup-python`，解释器由 `.python-version` + uv 决定 | 本地与 CI 同源，避免两处版本声明漂移 |
| 体检检查项编号 | DEP-04 含 otel 与 sqlite-checkpoint | 拆为 DEP-04（otel）/ DEP-05（sqlite-checkpoint） | 分组报告更清晰 |
| 锁校验标志 | CI / `setup.sh` 用 `uv sync --frozen` | 一律改 `--locked`（`setup.sh` 参数名同步改为 `--locked`） | 对照实验发现 `uv sync --frozen` 在锁文件过期时**不会**失败（退出 0 并继续安装），只有 `--locked` 报错；按原设计与 CI 意图（过期即失败）必须用 `--locked` |

##### 7.3 未验证项（如实记录）

- ~~**CI 远端运行**~~ **已关闭**：2026-11-02 推送后 run [41475678852](https://github.com/RightCloudhub/AG/actions/runs/41475678852) 三个 job 全绿（lint / 单测+覆盖率 / 离线评测），`uv sync --locked` 与 `uv run --locked` 在 GitHub 托管 runner 上均正常。此前本地只验证过等价命令与 YAML 解析。
- **在线后端探活**：`AGR_PROFILE=offline` 下 `LIVE-01` 走跳过分支；Neo4j / Qdrant 的失败分支未在本机验证（本机无 Docker 服务）。
- **`otel` / `sqlite-checkpoint` 组**：未安装，体检按 design 输出 info，未验证安装后的导入。

<a id="webapp-validation"></a>

### 多租户 Web 离线流程验证

来源：[docs/ops/WEBAPP.md](docs/ops/WEBAPP.md)。

#### 验证记录

分步提交和实测结果记录在 [多租户实施记录](plan/workstreams/multitenant-webapp.md)。
使用指南的离线流程已用临时 SQLite 和运行目录验证：注册、空间切换、导入两份样例、
开发邮件链接和服务密钥均通过；`doc_01` / `doc_02` 入图后，快速与多跳问答均返回 Elena Varga。
SQLite 单测不代表 PostgreSQL 或真实图／向量后端已完成部署验证。

<a id="setup-version-history"></a>

### 初始化版本与锁文件调整

来源：[docs/design/SETUP_DESIGN.md](docs/design/SETUP_DESIGN.md)。

##### 3.4 Python 版本与锁文件

- 新增 `.python-version` = `3.12`，并**从 `.gitignore` 移除**该行（当前被忽略，无法入库）。`requires-python = ">=3.12"` 保持；`uv` 据此选择解释器，`doctor` ENV-02 校验二者一致。
- `uv.lock` 用 `uv lock` 刷新（保留已锁定版本，只补齐 `pyproject` 变更），入库。实测刷新前锁文件缺少整个 `webapp` 组（`uv lock` 新增 sqlalchemy / pyjwt / psycopg 等 5 项），即 S-02 已实际造成 CI 与本地版本漂移。此后开发默认 `uv sync`（锁文件过期时顺带刷新并提示），CI 用 `--locked`（过期即失败，缺陷 S-02 不会再现；uv 的 `--frozen` 实测不校验时效，见 §7）。

<a id="setup-ci-doc-migration"></a>

### 初始化 CI、文档与脚本调整方案

来源：[docs/design/SETUP_DESIGN.md](docs/design/SETUP_DESIGN.md)。

##### 3.5 CI 对齐（需 ADR-008）

| Job | 现状 | 改为 |
|---|---|---|
| `lint` | `pip install ruff` | `uv sync --locked --extra dev` + `uv run --locked ruff ...` |
| `test` | `pip install -e ".[dev,webapp]"` | `uv sync --locked --extra dev --extra webapp` + `uv run --locked mypy/pytest` |
| `eval-offline` | `pip install -e .` | `uv sync --locked` + `uv run --locked agr-run-cases ...` |

`astral-sh/setup-uv` 钉版本（`0.13.6`，与本地一致），缓存键为 `uv.lock`。Node 侧不变（仍是临时钉版 `npx tsc`，无 npm 工程）。理由是 rules.md §7「改技术选型先写 ADR」：把"依赖解析与安装工具"从 pip 明确收敛到 uv，属于技术选型变更，故先落 ADR-008 再改 CI。

##### 3.6 文档单一事实源

| 文档 | 改法 |
|---|---|
| `README.md` 快速开始 | 改为 `./scripts/setup.sh` 两行 + 后续命令；保留 Web 应用变体（`./scripts/setup.sh --extras dev,webapp`） |
| `CLAUDE.md` `AGENTS.md` | 安装语句替换为 `./scripts/setup.sh` / `uv sync --extra dev`，并注明可跑 `python scripts/doctor.py` |
| `docs/ops/RUNBOOK.md` | 「服务启动」前补"环境准备见 README 快速开始 / `scripts/setup.sh`"；`.[otel]` 写法改成 `./scripts/setup.sh --extras dev,otel` |
| `docs/ops/WEBAPP.md`、`docs/product/WEBAPP_GUIDE.md` | 安装段收敛为 `./scripts/setup.sh --extras dev,webapp` |
| `docs/README.md` | 索引新增本文件 |
| `plan/engineering/repo-structure.md` | `scripts/` 说明补 `setup.sh`（环境）/`doctor.py`（体检） |

##### 3.7 既有脚本处置

- `scripts/bootstrap_poc.sh`：保留并修复——改用 `.venv/bin/python`（存在时）或 `python3`、改用 `agr-ingest` / `agr-build-graph` / `agr-index` 控制台入口、结尾提示改为 `agr-run-cases --no-llm`；由 `setup.sh --with-demo-data` 调用，从而不再是死代码。（删除是可选后续项，见 §8。）
- `scripts/verify_all.sh`：不动（它是门禁包，职责不同）。已在设计上避免与 `doctor.py` 重叠：门禁跑测试并写报告，体检只读环境。

<a id="setup-ci-original-risk"></a>

### CI 安装迁移的原始验证风险

来源：[docs/design/SETUP_DESIGN.md](docs/design/SETUP_DESIGN.md)。

| CI 改用 uv 后无法本地验证 GitHub 运行 | 本地验证等价命令（`uv sync --locked --extra dev --extra webapp`）与 YAML 语法；在实施记录中如实标注"CI 侧未经远端运行验证" |

## 2026-10-31

<a id="important-snapshot-7"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**Web 聊天界面三栏 + 分步向导改版（已实施）**：左侧会话历史 / 中间对话流 / 右侧「推理链 · 证据 · 子图」工作区；提问向导、澄清卡片、分步进度条、引用角标定位、追问与反馈原因、知识导入向导；文案集中 `web/static/js/i18n.js`。后端缺失的 5 个接口以 `FeatureFlag` 关闭并降级，契约见 [`plan/workstreams/api-and-ui.md`](plan/workstreams/api-and-ui.md) §2.8；差异与挂账见 §6「前端向导改版」

<a id="important-snapshot-8"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**Web「推理笔记本」v2 重设计（已实施，同日）**：按 `Prompt.md` 放弃左导航 + 单栏聊天，改为 顶栏 + 笔记本栏 + 问题页（标题 → HopRail → 结论与引用 → 元信息）+ 检查器（证据 / 子图 / 日志）；靛夜 / 晨纸双主题与六跳色令牌；命令面板、`@` / `/` 菜单、快捷键；管理视图迁入 `js/admin/`。按 `design-taste-frontend` app-screen 流程实施并在 headless Chromium 中实测 0 FAIL；视觉规范 [`docs/design/UI_DESIGN.md`](docs/design/UI_DESIGN.md) §10

<a id="important-snapshot-9"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**Web 图标与动效对齐参考 demo（已实施，同日）**：图标改用 Lucide 几何（vendored 子集 `web/static/vendor/lucide-icons.js`，由 `scripts/build_lucide_icons.mjs` 从 `lucide-static` 1.65.0 生成，ISC，名称表为唯一来源），品牌标记 `mark` 为唯一自绘字形；动效采用 demo 词表 `enter-note` / `enter-node`（跳节点 55ms 级联）/ `panel-in` / `breathe` / `spin`，`prefers-reduced-motion` 下只留透明度。22 个状态在 headless Chromium 实测 0 FAIL（对话/抽屉快照按 `--allow covered-text`）；规范同步 `docs/design/UI_DESIGN.md` §10 与 `docs/EXTERNAL_RUNTIMES.md`

<a id="ui-tokens"></a>

### 视觉令牌

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

| 项 | 状态 | 说明 | 解挂需要 |
|---|---|---|---|
| 视觉令牌 | `[x]` | 2026-10-31 v2：按 `Prompt.md`（界面重设计规范 v2）重做为「推理笔记本」，令牌在 `web/static/css/tokens.css`（靛夜 / 晨纸双主题，六跳色）；`docs/design/UI_DESIGN.md` §10 已同步，§1–§8 的「制图室」方向保留为备案 | — |

<a id="ui-validation"></a>

### 浏览器端验证

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

| 项 | 状态 | 说明 | 解挂需要 |
|---|---|---|---|
| 浏览器端验证 | `[x]` | 2026-10-31 v2：`.taste-skill/drive.mjs` 在 headless Chromium 中走查全部交互状态并留存截图（`.taste-skill/v2-*`、`r2-*`）；`design-taste-frontend/inspect.mjs --app` 对实时首屏与状态快照 0 FAIL；仍未做真机触控验证 | 真机触控专项（随移动端 PRD 决议） |

<a id="runtime-change-4"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

Web 图标：§3.2 记录 Lucide 1.65.0（ISC）vendored 子集与生成器 `scripts/build_lucide_icons.mjs`，对齐 `Prompt.md` §3.3

<a id="ui-browser-results"></a>

### 推理笔记本浏览器验证

来源：[docs/design/UI_DESIGN.md](docs/design/UI_DESIGN.md)。

- **校验**：`node .claude/skills/design-taste-frontend/scripts/lint.mjs --app web/` 0 FAIL；`inspect.mjs --app` 在 1440 / 768 / 390 下对实时首屏与各状态快照（暗 / 亮）均 0 FAIL（WARN 逐条辩护见 notes）；`.taste-skill/drive.mjs` 在 headless Chromium 中走完 欢迎 / 流式 / 回答 / 引用定位 / 跳筛选 / 悬停 / 子图 / 日志 / 反馈 / 改写 / 未找到答案 / 澄清 / 斜杠命令 / 向导 / 命令面板 / 设置 / 收起 / 1200 抽屉 / 768 图标栏 / 390 抽屉 / 知识导入 / 图谱 / 审核 / 运维 并留存截图与静态快照；`tsc` 严格检查、`scripts/check_web_templates.mjs`、`tests/unit/test_web_claude_ui.py`、`tests/unit/test_web_wizard_ui.py` 全绿。

<a id="ui-notebook-design"></a>

### 推理笔记本重设计

来源：[docs/design/UI_DESIGN.md](docs/design/UI_DESIGN.md)。

依据 `Prompt.md`（界面重设计规范 v2）重做：放弃「左导航 + 单栏聊天」，聊天窗口成为「推理笔记本」——每个问题一页，自上而下为 问题 → 推理路径 → 结论与证据。本轮按 `design-taste-frontend` 的 app-screen 流程实施，并用其检查器在真实浏览器中实测。

## 2026-10-24

<a id="frontend-test-recovery"></a>

### 前端结构测试恢复与后续验证

来源：[docs/design/REFACTOR_PLAN.md](docs/design/REFACTOR_PLAN.md)。

- 前端结构测试的既有失败已解决：`test_web_claude_ui.py` 的 7 个用例曾因期望的文件随 P5-UI-01 变更而失败；该文件已在类型审查（TS-006，2026-10-24）重写并恢复进 CI，2026-10-31 v2 结构断言（`test_web_claude_ui.py` + `test_web_wizard_ui.py`）全绿。

<a id="type-audit-validation"></a>

### 类型审查验证、截图与离线配对比较

来源：[docs/reviews/TYPE_SAFETY_AUDIT.md](docs/reviews/TYPE_SAFETY_AUDIT.md)。

#### 最终验证结果

| 验证 | 命令或证据 | 结果 |
| --- | --- | --- |
| Python 类型 | `.venv/bin/python -m mypy` | 247 个源文件通过 |
| JS 类型 | `node /tmp/ag-type-audit-tools/node_modules/typescript/bin/tsc -p web/jsconfig.json`（5.9.3） | 全部自有 JS 通过 |
| Lint | `.venv/bin/ruff check src tests scripts` | 通过 |
| 格式 | `.venv/bin/ruff format --check src tests scripts` | 335 个文件通过 |
| 代码指标 | `.venv/bin/python scripts/check_code_metrics.py` | 文件/函数/复杂度等全部通过 |
| 单测与覆盖率 | `.venv/bin/pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` | 457 通过，88.04%；1 项第三方弃用提示 |
| 收尾回归 | `test_graph_recursion_fallback.py` 与 `test_static_contracts.py` | 合计 5 项通过 |
| 锁文件 | `uv lock --check` | 通过 |
| 包类型标记 | `uv build --wheel --out-dir /tmp/ag-type-audit-dist`，检查 wheel | 构建通过，包含 py.typed |
| 模型 Schema | 导出 schema 与 `reasoning_chain_json_schema()` 对比 | 一致 |
| 补丁空白 | `git diff --check` | 通过 |
| 浏览器运行 | Chromium 156.0.8078.4 与真实离线 API | 五个视图通过，无页面异常，无外部资源请求；问答证据、任务徽标、审核与运维可用 |

浏览器使用临时工具与运行库、隔离的任务/审核数据和测试身份；应用保持离线，
未连接 LLM/Neo4j/Qdrant。截图：
[聊天与证据](docs/reviews/assets/type-safety-chat.png)、[任务状态与创建时间](docs/reviews/assets/type-safety-knowledge.png)。

#### 离线评测与配对比较

基线版本：`4860b619607132c04151723feb3ef8c2a0398aa1`，归档于
`/tmp/ag-audit-baseline`。G2 两侧使用相同数据集和 pilot 种子图，均无在线 LLM，
没有 LLM 缓存；基线无历史检索索引，候选 eval 显式隔离历史检索索引。

| 指标 | 基线 | 候选 |
| --- | --- | --- |
| G2 正确数 | 167/200 | 167/200 |
| G2 准确率 | 83.5% | 83.5% |
| P95（单次采样） | 101 ms | 82 ms |
| LLM 调用均值 | 0 | 0 |
| POC agentic | 仓库下限 21/31 | 21/31 |
| POC triage | 仓库下限 21/31 | 21/31 |

> **门禁复核（2026-11-02）**：表中「仓库下限」为当时值；当前 CI 门禁为 POC 30/31（agentic 与 triage）、G2 196/200，另加 probe 52/54 与 19/20，金标审计阻塞合并（`.github/workflows/ci.yml`）。

配对：基线独对 0、候选独对 0、同时正确 167、同时错误 33；McNemar p=1，
无翻转案例。结论为 no_significant_difference，不据单次 P95 声称性能提升。
合成离线结果是工程回归证据，不能代替生产/live 或真实试点域验收。

首次候选 157/200 的调查发现工作区旧 bm25.json 与归档基线输入不同；原始代码
加载同一索引，也仅过 10/20 无答案题。隔离索引后重新比较，不将首次输入不一致
的数字作为类型重构退化结论。

本地报告：`reports/type_audit_candidate.jsonl`、`reports/type_audit_poc.jsonl`、
`reports/type_audit_triage.jsonl`、`reports/type_audit_comparison.json`。
reports 目录被仓库忽略，关键结果已在本文件保留。

复现命令（在仓库根目录，激活开发环境）：

```bash
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_all.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl --run-name type_audit_candidate
agr-run-cases --no-llm --memory-graph --cases evals/datasets/poc_cases.jsonl \
  --run-name type_audit_poc
agr-run-cases --no-llm --memory-graph --enable-triage \
  --cases evals/datasets/poc_cases.jsonl --run-name type_audit_triage
python scripts/eval_compare.py \
  /tmp/ag-audit-baseline/reports/type_audit_baseline.jsonl \
  reports/type_audit_candidate.jsonl --cases evals/datasets/g2_all.jsonl \
  --json reports/type_audit_comparison.json --fail-on-worse
```

SHA-256：G2 数据集 e3068b45ddcfc486dccc4569e59cc40b0cc12de81bc5745916439835f582dcb0；
pilot 图 d1f5717fa99e51daf48e6f4633089573bda24eb413ede3683793fdc248a34efe。

<a id="type-audit-acceptance"></a>

### 类型审查原始验收清单

来源：[docs/reviews/TYPE_SAFETY_AUDIT.md](docs/reviews/TYPE_SAFETY_AUDIT.md)。

#### 最终验收

- [x] 静态类型检查通过并接入 CI。
- [x] lint、格式、代码指标与补丁空白检查通过。
- [x] 全量离线单测通过，覆盖率不低于 80%。
- [x] POC agentic/triage 各不低于 21/31；G2 不低于 167/200。（当时门禁；现为 30/31 与 196/200）
- [x] 对效果相关变更完成配对回归，准确率与 McNemar 结果已记录。
- [x] 前端零构建与注入安全断言通过。
- [x] 浏览器验收与截图完成，P0–P7 状态和问题台账已同步。

## 2026-10-23

<a id="register-baseline"></a>

### 需求登记表初始状态与离线基线

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

需求登记表（2026-10-23）：[x] 状态/不变量、实体锚与依赖调度、独立 Verifier
提示、checkpoint/SSE 审计、部分答案与弃答配置、同配置关闭开关、对齐与归因
分析工具；[ ] 真实 LLM 下 HotpotQA / 2Wiki / MuSiQue 各 1000 题、多种子方差
及费用评估；[ ] 关闭不变量的研究消融（产品运行路径始终校验不变量）；
[ ] 四跳覆盖率与 Verifier 误判的真实检索栈评估；[ ] 无证据“最可能答案”策略
需另行确定引用与置信度约束。默认不启用登记表，`--no-llm` 保留原流程。
本次改动前 G2 离线基线 157/200，低于 CI 下限 167/200，属于既有待修复缺口；
不要据此降低门槛。设计与验证记录见
[REQUIREMENT_REGISTER.md](docs/design/REQUIREMENT_REGISTER.md)。

<a id="register-original-validation"></a>

### 需求登记表原始工程验证与门禁复核

来源：[docs/reviews/REQUIREMENT_REGISTER_VALIDATION.md](docs/reviews/REQUIREMENT_REGISTER_VALIDATION.md)。

### 需求登记表工程验证（2026-10-23）

状态：工程实现已验证；真实 LLM 多跳效果评估待完成。默认关闭登记表。

| 验证 | 结果 |
|---|---|
| Ruff lint / format、代码指标、diff whitespace | 通过 |
| CI 范围单测 | 428 passed，覆盖率 ≥87% |
| 完整单测 | 既有前端 7 项失败；CI 已明确排除此模块 |
| POC agentic / triage | 两条路径均 21/31，达到 21/31 门槛 |
| G2 dev，pilot graph，no LLM | 120/153 |
| 改动前 G2 / 改动后默认路径 | 157/200 → 157/200；逐题判分一致，McNemar p=1 |
| 开启登记表配置但禁用 LLM | 157/200；仍走原流程，逐题判分一致，p=1 |

> **门禁与测试复核（2026-11-02）**：上表记录的是当时状态——仓库门槛为 POC 21/31、G2 167/200；现为 POC 30/31、G2 196/200，并加 probe `52/54` 与 `19/20`（`.github/workflows/ci.yml`）。表中「既有前端 7 项失败」已恢复全绿；当前完整单测 627 passed、覆盖率 89.6%（`pytest tests/unit --cov=agentic_graphrag`）。

G2 157/200 低于仓库 167/200 门槛，改动前即存在。此次没有降低门槛或
修改演示答案启发式。默认路径基线 P95 113ms，改动后 106ms；登记表配置
开启但禁用 LLM 的回归为 102ms。这些是单次离线机器测量，不能解释为
登记表带来效果或延迟改善，三次实验的平均 LLM 调用数均为 0。

新增测试覆盖：状态不消失、证据追加、未知引用与无理由退休被拦截、文本和
依赖不可删改、实体锚与依赖就绪、单 chunk 解决多项、独立 Verifier 否决和
故障、更新 JSON 错误、预算中断、去重与已读集合、checkpoint 恢复、SSE、
种子/更新/delta/Verifier 消融开关、部分答案与严格弃答、失败优先级、
gold 模糊对齐、未对齐排除、按 hop 召回、选择性准确率和重复实验方差。

实际验证命令：

```bash
ruff check src tests scripts
ruff format --check src tests scripts
python scripts/check_code_metrics.py
pytest tests/unit --ignore=tests/unit/test_web_claude_ui.py \
  --cov=agentic_graphrag --cov-fail-under=80 -q
agr-run-cases --no-llm --memory-graph --cases evals/datasets/poc_cases.jsonl
agr-run-cases --no-llm --memory-graph --enable-triage --cases evals/datasets/poc_cases.jsonl
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_all.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_dev.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl
PYTHONPATH=src python scripts/eval_compare.py BASE.jsonl CAND.jsonl \
  --cases evals/datasets/g2_all.jsonl
PYTHONPATH=src python scripts/analyze_search_drift.py CAND.jsonl \
  --base BASE.jsonl --cases evals/datasets/g2_all.jsonl --out DRIFT.json
```

评估原始 JSONL 写在 `/tmp/agr-register-*`，避免覆盖仓库已有报告。开启配置
但禁用 LLM 的回归在单独进程中设置
`get_config().guardrails.requirement_register.enabled = True`，其余配置相同。

G2 实体/关系金标未对齐为段落，因此新增分析正确报告段落召回 null、
180 题未对齐；LLM 判卷未执行，LLM-Eval 为 null、样本数 0。尚未运行
HotpotQA / 2Wiki / MuSiQue 三个各 1000 题的真实模型、多种子对照，不能
声称复现参考文档中的收益。详见
[设计与评估协议](docs/design/REQUIREMENT_REGISTER.md)。

## 2026-10-16

<a id="bl-ledger-closeout"></a>

### BL-01…14 挂账原因与关闭记录

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

##### 业务逻辑断链（BL-01…14，2026-07-27）

来源：[`docs/design/BUSINESS_LOGIC.md`](docs/design/BUSINESS_LOGIC.md) §4 + §7。**这 14 条已于 2026-10-16 全部解挂**（状态与实现位置以 BUSINESS_LOGIC.md §5 为准），下表保留原始挂账原因供追溯：

| # | 状态 | 挂账原因 | 解挂需要 |
|---|------|----------|----------|
| BL-01 上传 → 知识图谱无运行期通路 | `[x]` | 属功能新建而非缺陷修复：需要「抽取 → 消解 → 冲突 → 入图」的运行期编排与幂等/回滚语义；且入图前须先定 BL-14 的时间维度，否则建成后要回填全量图谱 | 立项 + ADR；先决 BL-14 |
| BL-03 人工复核决策无执行器 | `[x]` | 决策只改队列状态，不产生图谱副作用。本次仅补齐决策自身的正确性（终态保护 409、租户校验 404） | 与 BL-01 同批立项（共用写图通路） |
| BL-12 DAG 并发执行 | `[x]` | 广度预算与丢弃可见性已修（`max_sub_questions` + `plan_coverage`）；**并发分支执行未做** —— 需要仓库尚不具备的并发编排（当前 executor 线性推进 `current_index`） | V-17 量化收益后再决定是否引入并发 |
| BL-13 真 NLI 支撑判定 | `[x]` | 已加对象锚定，但仍非蕴含判定；评测侧**无法复现**该层（`_attach_evidence_catalog` 只持久化 id/content，丢弃 `structured`） | 引入 NLI 模型（live-only）；或在目录中补 `structured` 后同步评测口径 |
| BL-14 图谱无时间维度 | `[x]` | schema + 抽取 prompt + 冲突打分三处联动的数据模型变更；当前冲突按置信度而非时间裁决（`incremental_conflicts.decide_action`） | 产品决策 + 新 ADR（`plan/engineering/tech-stack.md`），**须早于 BL-01** |

> **解挂记录（2026-10-16）**：BL-01 `knowledge/graph_ingest.py` + `api/service_ingest.py`；BL-03 `knowledge/review/executor.py`；BL-12 `agent/parallel_branches.py`；BL-13 `generation/entailment.py` + `generation/relation_consistency.py`；BL-14 `knowledge/temporal.py`。全部经 `test_bl_write_path.py` / `test_bl_reasoning_gates.py` 覆盖。

<a id="bl-test-results"></a>

### BL 修复验证结果与后续复核

来源：[docs/design/BUSINESS_LOGIC.md](docs/design/BUSINESS_LOGIC.md)。

**运行结果（2026-10-16）：** 单测 309 通过（`test_web_claude_ui.py` 7 例为前端静态文件缺失导致的既有失败，与本文件无关）；覆盖率 84%（门禁 80%）；代码硬指标全部通过（同时修复了 3 处既有超标）；离线 POC 评测 21/31，与改动前逐例一致。

> **后续状态（2026-11-02）：** 上述 7 例前端用例已恢复全绿；当前单测 627 通过、覆盖率 89.6%（见 [REFACTOR_PLAN.md](docs/design/REFACTOR_PLAN.md)），离线 POC 基线已提升到 31/31（CI 门禁 30/31）。

<a id="bl-review-metadata"></a>

### 业务逻辑审查范围与验证声明

来源：[docs/design/BUSINESS_LOGIC.md](docs/design/BUSINESS_LOGIC.md)。

**初次审查：** 2026-07-26（纯静态审查）
**本次更新：** 2026-10-16 —— BL-01…BL-14 全部落地，**已实际运行**：`pytest tests/unit`（含新增 `test_bl_write_path.py` / `test_bl_reasoning_gates.py` 共 23 例）、`scripts/check_code_metrics.py`、`ruff`、离线评测 `agr-run-cases --no-llm`。
**定位：** ① 全系统业务逻辑图（节点 / 边 / 判定点）；② 业务逻辑完整性缺口及其落地状态。
**权威边界：** 强制规则以 [`plan/engineering/rules.md`](plan/engineering/rules.md) 为准；债务与延期以 [`IMPORTANT.md`](docs/reviews/IMPORTANT.md) 为准；模块地图以 [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) 为准。

---

## 2026-09-07

<a id="important-snapshot-10"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**P5-UI-02 角色感知控制台交付**：壳 + hash 路由 + 五视图（问答 / 知识 / 审核 / 可观测 / 图谱浏览）；M0 前置两项（`GET /v1/me`、`GET /v1/ingest-tasks` 分页）随批落地（`api/routes/workspace.py`）；2026-10-31 v2 重设计后管理视图位于 `js/admin/`

## 2026-08-08

<a id="important-snapshot-11"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**P5-UI-02 前端重规划立项**（已于 2026-09-07 交付）：试用问答 UI → 角色感知控制台（知识运维 / 审核 / 可观测 / 图谱浏览视图）；API 前置缺口两项随计划挂账（`GET /v1/me`、`GET /v1/ingest-tasks` 列表）；计划见 [`plan/phases/p5-ui-02-console-replan.md`](plan/phases/p5-ui-02-console-replan.md)；视觉设计基准 [`docs/design/UI_DESIGN.md`](docs/design/UI_DESIGN.md)（「制图室」方向；2026-10-31 起由 v2「推理笔记本」取代，见 UI_DESIGN §10）

<a id="ui-drafting-room"></a>

### 早期「制图室」视觉方向（未落地，已被推理笔记本取代）

来源：[docs/design/UI_DESIGN.md](docs/design/UI_DESIGN.md)。

#### 1. 设计语言

工作台以知识图谱为隐喻，采用「制图室」视觉：纸面底色、墨青正文、朱砂交互强调色、结构化细线和宋体标题。图纸网格只作低对比背景，不干扰正文和表格阅读。实体、证据、审核记录与运行数据使用一致的卡片、状态徽标和等宽标识呈现。

#### 2. 色彩与形状

| 令牌 | 值 | 用途 |
|---|---|---|
| `--bg` | `#f6f2e9` | 页面纸面背景 |
| `--bg-elevated` | `#faf7f0` | 侧栏、浮层背景 |
| `--surface` | `#ffffff` | 内容卡片与表格 |
| `--text` | `#22304c` | 正文与标题 |
| `--text-secondary` | `#57627c` | 次级信息 |
| `--muted` | `#778099` | 辅助提示，不单独承载关键说明 |
| `--border` / `--border-strong` | `#ded6c4` / `#c9bfa9` | 分隔线、输入边界 |
| `--accent` / `--accent-hover` | `#c2401f` / `#a33517` | 主操作、链接、焦点与选中态 |
| `--accent-soft` | `#f6e2d9` | 选中与强调的浅底色 |
| `--good` / `--good-bg` | `#35694d` / `#e7f1ea` | 成功反馈 |
| `--bad` / `--bad-bg` | `#9e2b25` / `#f7e7e4` | 错误与破坏性操作 |
| `--warn` / `--warn-text` | `#c9a227` / `#8a5a12` | 状态标记 / 警示文字 |
| `--node-ink` / `--edge-ink` | `#2f5d68` / `#8a5a12` | 图节点 / 关系标记 |
| `--grid-line` | `rgba(34, 48, 76, 0.045)` | 页面制图网格 |

使用 `--accent` 突出可交互状态，不以大面积纯色区块代替层级。成功、警示和错误状态必须同时有文字说明，不只依靠颜色传达。圆角基准为 `--radius: 14px` 和 `--radius-sm: 8px`。

#### 3. 字体与排版

- 标题与品牌：系统宋体衬线栈；正文与控件：系统无衬线栈；ID、时间、指标和路径：`--font-mono`。
- 视图标题约 20–22px，卡片标题约 16–20px，正文约 14–15px，辅助标签约 10–12px。
- 数字使用等宽数字；长 ID、文件名和用户内容允许换行，不挤压相邻操作。
- 不加载远程字体。缺少 CJK 字体时回退到系统字体，不影响内容和操作。

#### 4. 信息架构与布局

- 固定侧栏承载品牌、按角色授权的导航、服务健康和当前身份；窄屏时折叠为抽屉导航。
- 主区顶部显示当前视图标题、服务状态与连接设置；内容按视图组织为卡片、数据表和明确的空态/错误态。
- 问答区包含问题建议、流式过程、答案、引用、推理步骤和反馈；每条问题明确独立执行，不暗示模型具有跨问题上下文。
- 知识视图显示文件校验、上传结果、任务状态与处理说明；审核视图呈现上下文、决策记录和备注。
- 图谱视图以可搜索、可筛选、可分页的实体表为主；运行观测视图呈现指标、预算、事件和查询追溯。
- 文档接入支持 UTF-8 `.md` / `.txt`，最多 20 个文件、每个 5 MiB；任务列表分页并在活跃任务期间轮询。上传只创建接入任务，worker 需要单独运行，任务完成不代表已构建知识图谱。默认离线向量与 BM25 索引仅在各自进程内存中，独立 worker 的结果不会自动同步至 API 查询进程。
- 审核队列按状态和类型筛选，分页查看上下文并记录通过、驳回或跳过决策；决策只进入审核记录，不直接写入图谱。
- 图谱视图按实体名称、别名、ID 搜索，按类型过滤并分页浏览；当前 API 不提供关系邻域查询。运维视图仅供管理员查询进程指标、预算、安全事件，以及按 query ID 回查审计链和 trace。
- 桌面使用侧栏 + 内容区布局；平板与手机使用抽屉导航、单列卡片和横向可滚动数据表。

#### 5. 组件与状态

- **操作按钮**：主操作使用朱砂实底；次操作用描边；破坏性操作用错误色。请求进行中时禁用重复提交并保留明确的忙碌文案。
- **状态徽标**：待处理、完成、拒绝、错误等状态使用稳定的文字标签和对应浅底色；进行中状态不使用终态图形。
- **引用与推理链**：引用角标可点击定位论断证据；子问题、步骤、路径和证据 ID 分层展示，并提供安全的 JSON 文本查看方式。
- **数据表**：表头语义清晰、行操作可键盘聚焦；空结果、加载、无权限和服务错误使用可解释的状态卡与后续动作。
- **通知**：操作成功、警告和错误使用屏幕阅读器可感知的状态消息；重要失败保留在对应视图，不只依赖短暂 Toast。
- **权限**：`/v1/me` 提供当前身份和能力映射以构建导航；后端路由权限始终作为最终授权边界。API 不可用或凭证无效时显示连接错误，不通过试探受限端点推断角色。
- **上传**：支持 Markdown 与纯文本；显示每个文件的格式、大小与结果，明确单文件 5 MB、单批 20 个文件的限制。

#### 6. 可达性与动效

- 正文文本对比度目标至少 `4.5:1`；辅助色仅用于非关键提示。
- 所有可操作元素提供可见 `:focus-visible` 焦点环、明确标签和键盘操作路径；图标按钮须有可访问名称。
- 实时推理更新使用 `aria-live`；状态不只通过颜色或动画表达；常见操作命中区至少 32px。
- 动效仅使用 CSS，控制在 220ms 内；尊重 `prefers-reduced-motion`，减少动效时保留完整状态反馈。

#### 7. 安全与资源

- 用户输入、模型答案、审核内容和服务返回值只能通过 Vue 文本插值或 `textContent` 展示；禁止 `v-html` 与 `.innerHTML`。
- CSS、字体和图标不依赖外链资源；不引入图表、图标或动效运行时依赖。
- API Key 输入为密码控件；页面只显示身份与角色，不回显密钥。共享设备使用后可在连接设置中清除本地密钥。

## 2026-07-27

<a id="bl-original-validation"></a>

### BL 修复变更集原始验证清单（已被后续验证取代）

来源：[docs/reviews/IMPORTANT_DOCUMENTATION.md](docs/reviews/IMPORTANT_DOCUMENTATION.md)。

### Important Documentation — BL-01…14 修复变更集的待验证清单

> **本文件是「未执行验证」的替代交付物（2026-07-27 编写）。**
> 依据全局规则：当时**未运行项目、未安装依赖、未执行测试**（环境中亦无 `python` / `.venv` / `ruff`）。
> 所有正确性主张均通过**代码逻辑检查**（读代码、比对调用方、手算指标）得出。
>
> **2026-11-02 复核：** 本清单要求的验证已执行完毕——`BUSINESS_LOGIC.md` §5 记录了实跑结果（含 `test_bl_write_path.py` / `test_bl_reasoning_gates.py`），其列出的 BL-01…BL-14 全部解挂；下文的「未执行」框架与 §4 的「仍未实施」均已过期，保留作历史记录。
>
> 变更来源：[`BUSINESS_LOGIC.md`](docs/design/BUSINESS_LOGIC.md) §4 缺口清单，落地状态见其 §7。
> 当时的挂账项见 [`IMPORTANT.md`](docs/reviews/IMPORTANT.md) §6「业务逻辑断链」——该节所列 BL-01/03/12/13/14 已于 2026-10-16 解挂。

---

#### 1. 必跑门禁（按顺序）

```bash
# 0) 环境
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 1) 硬性代码度量（文件 ≤300 行、函数 ≤50 非空行、嵌套 ≤3、位置参数 ≤3、CC ≤10）
python scripts/check_code_metrics.py

# 2) Lint / format
ruff check src tests scripts
ruff format --check src tests scripts

# 3) 单测 + 覆盖率门禁
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q

# 4) 离线主回路（确定性，无 LLM / 无 Docker）
agr-run-cases --no-llm
python -m agentic_graphrag score

# 5) 离线 EV 演练（含增量冲突 drill）
PYTHONPATH=src python scripts/p3_ev_offline.py

# 6) 主验证包
./scripts/verify_all.sh --with-tests
```

##### 1.1 本次已做的静态核对（可跳过重算，但建议复核）

| 核对项 | 方式 | 结果 |
|---|---|---|
| 所有改动文件 ≤300 行 | `wc -l` | 最大 `config.py` = **300**（恰好达限，再加一行即失败） |
| 函数 ≤50 非空行（**含 `def` 行**——度量脚本用 `lines[lineno-1:end_lineno]`） | awk 复刻 | 最长 `cli/graph_cmd.py:_extract_triples` = 48、`generation/answer.py:generate_answer` = 48、`graph_builder.py:load_triples_into_graph` = 45 |
| 位置参数 ≤3 | awk 复刻 | 无超限 |
| 行宽 ≤100 | awk | 无超限 |
| 嵌套 ≤3 | 缩进启发式 + 逐个 `elif` 人工核 | 无超限（`elif` 在该脚本中**会**增加一层，已逐处确认均 ≤3） |
| CC ≤10 | 手算 AST 规则 | 最紧的是 `ingest_worker._run_task_batch` ≈ **10**（恰好达限），其余 ≤9 |

> ⚠️ CC 与嵌套是**人工估算**，`scripts/check_code_metrics.py` 是唯一权威 —— 步骤 1 必跑。

---

#### 2. 行为变更 —— 需实跑确认（回归风险）

##### 2.1 会改变既有输出/语义的变更

| # | 变更 | 影响面 | 验证方式 |
|---|---|---|---|
| C-01 | `fabrication_rate` **口径收紧**：从「claim 有无 evidence_ids」改为「与运行期门禁同口径」（id 存在 + 词法命中） | `reports/*.json`、`eval/report.py` markdown、任何跟踪该指标的历史序列 | 对同一 `reports/poc_run.jsonl` 前后跑 `python -m agentic_graphrag score`，确认 `fabrication_rate` **可能升高**；`unbound_claim_rate` 应等于旧的 `fabrication_rate` |
| C-02 | 新增 `unbound_claim_rate` 字段 | `SystemMetrics.to_dict()`、报告 markdown | 断言报告 JSON 含该键；旧报告缺该键时 `_summary` 用 `.get()` 返回 `None`，不应抛异常 |
| C-03 | `load_triples_into_graph` **默认开 schema 门禁**（`schema=None` 语义反转） | 所有入图路径 | 见 §3.1 兼容性核对；断言 seed 图 23 条全部通过（`triples_rejected == 0`） |
| C-04 | PDF 上传被拒（413 + 「暂不支持」） | `POST /v1/docs`、文档承诺 | 上传 `.pdf`，断言 413；文档已同步（README / ARCHITECTURE / ENTERPRISE_READINESS / ops-runbook / CLAUDE.md） |
| C-05 | 复核项重复决策返回 **409**（原为静默覆盖） | `POST /v1/review-queue/{id}/decision` | 连续两次 approve，第二次断言 409 |
| C-06 | `RetrievalCache.retrieval_key` **加入租户维度** | 所有检索缓存条目 | 键格式变化 ⇒ 进程重启后旧内存缓存自然失效（内存缓存无持久化，无需迁移）；断言双租户同问题不互相命中 |
| C-07 | AUTO_UPDATE 冲突现在**真的删除**被取代的旧边（内存图新增 `delete_relation`） | 增量更新后的关系总数 | `test_incremental_updater_no_clear`：批 2 后关系数应为 **1**（旧为 2）。该用例只断言实体数，不会失败，但行为已变 |
| C-08 | `ConflictAction.SKIP` **已删除** | 任何 `import` 或 `match` 该成员的下游 | 全仓 grep 已确认无引用；外部脚本需自查 |
| C-09 | `RetrievalCache.persist_embeddings` → `persist_cache_stats`，产物 `data/cache/embeddings.json` → `cache_stats.json` | 运维脚本 / 监控采集 | 全仓 grep 已确认无调用方；若有外部采集器需改路径 |
| C-10 | `fuse_and_cache(..., cache_result=)` 参数**已移除**（无调用方） | `agent/executor_dispatch.py` | grep 确认仅 `executor.py` 调用且未传该参数 |
| C-11 | 规划广度上限 `guardrails.max_sub_questions = 6` | 超过 6 个子问题的 plan | 被截断的节点写入 `chain.metadata.dropped_sub_questions`；未执行节点写入 `unexecuted_sub_questions` |
| C-12 | 护栏文案变更：`max_hops exceeded (5/4)` → 追加 `; breadth stop: …` 或 `; depth stop` | 断言护栏原因文本的用例 | `tests/unit/test_guardrails_memory.py:24` 只断言 `"max_hops" in reason`，**不受影响**；其他断言全等文本的地方需检查 |

##### 2.2 修复类变更（应带来可观测的正向变化）

| # | 变更 | 期望现象 |
|---|---|---|
| F-01 | SSE `_initial_state` 补 `tenant_id`（BL-04） | 流式路径不再以 `tenant_id=None` 检索（四个后端均把 `None` 当「不过滤」） |
| F-02 | `_REPAIR_HINTS` 按具体失败原因生成重生成提示（BL-02） | 词法支撑失败时的重试不再是确定性浪费 |
| F-03 | CJK 二元组切词（BL-02） | 纯中文 claim 不再必然落 `citation_fallback` |
| F-04 | 图证据对象锚定（BL-13） | 引用了「关系不符但主语相同」的边时，门禁应拒绝 |
| F-05 | `IngestTaskStore.requeue_stale()`（BL-06） | worker 崩溃后任务可被重新取到 |
| F-06 | `_build_cli_worker()` 注入 `task_store`（BL-05） | `--once` 会消费队列而非退回旧的 doc-store 扫描 |
| F-07 | 审计落库失败 `logger.error`（BL-10） | 不再静默丢失合规记录 |
| F-08 | 全量 embed 失败 ⇒ 任务判 `failed`（BL-10） | 不再以 `done` + 0 向量收场 |
| F-09 | 上传分片读取（256KB）+ 严格 UTF-8（BL-08） | 超大文件不再先整体入内存；非 UTF-8 被拒 |
| F-10 | `/v1/feedback` 与 `/v1/review-queue/*/decision` 一律 404（BL-09） | 不可作存在性探测 |
| F-11 | `/v1/graph/entities` 加 RBAC + 租户过滤（BL-09） | 关闭鉴权时行为不变（`require_role` 在 `AGR_REQUIRE_AUTH` 未开时 no-op） |
| F-12 | `BatchResult.conflicts_kept` + `accepted` 取实际 upsert 数（BL-11） | 批次计数守恒 |
| F-13 | `ReviewQueue._load` 跳过损坏行（BL-11） | 单行损坏不再拖垮 `QueryService` 构造 |
| F-14 | `_TASKS` 有界 `OrderedDict`（上限 500）（BL-11） | 进程生命周期内不再无限增长 |

---

#### 3. 兼容性核对（已静态确认，仍建议实跑复核）

##### 3.1 「默认开门禁」对既有三元组的影响（C-03）

`load_triples_into_graph(schema=None)` 现在加载 `configs/schema/domain_v0.yaml` 并按
`knowledge.extract_confidence_threshold`（0.5）过滤。已逐条核对以下来源**全部合规**：

| 来源 | 条数 | 关系/类型 | 最低置信度 |
|---|---|---|---|
| `data/processed/seed_triples.jsonl` | 23 | `CEO_OF`/`COMPETES_WITH`/`PARENT_OF`/`PARTICIPATED_IN`/`PRODUCES`/`SUBSIDIARY_OF`/`SUPPLIES`/`SUPPLIES_FOR`/`WORKED_AT`，head/tail 类型与 schema 逐对匹配 | 0.80 |
| `knowledge/pilot_triples.py` | 生成式 | 同上 + `WORKS_AT` | 0.95 |
| `eval/p3_ev.py` drill | 2 seed + 3 batch | `SUBSIDIARY_OF`/`CEO_OF` | 0.60 |
| `tests/unit/test_p3_ev.py` | 2 | `PARENT_OF`/`CEO_OF` | 0.90 |
| `tests/unit/test_phase3_kg_and_budget.py` | 2 | `PARENT_OF` | 0.60 |
| `tests/unit/test_multihop_graph_evidence.py` / `test_graph_recursion_fallback.py` | 读 seed 文件 | 同 seed | 0.80 |

**验证点：** 断言 `stats["triples_rejected"] == 0`。若真域语料接入后出现拒绝，
`api/service.py:_load_seed_graph` 会打 WARNING（含 `rejection_reasons`），不静默。

**已 gate 的调用方**（必须传 `pre_gated=True`，否则重复计数）：
`cli/graph_cmd.py:_write_graph`（`_gate_and_report` 先 gate）、`knowledge/incremental.py:_apply_locked`（`_gate` 先 gate）。

##### 3.2 现有用例的预期结果（静态推演，需实跑确认）

| 用例 | 推演结论 |
|---|---|
| `test_enterprise_readiness.py::test_upload_allowed_extension` | `.md` → 200 ✔ |
| `test_enterprise_readiness.py::test_upload_disallowed_extension` | `.exe` → 413 ✔ |
| `test_enterprise_readiness.py::test_upload_too_many_files` | 25 个 → 413 ✔ |
| `test_graph_entities_api.py::test_graph_entities_endpoint_returns_seed_entities` | RBAC 在未开鉴权时 no-op；`_tenant_matches` 允许空租户的 seed 记录 ⇒ 仍返回非空 ✔ |
| `test_phase3_triage_fusion_cache.py`（`set_retrieval`/`get_retrieval` 无租户参数） | 两侧同为 `tenant_id=None` ⇒ 同键 ✔ |
| `test_phase3_kg_and_budget.py::test_incremental_updater_no_clear` | 实体数 2 → 3，断言 `e2 >= e1` ✔；关系数由 2 变 1（见 C-07） |
| `test_phase3_kg_and_budget.py::test_review_queue_decide` | `tenant_id=None` ⇒ `_tenant_allows` 放行；`pending` ⇒ 不触发 409 ✔ |
| `test_p3_ev.py::test_incremental_drill_smoke` | `post_query_ok` 依赖 `HoldCo` 邻居 ≥1（Acme/Elena/Nova 均在）✔；`batch_accepted` = 1 |
| `test_guardrails_memory.py:24` | 只断言 `"max_hops" in reason` ⇒ 不受文案追加影响 ✔ |

##### 3.3 需要新增的用例（当前**缺测**）

| # | 用例 | 断言 | 对应 |
|---|---|---|---|
| N-01 | CJK claim 词法支撑 | 中文 claim + 中文证据 ⇒ `claims_lexically_supported` 为 True | BL-02 / V-03 |
| N-02 | 对象锚定拒绝 | 证据 `A -[FOUNDED_BY]-> X`（`structured.kind="neighbor"`, `tail="X"`），claim「A 的 CEO 是 Y」引用该 id ⇒ `validate_answered_claims` 返回 `"claim text not supported by cited evidence content"` | BL-13 / V-18 |
| N-03 | path 锚定 | `structured.kind="path"` 且 claim 只命中 1 个节点 ⇒ 拒绝；命中 ≥2 ⇒ 通过 | BL-13 |
| N-04 | 缓存租户隔离 | 租户 A `set_retrieval` 后，租户 B `get_retrieval` 同问题返回 `None` | BL-04 / V-06 |
| N-05 | 流式租户隔离 | 租户 B 走 `/v1/query/stream` 不应看到租户 A 的 chunk | BL-04 / V-05（**最高优先**） |
| N-06 | `requeue_stale` | 造一个 `updated_at` 超期的 `EXTRACTING` 任务 ⇒ 回到 `QUEUED` 且出现在 `pending()` | BL-06 / V-08 |
| N-07 | worker 消费队列 | `_build_cli_worker()` 返回的 worker 其 `task_store is not None` | BL-05 / V-07 |
| N-08 | 无扩展名上传 | 文件名 `payload`（无点）⇒ 413 | BL-08 / V-09 |
| N-09 | 超大文件 | 100MB 上传 ⇒ 413，且进程内存不应达到 100MB 量级 | BL-08 / V-10 |
| N-10 | 非 UTF-8 上传 | 含 `0xFF` 字节的 `.txt` ⇒ 413 | BL-08 |
| N-11 | 跨租户复核决策 | 租户 A 的 operator 对租户 B 的 item_id ⇒ 404 | BL-09 / V-11 |
| N-12 | 重复决策 | 第二次 decision ⇒ 409 | BL-11 / C-05 |
| N-13 | feedback 存在性一致 | 「他租户 query_id」与「随机 query_id」返回同为 404 | BL-09 / V-13 |
| N-14 | `/v1/graph/entities` 租户过滤 | 双租户实体共存时返回集合正确 | BL-09 / V-12 |
| N-15 | 批次计数守恒 | 含 KEEP_OLD 的批次：`accepted + rejected + conflicts_auto + conflicts_review + conflicts_kept` == 输入数 | BL-11 / V-14 |
| N-16 | 广度上限 | 桩化产出 8 个子问题的 plan ⇒ `sub_questions` 只剩 6，`metadata.dropped_sub_questions` 有 2 条 | BL-12 / V-16 |
| N-17 | 护栏文案区分 | planned=6 > max_hops=4 ⇒ reason 含 `breadth stop`；planned ≤ max_hops ⇒ 含 `depth stop` | BL-12 |
| N-18 | 内存图删边 | `InMemoryGraphStore.delete_relation(id)` 删除后 `counts()["relationships"]` 减 1，跨租户同 id 一并删除 | BL-11 / C-07 |
| N-19 | 审计落库失败可见 | 注入抛异常的 audit store ⇒ `save_chain_audit` 返回 False 且打日志，查询本身仍成功 | BL-10 |
| N-20 | 全量 embed 失败 | `embed_fn` 全抛 ⇒ `IngestResult.error` 为 `embed_failed_all (n/n)`，任务判 `failed` | BL-10 |
| N-21 | 损坏复核队列行 | 队列文件插入一行 `{bad json` ⇒ `ReviewQueue` 构造成功且跳过该行 | BL-11 |
| N-22 | `_TASKS` 上界 | 创建 600 个任务 ⇒ `len(_TASKS) == 500` | BL-11 |
| N-23 | 流式 `request_id` | SSE 路径落库的 chain 其 `metadata.request_id` 非空 | BL-11 |
| N-24 | `unbound_claim_rate` 兼容旧行 | 无 `chain.metadata.evidence` 的历史行 ⇒ `_claims_fail_gate` 返回 False（不冤枉旧数据） | C-01 |

---

#### 4. 当时未实施（2026-07-27；**2026-10-16 已全部落地**，见 `BUSINESS_LOGIC.md` §5）

| 缺口 | 为什么本次不做 |
|---|---|
| **BL-01** 上传 → 图谱无运行期通路 | 功能新建：需要「抽取 → 消解 → 冲突 → 入图」的运行期编排 + 幂等/回滚语义。审计文件自身（§6）也标注为「需先立项」，且**须在 BL-14 决策之后**——否则入图通路建成后再补时间字段要回填全量图谱 |
| **BL-03** 复核决策无执行器 | 同上：批准需产生图谱副作用。本次只补齐决策自身的正确性（终态保护 409、租户校验 404） |
| **BL-14** 图谱无时间维度 | 数据模型变更（schema + 抽取 prompt + 冲突打分三处联动）。按仓库约定，技术选型变更**须先写 ADR**（`plan/engineering/tech-stack.md`）再动代码 |
| **BL-12 的并发分支执行** | 需要仓库尚不具备的并发编排（executor 目前线性推进 `current_index`）。本次只修「静默丢弃 + 文案误导」这一有害部分；收益应先由 V-17 量化 |
| **BL-13 的真 NLI 判定** | 需引入模型依赖（禁止本次下载）。另：评测侧**无法复现**对象锚定层——`_attach_evidence_catalog` 只持久化 `id`/`content`，丢弃 `structured` |

---

#### 5. 安全检查表（提交前逐项确认）

| 项 | 本变更集状态 |
|---|---|
| 无硬编码密钥 | ✔ 未引入任何字面量密钥；新增配置项 `guardrails.max_sub_questions` 为数值 |
| 用户输入校验 | ✔ 上传扩展名/大小/编码三重校验收紧（BL-08）；`limit`/`offset` 被 clamp 到 `[0, MAX_ENTITY_PAGE]` |
| SQL 注入 | 不适用（无 SQL；Neo4j 走参数化 Cypher，本次未改） |
| XSS | 不适用（本次未改 `web/`；`v-html`/`innerHTML` 仍禁用） |
| CSRF | 不适用（无 Cookie 会话，API-Key 鉴权） |
| 认证/授权 | ✔ `/v1/graph/entities` 补 RBAC 依赖；复核决策补租户校验 |
| 限流 | ✔ 沿用 `AuthRateLimitMiddleware`（全局），本次未新增绕过路径 |
| 错误信息不泄露 | ✔ 「他租户」与「不存在」统一 404（消除存在性探测）；`decide` 的 409 只回状态字符串，不回内容 |

---

*本文件随 BL 修复变更集同批提交。任一条目实跑通过后，请在此勾记并同步 [`BUSINESS_LOGIC.md`](docs/design/BUSINESS_LOGIC.md) §7。*

## 2026-07-26

<a id="bl-original-audit"></a>

### 业务逻辑原始缺口审查（含后续修复批注）

来源：[docs/design/BUSINESS_LOGIC.md](docs/design/BUSINESS_LOGIC.md)。

#### 6. 原始缺口记录（2026-07-26 审查）

> 以下为初次审查的原文记录，行号对应当时版本，保留用于追溯；现状以 §0 与 §5 为准。

##### 6.1 完整性缺口清单

严重度：🔴 关键（业务承诺无法兑现 / 数据越权）｜🟠 高｜🟡 中｜⚪ 低

**验证状态：** BL-01 / BL-02 / BL-03 / BL-04 已经过**独立对抗式复核**（复核者以「默认该结论错误」为立场，专门搜寻可推翻的调用点、默认值、中间件与并行模块），四条全部 **CONFIRMED**；复核同时修正了 BL-01 的调用点枚举、BL-02 的触发条件与行号、BL-04 的受影响子路径。其余条目为单次静态审查结论，**未经对抗复核**，标注为待验证。

**BL-12 / BL-13 / BL-14 为第二轮补充审查所得**（来源：`ARCHITECTURE_DESIGN.md` 净室设计与实现的逐条对差，见 [`DESIGN_VS_IMPLEMENTATION.md`](docs/design/DESIGN_VS_IMPLEMENTATION.md) 的 D1 / D2 / D3），同样**未经对抗复核**。该轮的第四条发现（人工复核决策无执行器，D5）与既有 **BL-03 完全重合**，故不另立条目 —— BL-03 的记述更完整（额外指出 `IncrementalUpdater._append_review` 与 `ReviewQueue` 是两套互不连通的存储）。

###### 🔴 BL-01：不存在「上传文档 → 进入知识图谱」的运行期通路

| 项 | 内容 |
|----|------|
| 证据 | 全仓库图写入调用点共 7 处，**无一可从 API 运行期到达**：底层 `graph_builder.py:101-102`；`resolution_merge.py:23`（仅经 `EntityResolver._apply_merge`，而 `EntityResolver(` 只在两个单测中构造）；`api/service.py:134`（**服务构造期**加载 seed 三元组）；`cli/graph_cmd.py:153`、`cli/cases_run.py:52`（CLI）；`incremental.py:204`、`eval/p3_ev.py:166`（离线评测演练）。 |
| 证据 | `IngestWorker.process_doc`（`ingest_worker.py:70-86`）只做 chunk → embed → 向量/全文索引；其构造函数（`:50-59`）**根本没有 graph 参数**。`api/` 与 `agent/` 两个包内对 `bundle.graph` 的引用除 `service.py:134` 外全部是读操作（`app.py:169/188`、`service_helpers.py:73`、`routes/knowledge.py:216`）。 |
| 证据 | `api/app.py:28-51` lifespan 只做 `setup_logging` → 可选 OTel → `build_default_service()` → `_validate_live_credentials`，**不启动任何 worker 或线程**；`src/` 内启线程处仅 `ingest_worker.py:141`（API 从不调用）与 `service_agent.py:21` 的查询线程池。 |
| 代码自陈 | `knowledge_upload.py:83` 的任务消息即写明：`"Documents persisted to doc store; run extract pipeline offline or via worker"`。 |
| 失败场景 | 管理员通过 `POST /v1/docs` 上传新文档 → 文档可被向量/BM25 检索到 → 但其中的实体与关系**永远不会进入图谱** → 图检索（多跳推理，本项目的核心卖点）对新文档完全无效。 |
| 影响 | 产品叙事「知识图谱作为结构化记忆」在运行期只对 CLI 预构建的语料成立。 |

###### 🔴 BL-02：在线回答路径的引用门禁对纯中文 claim 必然失败

| 项 | 内容 |
|----|------|
| 证据 | `generation/citations.py:142-148` `_content_tokens()` 以**空白分词**：`(text).lower().replace("-"," ").split()`。中文无空格 → 整句坍缩为一个 token（全角标点被 `isalnum()` 过滤掉，**不**起分隔作用）。`claims_lexically_supported`（`:116`，`min_overlap=1`）要求 claim 与被引证据**至少共享一个 token**。 |
| 触发条件（已精确化） | 失效点在 **claim 一侧**，与证据类型无关。判据：*claim 文本中若不含任何与证据共享的「空格分隔的拉丁/数字 token」，则必然判失败*。 |
| 实测（纯函数推演，未运行项目） | 证据 `Apex Holdings -[CEO_OF]-> Elena Varga (Person)`：<br/>· claim `Elena Varga is the CEO of Apex Holdings.` → 交集 {apex, elena, holdings, varga} → **通过**<br/>· claim `Apex Holdings的首席执行官是Elena Varga。` → 交集 {apex, varga} → **通过**（多词拉丁人名「漏」出可匹配 token）<br/>· claim `NovaTech的首席执行官是马库斯·韦伯。` → 交集 ∅ → **失败**<br/>· claim `苹果控股的首席执行官是埃琳娜·瓦尔加。` → 交集 ∅ → **失败** |
| 失败场景 | 在线路径 `generate_answer`（`answer.py:152`）→ `_apply_payload`（`:76`，`require_lexical_support` 默认 True，全仓库无任何调用点传 False）返回 None → 重生成一次（`answer.py:193`）→ 再次失败 → `honest_fallback("answer claims lacked evidence citations after regenerate")`（**`answer.py:221`**）。**纯中文问答消耗 2 次 STRONG 档 LLM 调用后退化为「无法回答」。** |
| 重试注定失败 | 重生成提示（`answer.py:100-103`）只说「每条 claim 必须带 evidence_ids」，而真实失败原因是**词面重叠**，与 id 无关 → 第二次调用是确定性浪费。 |
| 为何未被评测发现 | `answer.py:138` —— `allow_llm=False` 时直接走 `offline_answer`，**完全绕过该门禁**（`:150`、`:173` 的降级分支亦然）。`--no-llm` 的 20 例评测因此全绿。典型的离线/在线路径不对称。 |
| 三个入口均受影响 | `agent/loop_runtime.py:208`、`agent/fast_path.py:33`、`agent/loop_recover.py:83` —— 只要 `allow_llm and llm is not None` 就会过闸。 |
| 修复位置 | `generation/citations.py:144` 需改为 CJK 感知切分（CJK 连续段落须切出 unigram/bigram）。**注意：直接照搬 `stores/fulltext_store.py:11` 的 `[\w一-鿿]+` 正则无效** —— 它对 CJK 同样是「整段一个 token」。 |

###### 🔴 BL-03：人工复核决策没有执行器

| 项 | 内容 |
|----|------|
| 证据 | `ReviewQueue.decide()`（`review/queue.py:150-173`）只写 `status`/`reviewer`/`decided_at`/`decision_note`。`grep -rn "APPROVED\|approved" src/`（排除 `queue.py`）**零命中** —— 无任何代码消费已批准项。 |
| 失败场景 | 运维批准一条低置信冲突 → 图谱不变；驳回一条错误三元组 → 该三元组（若已入图）不被移除。复核队列是一本**只写的台账**，不是控制面。 |
| 关联 | `IncrementalUpdater._append_review`（`incremental.py:269`）把待复核冲突写入一个**独立的 JSONL** `review_log`，与 `ReviewQueue` 是两套互不连通的存储；`GET /v1/review-queue` 永远读不到它。 |

###### 🔴 BL-04：SSE 流式路径未传播 `tenant_id`，导致跨租户检索与缓存共享

| 项 | 内容 |
|----|------|
| 证据 | 同步路径 `loop_recover.py:36-46` 初始状态**含** `"tenant_id": tenant_id`（`:45`）；流式路径 `loop_stream.py:248-258` `_initial_state()` 只有 3 个参数，**不含 tenant_id**（调用点 `:223`）。全仓库 `agent/` 内 `"tenant_id"` 字面量仅两处命中：`loop_handlers.py:94`（读）与 `loop_recover.py:45`（写）。 |
| 传导链 | `loop_handlers.py:94` `tenant_id = ctx.state.get("tenant_id") or None` → 流式恒为 `None` → `executor.run(tenant_id=None)` → ① 三路检索**不做租户过滤**（`executor_dispatch.py:159/171`，图侧 `:106/:125/:146`）；② `executor.py:143` 读共享检索缓存、`:150` `cache_result=tenant_id is None` **写入**共享缓存。 |
| 存储层为「放行」而非「拒止」 | `fulltext_store.py:83` `if tenant_id is None or ...`；`vector_store.py:134` `if tenant_id is not None and ...`；`memory_graph.py:229-230` `if requested is None: return True`；Qdrant `vector_store.py:67-71` 仅在非 None 时加 `FieldCondition`。四个后端一致：`None` = 全量放行，泄漏方向而非 fail-closed。 |
| 失败场景 | 租户 A 用 `POST /v1/query/stream` 提问 → 检索命中租户 B 的实体/chunk 并作为证据引用；结果写入全局检索缓存 → 租户 B 后续同样子问题直接读到 A 的检索结果。 |
| 受影响子路径（已精确化） | 闲聊：不涉检索；**Fast Path 未升级：正确**（`loop_stream.py:177` 显式传 `tenant_id`）；**Agentic 三种进入方式全部受影响** —— triage 路由（`:146`）、`force_agentic`/`enable_triage=False`（`:100`）、**Fast Path 升级**（`:182`）；非流式 `POST /v1/query`：正确。 |
| 缓存键无租户 | `RetrievalCache.retrieval_key`（`cache.py:133`）只含 index_version + 归一化 query + tools，**不含 tenant_id**，其隔离完全依赖「租户作用域运行绕过缓存」这一约定 —— 该约定在流式路径被打破。答案缓存**不受影响**（`cache.py:150` 已含租户，另有 `service_helpers.py:148` 元数据兜底）。 |
| 与已发布承诺冲突 | `docs/reviews/ENTERPRISE_READINESS.md:278` 明写「检索三路、executor dispatch、fast path 与 agent loop 透传 `tenant_id`（**租户作用域运行绕过检索缓存**，隔离优先）」—— agent loop 一环在流式下不成立。 |
| 连带发现 | API 侧 `tenant_id` 恒非空（`api/auth.py:169/178/189`、`api/service.py:168/177` 均默认 `"default"`），因此 `executor.py:143` 的 `tenant_id is None` 判据意味着 **P3-PERF-04 检索缓存在所有非流式 API 查询上实际是失效的**，而在流式上「生效」恰恰是本缺陷的副作用。修复 BL-04 会同时让两侧都关闭该缓存 —— 需要一并决定缓存键是否改为含租户。 |
| 现有测试盲区 | `test_enterprise_completion.py:39-74` 只直接调 `store.search/neighbors`；`test_audit_tenant_cache_isolation.py` 只覆盖答案缓存与审计库；`test_live_sse_stream.py:26/51` 虽传 `tenant_id="t"` 但不做任何隔离断言。 |
| 修复位置 | `agent/loop_stream.py:223` —— `opts.tenant_id` 已在 `ctx.run_opts` 上（由 `resolved_run_opts` 填充，`loop.py:264`），只需把它并入初始状态，对齐 `loop_recover.py:45`；该行以上无需改签名。 |

###### 🟠 BL-05：文档化的 worker 命令不消费任务队列

| 项 | 内容 |
|----|------|
| 证据 | `CLAUDE.md` 记载 `python -m agentic_graphrag.knowledge.ingest_worker --once  # consume queued upload tasks`，但 `ingest_worker.py:181-189` 的 `__main__` 构造 `IngestWorker(...)` **未传 `task_store=`** → `run_once()`（`:110`）走 legacy 分支遍历 `doc_store.list_ids()`。 |
| 失败场景 | 上传后运行文档化命令 → 任务状态永远停在 `queued`，`GET /v1/ingest-tasks/{id}` 永远返回 queued。且该命令另起进程调 `create_offline_bundle()`，若 doc store 为 `InMemoryDocStore`（`stores/doc_store.py:11`）则与 API 进程不共享数据，worker 看不到任何文档。 |

###### 🟠 BL-06：`extracting` 态崩溃后任务永久卡死

2026-11-03 更新：租约重投之外已增加持久化 chunk checkpoint、跨进程任务锁与 reconcile，见 §3.1.1。以下为原始问题证据。

| 项 | 内容 |
|----|------|
| 证据 | `ingest_worker.py:124` 先 `transition(QUEUED→EXTRACTING)` 再处理；`IngestTaskStore.pending()`（`ingest_tasks.py:90`）**只返回 QUEUED**；`_ALLOWED_TRANSITIONS`（`:24-28`）无 `EXTRACTING→QUEUED` 重投路径，也无租约/心跳/超时字段。 |
| 失败场景 | worker 在处理中被 kill → 任务永久停留 `extracting`，无任何机制重投或告警。 |

###### 🟠 BL-07：schema 校验门禁在写入函数中是「可选参数」而非「门」

| 项 | 内容 |
|----|------|
| 证据 | `schema_check.py:3` 声明不变量「非符合 schema 的三元组被拒绝并记录，**绝不进入图**」。但 `graph_builder.load_triples_into_graph`（`:74-97`）的 `schema` 参数默认 `None`，为 `None` 时**完全跳过** `gate_triples`。 |
| 各调用点 | `cli/graph_cmd.py:153` ✅（在 `_gate_and_report` 中已提前 gate）；`api/service.py:134` ❌ 未 gate（seed 三元组，风险可接受但不变量不成立）；`incremental.py:204` ❌ **显式** `schema=None`。 |
| 叠加缺陷 | `IncrementalUpdater` 两处构造点（`eval/p3_ev.py:167`、`tests/unit/test_phase3_kg_and_budget.py:43`）**都未传 `schema=`** → `_gate()`（`incremental.py:210`）直接原样返回，增量路径既无 schema 校验也无置信度阈值。 |
| 次级 | `graph_builder.py:92` —— 即使传了 schema，`confidence_threshold=None` 时 `thr=0.0`，置信度过滤退化为空操作。 |

###### 🟡 BL-08：上传校验可被绕过 + PDF 声称支持但无解析

| 项 | 内容 |
|----|------|
| 证据 | `knowledge_upload.py:23` `if ext and ext not in ALLOWED_EXTENSIONS:` —— **无扩展名文件 `ext` 为空 → 短路跳过白名单**。 |
| 证据 | `:48` 大小校验发生在 `await file.read()`（`:47`）**之后** → 5MB 限制无法阻止超大文件先进内存。 |
| 证据 | `ALLOWED_EXTENSIONS` 含 `pdf`（`:16`），但 `:54` 一律 `content.decode("utf-8", errors="replace")`，**无 PDF 文本抽取** → PDF 被存为乱码并进入分块与索引。 |

**2026-11-03 更新（已落地，以上保留为历史证据）：**

- `core/ports/parsing.py` 定义 `DocumentParser` / `ParsedDocument`；API 与 CLI 共用
  `knowledge/parsers` 路由。扩展名白名单与 magic bytes 双重校验，OOXML 检查 ZIP
  包内主文档类型，不解压文件；伪装成 PDF 的可执行文件在调用解析器前拒绝。
- `TextParser` 支持 MD/TXT；charset-normalizer 检测非 UTF-8 编码，支持 GBK/GB18030，
  去 BOM、统一换行后以 Unicode Markdown 保存。严格 UTF-8 优先；检测阈值为
  `encoding_min_confidence`（`1 - chaos`，不是统计概率），短文本编码同分时按
  `encoding_preference`（默认 GB18030）选择。类型/签名不符或 MinerU 未配置为 **415**，
  无可靠解码或无有效正文为 **422**，文件/批量超限仍为 **413**。
- `HtmlParser` 用 trafilatura 提取正文，BeautifulSoup 去除 nav/footer/script 等噪声并
  解实体；提取失败或丢失标题/表格时用结构化 Markdown 渲染兜底。`MinerUParser`
  显式调用可配置的 HTTP `/file_parse`，支持路由 PDF、DOCX、PPTX、XLSX 及图片扫描件；
  使用 `parse_method=auto`，由服务执行 OCR。服务故障/超时返回 **503**。
  返回 Markdown 中的 HTML 表格也转为 Markdown 表格，代码围栏里的示例保持原样。
- `knowledge/chunking.py` 按 Markdown 标题层级划分 section，段落 → 中英文句子 → token
  递归拆分；默认 **512 tokens / 最多 64 tokens overlap**，预算包含
  `Doc title > H1 > H2` 前缀。重叠不跨 section、不截断表格或代码；大表格只在整行间
  拆分，每片重复表头。围栏/缩进代码、单个列表项和引用块保持完整；单个原子结构或
  表头加一行仍超预算时，worker 明确报 failed 并提示增大预算，不生成超限块。
- `chunk_text` 保留为薄兼容入口，`chunk_size` / `overlap` 现以 token 计量；配置迁移为
  `chunk_size_tokens` / `chunk_overlap_tokens`，旧 `*_chars` 键明确报错。
  默认 cl100k_base 词表随包分发、校验 SHA-256，无首次下载；`tokenizer` 可指定本地
  编码器定义，格式见 [tokenizers/README](src/agentic_graphrag/knowledge/tokenizers/README.md)。
- 文档元数据保存 `parser_name`、`parser_version`（适配器协议版本）、`source_mime_type`；
  文本另记 `source_encoding`，服务报告版本时另记 `mineru_version`。块保存 `breadcrumb`、
  `heading_path`、`token_count`、`tokenizer`，继承源元数据。doc_id 规则、`doc_id:index`
  格式、索引及 embedding 实现保持原契约。
- 离线回归覆盖：GBK/BOM、正文去噪与实体、双语 token 上限、层级 breadcrumb、整行表格、
  围栏保护、文件伪装、MinerU 响应及错误边界。运行配置与 HTTP 合约见
  [RUNBOOK §6.1](docs/ops/RUNBOOK.md#61-文档解析与分块bl-08)。

###### 🟡 BL-09：跨租户越权与存在性泄漏（API 层）

| 项 | 内容 |
|----|------|
| 复核决策无租户校验 | `routes/knowledge.py:145` `svc.review_queue.decide(item_id, ...)` 不传租户；`ReviewQueue.decide`（`queue.py:150`）也不校验 —— 与 `list()`（`:126`，有 `tenant_id` 过滤）不对称。租户 A 的 operator 知道 item_id 即可决策租户 B 的条目。 |
| 图实体接口无租户过滤 | `routes/knowledge.py:208` `GET /v1/graph/entities` 既无 `require_role`，`_list_entity_records`（`:234`）调用 `lister(limit=, offset=)` **不传 tenant_id**，而 `memory_graph.list_entities`（`memory_graph.py:155`）是支持 `tenant_id` 的 —— 能力存在但未使用。 |
| 反向存在性泄漏 | `routes/knowledge.py:188-190`：他租户的 query_id → 404；**不存在**的 query_id → 200。与 `:177` 注释「404 for both missing and cross-tenant (no existence leak)」相反，可用作存在性探测。 |

###### 🟡 BL-10：静默吞异常导致「成功」语义失真

2026-11-03 更新：部分 embedding/fulltext/graph 失败统一为 partial + 计数，401 为 failed；不再只检测全部 embedding 失败。以下为原始问题证据。

| 位置 | 行为 | 后果 |
|------|------|------|
| `knowledge_upload.py:68-71` | `docs.save()` 失败 `pass` | 文档未落盘，任务仍报 `queued` 并列出该 doc；注释称「任务仍记录失败尝试」，但实际**未记录任何失败信息** |
| `service_query.py:278-282` | `audit_store.save()` 失败 `pass` | 合规审计链可静默缺失 |
| `ingest_worker.py:88-96` | 逐条 embed 失败仅 warning | 全部 embedding 失败时 `result.error` 仍为 None → 任务标记 `done`，实际 0 条入索引 |
| `incremental.py:255` | `delete_relation` 失败 `pass` | AUTO_UPDATE 后旧边残留，形成双事实 |

###### ⚪ BL-11：声明但无生产者的分支 / 计数不闭合

| 项 | 证据 |
|----|------|
| `IngestStatus.REVIEW` 无生产者 | `ingest_tasks.py:18` 声明，`_ALLOWED_TRANSITIONS` 允许 `EXTRACTING→REVIEW`，但全仓库无任何 `transition(..., REVIEW)` 调用 |
| `ConflictAction.SKIP` 无生产者 | `incremental.py:28` 声明；`_decide()`（`:178`）只返回 AUTO_UPDATE / REVIEW / KEEP_OLD |
| `KEEP_OLD` 不计数 | `_collect_writes`（`:217`）只处理 AUTO_UPDATE 与 REVIEW；KEEP_OLD 既不写库也不计入 `BatchResult` 任何字段 → 批次数字不守恒 |
| 值冲突只取第一条 | `incremental.py:148` `old = value_conflicts[0]` —— 存在多条冲突边时其余被静默忽略，`_retire_conflicting_edge` 无法兑现「不留双事实」 |
| `accepted` 计数可虚高 | `incremental.py:205` `stats.get("relations", 0) or len(to_write)` —— 底层返回 0 时回退为「计划写入数」，0 写入被报成成功 |
| `persist_embeddings` 名不符实 | `cache.py:227-233` 写入的是 `{"size","hits","misses"}` 统计，**不是 embedding**，且无任何读回路径 |
| 流式缺 `request_id` | `service_stream.py:195` 调 `_finalize_chain` 未传 `request_id`（同步路径 `service_query.py:66` 传了）→ 流式审计记录 `request_id` 恒为空 |
| 流式 triage 帧不保证 | `loop_stream.py:106` 注释「clients always see a triage frame first」，但 `:107` 仅在 `force_agentic` 时发帧；`enable_triage=False` 且非 force 时无 triage 帧 |
| `_TASKS` 无界增长 | `routes/knowledge.py:22` 模块级全局 dict，只增不删，无淘汰与保留策略 |
| 复核决策非幂等 | `queue.py:150` 重复提交会覆盖 `status`/`reviewer`/`decided_at`，无终态保护 |
| 加载健壮性不对称 | `queue.py:89` `json.loads` 无 try/except（`ingest_tasks.py:116` 有）→ 单行损坏即导致 `QueryService` 初始化抛异常 |
| 上传复核置信度硬编码 | `routes/knowledge.py:68` 每次上传都以 `confidence=0.5` 入队 SPOTCHECK，非业务规则而是占位值 |

---

###### 补充审查（第二轮：设计对差）

> 以下三条来自净室设计文档与实现的逐条对差，**未经对抗式复核**。均已给出 `file:line` 证据，但落地前应按 §5 的 V-16…V-19 实测确认。

###### 🟠 BL-12：Planner 产出 DAG，Executor 按线性序执行；hop 预算与子问题数被混同

| 项 | 内容 |
|----|------|
| 证据（并行语义未兑现） | `plan_dag.py:124 ready_subquestions()` —— 返回「全部依赖已满足的待执行节点」，即并行分支 API —— 在 `planner.py:16,32` 被再导出、在 `tests/unit/test_planner_dag.py:44,51` 有单测，但**全仓库无任何运行期调用点**。Agent 循环改用 `current_index` 线性推进（`loop_runtime.py:122,162`；`loop_handlers.py:70,81,151,213`）。 |
| 证据（无并发能力） | 全仓库 `async def` 共 11 处且**全部位于 `api/`**；`agent/` 与 `retrieval/` 内无 `asyncio.gather` / `TaskGroup` / `to_thread`。 |
| 证据（hop ≡ 已执行子问题数） | `loop_runtime.py:110` —— `node_executor` 每次进入即 `guards.on_hop_start()`（`guardrails.py:128` `hop += 1`），而每次进入只处理 `idx` 指向的**一个**子问题（`:122-146`）。故 hop 计数等价于「已执行子问题数」，并非推理深度。 |
| 证据（子问题数无上限） | `planner.py:58-60` 对 LLM 返回的 `result.sub_questions` **不截断**，`normalize_plan`（`plan_dag.py:47`）亦不截断；`loop_handlers.py:249` 还会在运行期**插入**动态子问题，使总数进一步增长。 |
| 失败场景 | `configs/default.yaml` `max_hops: 4`。LLM planner 产出 6 个子问题（或产出 4 个后 critic 追加 2 个）→ 第 4 次 executor 后 `loop_handlers.py:64` `hop >= max_hops` 置 `done`（`loop_runtime.py:240` 同判据）→ 第 5、6 个子问题**永不执行且无告警**。护栏文案 `guardrails.py:134` 报 `max_hops exceeded (5/4)`，读作「推理太深」，实际触发原因是「子问题太多」。 |
| 影响 | ① 深度（多跳）与广度（子问题数）共用同一预算，两者不可独立调参，也无法从日志区分是哪一种超限；② DAG 的 `depends_on` 在运行期只影响拓扑排序**顺序**，其并行语义（同层节点可同时执行）未兑现 —— 互不依赖的分支各自消耗一个 hop，等于把广度计入深度预算；③ 延迟为 Σ(分支) 而非 max(分支)，对 AC-4（8s）不利。 |
| 备注 | 属**能力未兑现**，非死循环 —— §2「读路径闭环」结论不受影响：超预算时仍走 answer 节点产出 `ReasoningChain`。修复面小于新增功能：并行 API 与其单测已存在，缺的是运行期调用与并发载体。 |

###### 🟠 BL-13：引用门禁与 fabrication 指标只到「词面重叠」，无法判别关系是否相符

| 项 | 内容 |
|----|------|
| 门禁实为三级 | `citations.py:100-108`：① `claims_have_citations` 每条 claim 有 `evidence_ids`；② `claims_bind_to_evidence` 引用 id 必须存在于检索集合；③ `claims_lexically_supported`（`:116`，`min_overlap=1`）claim 与被引证据**至少共享 1 个内容词**。函数自陈（`:98`）：*"not a full NLI check — still better than ID-only fabrication=0"* —— 设计者已知这是代理判据。 |
| 假阳性（本条） | `min_overlap=1` 可仅靠**主语实体**满足，与关系类型无关。证据 `Apex Holdings -[FOUNDED_BY]-> 李四`、claim `Apex Holdings 的 CEO 是张三` → 交集 {apex, holdings} ≥ 1 → **通过门禁**。即：引用一条真实、已检索、但**断言了另一种关系**的证据，门禁无法拦截。 |
| 与 BL-02 互为反面 | BL-02 是同一函数的**假阴性**方向（纯中文 claim 因空白分词恒失败）。中文侧过严（必然拒绝），含拉丁 token 侧过松（仅需 1 词重叠）。**注意：修复 BL-02 的 CJK 切分会加剧本条** —— 切出 unigram/bigram 后，中文 claim 更容易凑满 1 个重叠 token 而蒙混通过。两条应一并设计，不宜先后单独修。 |
| 指标口径比门禁更弱 | `eval/metrics_evidence.py:145 fabrication_rate` 的判据 `_claims_unbound`（`:175`）**只检查 `evidence_ids` 是否非空** —— 既不校验 id 是否在检索集合内，也不做词面重叠，是三级门禁中最弱的一级。docstring（`:146`）自陈为「AC-7 proxy」，但指标名 `fabrication_rate` 读作「捏造率」。 |
| 失败场景 | 在线路径：模型引用真实 id 但误读其内容 → 门禁放行 → 输出错误答案且附带「看起来有据可依」的引用。评测路径：该行 `_claims_unbound` 返回 False → 计为「未捏造」→ `fabrication_rate` 系统性偏低。 |
| 影响 | AC-7「无据不答」的验收证据强度低于指标名义。若要真正判别关系相符需引入蕴含判定（每 claim 一次轻档 LLM 调用，或小型 NLI 模型），成本需单独评估；在此之前，至少应让指标名与实际判据一致。 |

###### 🟡 BL-14：图谱无时间维度，时效性冲突按置信度而非时间裁决

| 项 | 内容 |
|----|------|
| 证据 | 全仓库 `src/` 内 `valid_from` / `valid_to` / `valid_at` / `as_of` / `temporal` **零命中**。`graph_builder.py:39` 的 source 结构为 `{doc_id, chunk_id, span, confidence}`，实体与关系均无时间字段。 |
| 冲突按置信度裁决 | `graph_builder.py:53` `if rid not in relations or t.confidence > relations[rid].confidence:` —— 同一 `rid` 的重复关系保留**置信度更高**者。置信度来自抽取阶段，与事实新旧**无关**。 |
| 失败场景 | 语料同时含 2019 与 2025 年的 CEO 公告 → 两条 `CEO_OF` 边归一到同一 `rid` → 保留抽取置信度更高的一条（可能恰是旧闻，因其表述更规范）→ 图检索稳定返回过期答案，且 `ReasoningChain` 会为其附上**真实引用**，人工复核前不可见。 |
| 与增量路径同源 | BL-11「值冲突只取第一条」（`incremental.py:148`）与本条是同一个根因：数据模型只能表达「二选一」，无法表达「两者都对，分属不同时间区间」。`_retire_conflicting_edge` 因此只能删除旧边，历史丢失。 |
| 影响 | 对当前静态演示语料无实际损害；对任何有更新节奏的真实语料，这是**先于 BL-01 需要决策**的数据模型问题 —— 补时间字段属 schema 变更（`configs/schema/domain_v0.yaml` + 抽取提示 + 路径打分），成本显著高于补一个执行器。 |

---

<a id="design-original-comparison"></a>

### 设计与实现原始对照（含后续复核批注）

来源：[docs/design/DESIGN_VS_IMPLEMENTATION.md](docs/design/DESIGN_VS_IMPLEMENTATION.md)。

### Clean-Room Design vs. Actual Implementation

Diff of [`ARCHITECTURE_DESIGN.md`](docs/design/ARCHITECTURE_DESIGN.md) (written blind, from the project name
only) against the code on `feat/all-phases-complete` as of 2026-07-26.

**Headline: the design and the implementation agree on essentially the whole skeleton.** Of 26
designed capabilities, 20 are present and structurally equivalent, 2 are present as interface seams
without an implementation, and 4 are absent. Three places where the code deliberately does something
the design did not anticipate are, on inspection, better than the design.

> **复核（2026-11-02）**：下列差异是 2026-07-26 对 `feat/all-phases-complete` 的快照。此后 D1、D2、D3、D4、D5 相关能力陆续落地，原「4 项缺失 / 2 个空接口」的结论已不成立；§2 的 D1–D5 正文与 §5 记分卡保留原始判断，以下为当前实现位置：
> - **D1 并行分支已真正执行**：`agent/parallel_branches.py`（`ready_siblings` / `retrieve_group`）经 `agent/executor_dispatch.py` 的 `ThreadPoolExecutor` 并发，`agent/loop_handlers.py` 调用 `retrieve_for_node`；跳数预算不再按宽度计费。
> - **D2 蕴含校验已接入**：`generation/entailment.py` 的 `judge_claims` 在 `generation/answer.py` 调用；词项支持改为 CJK 感知（`generation/claim_support.py`）。`unbound_claim_rate` 保留为历史口径，`fabrication_rate` 改用严格关系一致规则。
> - **D3 时效字段已存在**：`knowledge/temporal.py`、`knowledge/incremental_conflicts.py`、`knowledge/extract_rules.py` 使用 `valid_from` / `valid_to` / `as_of`（另见 `BUSINESS_LOGIC.md` V-19）。
> - **D4 重排器已实现**：`retrieval/rerankers.py` 的 `LexicalReranker` 与 `HttpCrossEncoderReranker`（默认关闭，离线 A/B 无显著收益，见 `ARCH_OPTIMIZATION_V2.md` §7）。
> - **D5 审核回写已闭环**：`knowledge/review/executor.py` 的 `ReviewExecutor` 由 `api/routes/knowledge.py` 的决策端点驱动。
> - 仍然成立：**D6**（固定窗口分块）与 **D8 / D9 / D10**（符号化路径渲染、消融数量、人工签署）。

These findings are **not** ledgered in `docs/reviews/IMPORTANT.md`. They are an external design's opinion,
not project-sanctioned debt. Promote individual items there only if you agree they are debt.

---

#### 1. Convergent (design ≡ code)

Independently arriving at the same structure is weak evidence that the structure is forced by the
problem. These matched without qualification:

| Design element | Implementation | Note |
|---|---|---|
| Offline/live duality, offline default | `stores/factory.py`, `AGR_ALLOW_LLM` / `AGR_USE_LIVE_STORES` | Code is **stronger**: the two switches are independent, so you can test a live LLM against offline stores. The design treated "live" as one axis. |
| 4 store protocols + one composition root | `stores/interfaces.py`, `stores/factory.py` | Exact match |
| One `Candidate` shape across 3 retrievers | `retrieval/contracts.py` | Exact match |
| Versioned reasoning-chain export | `generation/trace.py` + `configs/schema/reasoning_chain_v1.json` | Exact match |
| Triage → {fast path, agentic} + escalation | `agent/triage.py:61` (`Route` has exactly 2 members), `agent/loop_policy.py` | Exact match |
| Chitchat short-circuit before triage | `agent/chitchat.py` | Exact match |
| Sub-question DAG, placeholders, topo-sort | `agent/plan_dag.py` — Kahn sort, `{from:sqN}` auto-wires `depends_on`, cycle→input order | Structurally exact |
| Critic with replan/answer verdict | `agent/critic.py` (`CriticAction`, `CriticScope`) | Exact match |
| Guardrails: hops/calls/tokens/timeout/recursion | `agent/guardrails.py`, `configs/default.yaml` | Exact match, plus extras — see §4 |
| Consecutive-failure circuit breaker | `llm/circuit.py:21` (threshold 2, cooldown, half-open) | Exact match |
| Hub-node degree defence | `retrieval/graph_beam.py:49,113` — `high_degree_threshold=30`, `looks_high_degree()` gates a hard relation-type filter | Exact match |
| Three-tier entity resolution | `knowledge/resolution.py` — docstring literally names the same three tiers | Exact match |
| Reject-never-repair schema gate | `knowledge/graph_builder.py:86` "never writes rejected", `knowledge/schema_check.py` | Exact match |
| Provenance + confidence per edge | `knowledge/graph_builder.py:39` — `{doc_id, chunk_id, span, confidence}` on relations *and* entities | Exact match |
| Extraction resume journal / retry / quarantine | `knowledge/extract_pipeline.py`, `extract_types.py` | Exact match |
| Abstention as a first-class outcome | `QueryStatus.NO_ANSWER` + `honest_fallback()`, wired at 5 sites (guardrail trip, recursion recovery, no evidence, model no-answer, citation failure) | Exact match |
| Citation binding, regenerate-once, then abstain | `generation/citations.py`, `generation/answer.py:221` | Match — but see **D2** |
| RRF fusion | `retrieval/fusion.py` | Exact match |
| Cache: index-version invalidation, tenant-scoped answers | `retrieval/cache.py:133,147` — retrieval key = `tenant|norm(query)|tools+version`; answer key = `tenant|user|params|version` | Exact match |
| SSE incremental streaming | `agent/loop_stream_events.py` — `triage/thinking/sub_question/hop_done` | Exact match |
| RBAC, tenancy, budgets, audit events | `api/rbac.py`, `llm/budget_policy.py`, `observability/audit_events.py` | Exact match |
| Evidence recall + fabrication rate | `eval/metrics_evidence.py:93,145` | Match — but see **D2** |
| Vector-RAG baseline on identical cases | `eval/baseline_rag.py` | Exact match |
| Deterministic gold gen by graph walk | `eval/gold_gen.py` + `eval/gold_templates/` | Exact match |
| Critic non-convergence defence | `agent/loop_handlers.py` — `memory.is_duplicate_subquestion()`, `memory.is_excluded()` | The design named this as a *risk mitigation*; code has it |

---

#### 2. Divergences — design specifies, code lacks

Ordered by how much they matter.

##### D1 · The DAG is built but never executed as a DAG ⚠ highest impact

`plan_dag.py:124 ready_subquestions()` — which returns *all* pending nodes whose dependencies are
satisfied, i.e. the parallel-branch API — is defined, re-exported from `planner.py:16,32`, and
unit-tested (`tests/unit/test_planner_dag.py:44`), **but has no runtime caller.** The agent loop
instead walks `current_index` linearly (`loop_handlers.py:70,81,151,213`), one sub-question per
executor node visit.

Confirming evidence: the codebase is synchronous — 11 `async def` total, all in the API layer. There
is no `asyncio.gather` / `TaskGroup` / `to_thread` anywhere in `agent/` or `retrieval/`.

**Consequence.** The DAG buys dependency-correct ordering and placeholder binding — both real — but
not latency. For the doc's own worked example, `q2` (CEO) and `q3` (HQ) are independent and both
depend only on `q1`; they run back-to-back instead of together. On an *n*-branch question, latency is
Σ(branches) where it could be max(branches). Given `query_timeout_seconds: 45` and an unmet 8s AC-4
target, this is the largest unexploited latency win in the system, and most of the work — the graph
model, readiness predicate, and its tests — is already done.

**Worse than latency.** `loop_runtime.py:110` calls `guards.on_hop_start()` (`guardrails.py:128`,
`hop += 1`) on *every* executor visit, and each visit handles exactly one sub-question. So **the hop
counter is a count of executed sub-questions, not of reasoning depth** — breadth is billed to the
depth budget. With `max_hops: 4`, a plan of 6 sub-questions silently drops the last two
(`loop_handlers.py:64` sets `done` at `hop >= max_hops`), and the guardrail reports
`max_hops exceeded (5/4)`, which reads as "too deep" when the cause was "too wide". Nothing caps
`result.sub_questions` at `planner.py:58-60`, and `loop_handlers.py:249` can insert more mid-run.

##### D2 · The citation gate stops at lexical overlap; *entailment* is not checked

The runtime gate (`citations.py:100-108`) is three tiers, not one: every claim must (1) carry
`evidence_ids`, (2) cite an id present in the retrieved set, and (3) share **≥1 content token** with
the cited evidence (`claims_lexically_supported`, `min_overlap=1`). The function is self-aware —
*"not a full NLI check — still better than ID-only fabrication=0"*.

Tier 3 is satisfiable by the **subject entity alone**, independent of the relation asserted. Evidence
`Apex Holdings -[FOUNDED_BY]-> X` and claim *"Apex Holdings' CEO is Y"* share `{apex, holdings}` and
pass. The gate cannot tell which relation the evidence states.

Evaluation is weaker still: `metrics_evidence.py:145 fabrication_rate()` uses `_claims_unbound()`
(:175), which checks **only that `evidence_ids` is non-empty** — not tier 2, not tier 3. Its docstring
says "AC-7 proxy"; its name reads as "fabrication rate".

**Failure mode.** A claim citing a real, retrieved, but relationally-wrong evidence id passes the
gate, is never regenerated, never triggers `honest_fallback`, and scores as non-fabricated. Note this
is the mirror of the project's own BL-02 (pure-Chinese claims fail tier 3 unconditionally, because
`_content_tokens` splits on whitespace): tier 3 is simultaneously too strict for CJK and too loose
for Latin text. **Fixing BL-02 with CJK segmentation makes this worse** — unigrams make the ≥1
overlap easier to satisfy. The two must be designed together.

##### D3 · No temporal validity on edges

Zero occurrences of `valid_from` / `valid_to` / `valid_at` / `as_of` / `temporal` anywhere in `src`.
The graph carries `confidence` and provenance but no time scope.

**Consequence.** Time-scoped relations — `ceo`, `headquartered_in`, ownership, pricing — are stored
as timeless. Re-ingesting a corpus that contains both the 2019 and 2025 CEO produces two `ceo` edges
with no way to prefer the current one; `graph_builder.py:53` resolves the collision by **higher
confidence**, which is uncorrelated with recency. The design's staleness mitigation (validity fields
+ recency term in path scoring) has no counterpart in code. Low urgency on a static demo corpus,
blocking for any real deployment with an update cadence.

##### D4 · Reranker is a seam, not an implementation

`fusion.py:28,34` defines a `Reranker` Protocol and an `IdentityReranker` that returns its input
unchanged; `executor.py:76` threads an optional `reranker` through. No cross-encoder exists.

This is **correct engineering** — the interface is defined at the right place and nothing premature
was built behind it — but the design's F-11 should read "rerank seam present, unimplemented" rather
than "optional rerank."

##### D5 · The correction loop does not close

`review/queue.py:150 decide()` sets `status`, `reviewer`, `decision_note`, `decided_at` and persists.
Nothing reads approved items back into a `GraphStore`. The API route
(`api/routes/knowledge.py:133`) records a `review_decision` audit event and returns.

**Consequence.** In the design's Figure 2, `feedback → review queue → curator → graph` is a closed
cycle; in code the last arrow is missing. A curator can approve a correction, and the graph is
unchanged. Feedback is currently an *audit* mechanism, not a *repair* mechanism. This is the one
divergence where the design's diagram is actively misleading about the code, and it is a small piece
of work relative to what it unlocks (the KG becoming an asset that improves).

**Already known.** `docs/design/BUSINESS_LOGIC.md` records this independently as **BL-03**, with one detail
this diff missed: `IncrementalUpdater._append_review` (`incremental.py:269`) writes pending conflicts
to a *separate* JSONL that `ReviewQueue` never reads, so `GET /v1/review-queue` cannot surface them
at all. Treat BL-03 as the authority for this finding.

##### D6 · Chunking is fixed-size, not structure-aware

`knowledge/ingest.py:49 chunk_text(chunk_size=1200, overlap=150)` — a sliding window that prefers a
break at `\n\n`, `。`, or `. ` past ⅓ of the window. No heading, list, or table handling; nothing
propagates document structure into chunk metadata. Tables in particular will be split mid-row.

##### D7 · Anchor linking is closed-vocabulary, with no embedding fallback

`agent/entity_mentions.py` resolves mentions via quoted spans, a lexicon built from the seed triples
(`default_lexicon_from_seed`), title-case spans, CJK spans, and `_fuzzy_lexicon_hit` (:188). There is
no embedding fallback.

**Consequence.** An entity absent from the lexicon cannot anchor, so graph traversal never starts and
the query silently degrades to BM25/vector. This is a defensible design — anchoring only to entities
that exist in the graph avoids a class of false starts — but it makes anchor recall a hard function
of lexicon coverage, and there is no metric isolating anchor-miss from retrieval-miss.

##### D8 · Path verbalization is symbolic, not natural language

`retrieval/graph_paths.py:58–68` renders paths as `A -[RELATION#id]-> B` arrow notation. The design
called for natural-language verbalization (`"Acme Robotics is a subsidiary of Zenith Group"`) so the
generator sees one uniform evidence format. Minor, and arguably better for a machine reader — but it
means graph evidence and text evidence reach the LLM in visibly different formats, which is a known
source of uneven attention across evidence types.

##### D9 · Only one ablation exists

`scripts/p3_ev_offline.py:91 _run_triage_ablation()` → `triage_ablation.json` (P3-EV-02, marked `[~]`
in `plan/phases/phase-3-optimization.md:44`). There is no `−graph`, `−fusion`, or `−critic` ablation.

**Consequence.** The vector-RAG baseline (`eval/baseline_rag.py`) establishes that *the system* beats
*plain RAG*. It cannot attribute that delta to the graph specifically, nor tell you whether the
critic loop or RRF fusion earns its cost. For a system whose entire thesis is "the graph is what
makes multi-hop work," the component-level evidence for that thesis is currently missing.

##### D10 · Human gold sign-off is pending

`evals/datasets/GOLD_SIGNOFF.md` — every checklist box unchecked, `**Signed:** _(pending)_`. The
design flagged graph-walk gold cases as self-confirming and required a human-authored supplement;
that supplement is scaffolded but unsigned. Consistent with, and a direct cause of, `formal_pass:
false`.

---

#### 3. Where the code is right and the design was wrong

##### E1 · Retrieval is conditional, not always-three-way

The design asserted all three retrievers always run and fuse, justified by uncorrelated failure
modes. The code disagrees deliberately:

- `executor_plan.py::build_heuristic` sets `skip_vector=has_graph` — **vector search is dropped when
  graph specs are already present**, with the comment *"Skip remote embedding when graph already
  expanded (AC-4 latency)."*
- `choose_tools()` skips the LLM tool-planning call entirely when heuristics already produced a
  `graph_neighbors` / `graph_path` / `graph_subgraph` handle: *"Remote LLM tool-planning is expensive
  (seconds–tens of seconds). Use it only when heuristics have no graph/path handle."*

Both are correct calls under a live P95 target; remote embedding and an extra LLM round-trip are the
two dominant latency costs, and the design ignored that in favour of a tidy principle.

Worth naming honestly, though: this **partially spends the redundancy argument**. On exactly the
queries where the graph produces a confident handle, the semantic retriever that would have caught a
wrong or incomplete graph is not consulted. The trade is latency-for-recall on graph-anchored
queries, which is probably right — but it means "three uncorrelated retrievers" describes the
capability, not the hot path, and D9's missing `−graph` ablation is what would tell you the true cost.

##### E2 · Guardrails are coupled, not independent

`guardrails.py:20 min_recursion_limit(max_hops) = 2*max_hops + 5`, derived from the worst-case node
visit count, and applied via `max(configured, floor)` in both `from_app_config` and `with_overrides`
— *"Never ship a recursion_limit that the hop budget can exhaust."* Plus a `hard_max_hops=20` ceiling
that per-request overrides cannot exceed.

The design listed hop cap and recursion limit as two independent guardrails. Treating them as
independent is a latent bug — a caller raising `max_hops` would hit an unrelated LangGraph recursion
error instead of a clean hop-budget stop. The code prevents that structurally.

##### E3 · Domain guards patch a known critic weakness

`agent/critic_guard.py::force_incomplete_multihop` forces another hop when a person-role question
("who is the CEO of…") has evidence that names organisations but no person. This is a pragmatic patch
for the critic under-calling `continue` on partially-answered multi-hop questions — a failure the
design named as a risk but offered no mechanism against.

---

#### 4. Unanticipated in the design

**The offline heuristic answer layer.** `generation/offline_heuristics/rules_*.py` — 7 rule modules
(~940 lines) hardcoded to the demo corpus, coverage-omitted, driving `--no-llm` determinism. The
design assumed a mock LLM would suffice for offline determinism; the reality is a rule engine large
enough to be its own maintenance surface. It is honestly labelled in `CLAUDE.md` as not the
production path, and quarantined behind the omit list. Still: it is the single largest divergence in
*volume* between designed and actual system, and the design has no slot for it.

---

#### 5. Summary scorecard

| # | Capability | Status |
|---|---|---|
| F-01 | Structure-aware chunking | ◐ fixed-size + boundary preference (**D6**) |
| F-02 | Extraction w/ journal, retry, quarantine | ● |
| F-03 | Ontology gate, reject-and-record | ● |
| F-04 | Three-tier entity resolution | ● |
| F-05 | Typed edges + confidence + provenance | ● 时效字段已补（**D3** 已落地） |
| F-06 | Multi-index build | ● |
| F-07 | Incremental update + review queue | ● |
| F-08 | Swappable stores, offline default | ● |
| F-09 | Three retrievers | ◐ conditional, not always-on (**E1**) |
| F-10 | Beam search, path scoring, verbalization | ◐ symbolic rendering (**D8**) |
| F-11 | RRF fusion + rerank + cache | ● rerank 已实现（lexical / HTTP cross-encoder，默认关闭；**D4** 已落地） |
| F-12 | Triage + escalation | ● |
| F-13 | Sub-question DAG | ● 就绪分支并发执行（**D1** 已落地） |
| F-14 | Executor + evidence pool | ● |
| F-15 | Critic replan-or-stop | ● |
| F-16 | Guardrails | ● (**E2** improves on design) |
| F-17 | Grounded synthesis + citation binding | ● |
| F-18 | Faithfulness + abstention | ● 蕴含校验已接入（**D2** 已落地） |
| F-19 | Reasoning-chain export | ● |
| F-20 | Sync + SSE API | ● |
| F-21 | RBAC, tenancy, budgets | ● |
| F-22 | Logs, metrics, traces, audit | ● |
| F-23 | Feedback → curator → graph | ● 审核决策回写图谱（**D5** 已落地） |
| F-24 | Gold generation | ◐ graph-walk yes, human sign-off pending (**D10**) |
| F-25 | Accuracy/recall/fabrication/latency/cost | ◐ fabrication measures binding (**D2**) |
| F-26 | Baseline + ablations | ◐ baseline yes, 1 of 4 ablations (**D9**) |

● present · ◐ partial · ○ absent

**If you act on three things:** close the review→graph write-back (**D5**, small, unlocks the
knowledge flywheel), execute ready DAG branches concurrently (**D1**, the parallel API and its tests
already exist), and either add an entailment check or rename `fabrication_rate` to match what it
measures (**D2**).

## 2026-07-25

<a id="important-snapshot-5"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

ENT 实施完成（RPA 除外）：ENT-01/02/03/04/05/06/08 代码 + 单测已交付，ENT-07/RPA 按范围不实施。`pytest tests/unit --cov` 287 passed / 83.3%，ruff/format PASS；仍需 check_code_metrics 拆分收口及 Redis/Neo4j/Qdrant/OTLP 部署验证。

<a id="important-snapshot-6"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

**文档同步（ENT 收口）**：runbook 全量增补（RBAC/错误码对照/日志字段/四点回查/worker/保留清理/告警规则）；ENTERPRISE_READINESS 内部矛盾清理（§4/§5 状态与 §3.5 对齐）；README / ARCHITECTURE / phase-4/5 / cicd-observability / api-and-ui / CLAUDE.md 对齐实现。静态复核：全部 src 文件 `wc -l` ≤300（最大 `config.py` 298）；`check_code_metrics`（函数级）与全套门禁**复跑仍待运行环境**

<a id="enterprise-delivery"></a>

### 企业能力交付批次关闭

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

| 批次 | 任务 | 状态 | 门禁挂钩 | 剩余工作量 |
|---|---|---|---|---|
| ~~第 1 批（G4 前必做）⏫~~ | ~~ENT-01、ENT-03~~ | ✅ 已交付 | ~~G4"监控告警/审计回查生产验证"~~ | 0 |
| ~~第 1 批剩余 ⏫~~ | ~~ENT-06（租户隔离 + 启动校验）~~ | ✅ 已交付（2026-07-25） | P4-REL-01 代码侧已承接（运维侧仍开） | 真后端回归归部署侧 |
| ~~第 2 批（试点期）~~ | ~~ENT-02、ENT-04、ENT-08 Prometheus~~ | ✅ 已交付 | ~~P4-REL-03 告警落地~~ | 0 |

<a id="enterprise-audit-coverage"></a>

### 企业审计覆盖范围与检索结果

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

#### 附录 A：审计覆盖文件（2026-07-25 更新）

`api/`（app、auth、errors、envelope、schemas、rbac、routes/query、routes/knowledge、routes/admin、service、service_query、service_stream、sse）、`observability/`（metrics、trace、logging_setup、audit_events、redaction）、`llm/`（budget、budget_policy、circuit）、`agent/`（guardrails、loop_stream_events）、`generation/audit_store.py`、`knowledge/review/queue.py`、`stores/`（interfaces、factory、memory_graph、vector_store、doc_store、neo4j_store）、`config.py`、`web/static/js/api.js`、`docs/ops/RUNBOOK.md`、`plan/engineering/cicd-observability.md`、`plan/phases/phase-4-pilot.md`、`phase-5-scale.md`、`docs/reviews/IMPORTANT.md`、`plan/roadmap.md`、`PRD.md`（需求索引）。

新增覆盖（2026-07-25）：`observability/logging_setup.py`、`observability/audit_events.py`、`observability/redaction.py`、`api/rbac.py`、`api/routes/admin.py`、`tests/unit/test_enterprise_readiness.py`、`tests/unit/test_logging_setup.py`。

检索确认零命中（2026-07-25 复核）：`webhook/callback_url`（src+configs，佐证 ENT-07 未实施）、`CORSMiddleware`。已不再成立：~~`opentelemetry`~~ → `observability/otel_bridge.py` 在可选依赖内使用（`otel` extra，无 SDK 时 no-op）；~~`logging/getLogger`~~ → 现有 `observability/logging_setup.py` + `observability/audit_events.py` + `api/rbac.py` + `api/app.py` 使用 `logging`。

## 2026-07-24

<a id="important-snapshot-4"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

企业级管控审计：排障/权限/并发/数据安全/审计 🟡 部分、日志集成/RPA 集成 ❌ 缺失（**全 src 零应用日志**）；规划 P5-ENT-01…08 见 `docs/reviews/ENTERPRISE_READINESS.md`（**仅规划未实施**；ENT-01/03/06 标 G4 前置）

<a id="enterprise-original-audit"></a>

### 企业能力原始缺口审计

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

#### 2. 现状盘点（证据）

##### A. 全链路排障

已有：

- 每请求 `request_id`（`X-Request-Id` 或 uuid4），随 envelope meta 返回，异常时注入 error details — `src/agentic_graphrag/api/routes/query.py:37`、`api/app.py:56`
- 机器可读错误码集合（INVALID_INPUT/BUDGET_EXCEEDED/RATE_LIMITED/…）— `api/errors.py:26`
- SSE `error` 事件（code + 异常类型名，不泄内部细节）— `api/service_stream.py:77`
- `/healthz` 分依赖浅检（graph/vector/ping + 前后端 backend 标识 + allow_llm）— `api/app.py:91`
- 护栏触发后兜底摘要（explored paths）— `agent/guardrails.py:167`；GraphRecursionError 恢复 — `agent/loop_recover.py`
- LLM 连续失败熔断（半开恢复）— `llm/circuit.py`；Neo4j 不可达回退内存图 — `stores/factory.py`
- 抽取管线 journal/retry/quarantine — `config.py:100`（KnowledgeConfig）
- 排障手册（常见故障 5 类 + 告警建议）— `docs/ops/RUNBOOK.md`
- badcase 归因 / spotcheck CLI — `eval/badcase.py`、`cli/spotcheck.py`
- 查询级 trace（span + query_id 关联）与指标（P50/95/99、错误码计数、budget_trips）— `observability/trace.py`、`observability/metrics.py`

缺口：

1. **全仓库零应用日志**：`src/`、`scripts/` 无任何 `import logging`/`getLogger`/structlog。未处理 500 仅靠 uvicorn 控制台栈回溯，无 request_id/租户字段，无落盘；SSE 错误对客户端只给类型名，服务端**无处**留存根因。
2. trace/metrics 均为进程内环形缓存（`trace.py:92`、`metrics.py:108`），重启即失；trace **无任何 API/CLI 可查**（路由仅 query+knowledge，无 `/v1/traces`）。
3. API `request_id` 与推理链 `query_id` 的映射仅存在于单次响应 envelope，不入审计链（`api/service_query.py:197` 只写 tenant/user/query_id）— 事后凭网关日志无法回查链路。
4. 摄取任务注册表为进程内 dict 且**无 worker 消费**，状态永远 `queued`（`api/routes/knowledge.py:21,77`）— 排障死胡同。
5. `/healthz` 不反映 LLM 熔断器状态与审核队列积压，降级不可见。

##### B. 权限管理

已有：

- API Key→租户映射、Bearer/X-Api-Key 双头、`AGR_REQUIRE_AUTH` 强制开关、租户级限流一体中间件 — `api/auth.py:115`
- 用户预算身份默认绑定 key 摘要，客户端不可伪造 `X-User-Id`（`AGR_TRUST_X_USER_ID` 显式信任）— `api/auth.py:63,169`
- Web 工作台 API Key 输入（localStorage + Bearer）— `web/static/js/api.js:5`

缺口：

1. **无角色/权限模型**：任意合法租户 key 可调用运营端点 — 审核决议 `POST /v1/review-queue/{id}/decision`、全局指标 `GET /v1/metrics`、文档上传 `POST /v1/docs`、图谱浏览（`api/routes/knowledge.py:139,195,49,202`）。审核队列列表**跨租户可见**。
2. **默认开放**：`AGR_REQUIRE_AUTH` 未设置时所有端点匿名可用（`auth.py:165` 直接放行为 default 租户）— 非 secure-by-default。
3. Key 无生命周期：明文列在 env、无过期/轮转/吊销、无哈希存储；无 SSO/OIDC 对接点。
4. `/web`、`/docs`、`/openapi.json` 永久公开（`auth.py:133`）— 试点可接受，生产需可配。

##### C. 并发调度

已有：

- 租户级 QPS 窗口 + 并发在飞上限，超限 429 — `api/auth.py:69`
- 三级预算（租户/用户/查询，日窗口，check_and_reserve/commit/release 原子）— `llm/budget_policy.py:46`
- 单查询护栏：hops/LLM 调用/token/墙钟超时/递归上限，请求覆盖不越服务器硬顶 — `agent/guardrails.py:31`
- 查询路径不持全局锁，SSE 慢客户端不阻塞他人 — `api/service.py:70`、`service_stream.py:99`
- 检索三路并行 + RRF（`retrieval.parallel`）；答案/检索缓存落盘且键含租户/用户 — `retrieval/cache.py`

缺口：

1. **单进程内存态**：限流窗口、预算用量、指标、trace、审计索引、审核队列索引、摄取任务全在进程内 — 多副本部署即失效（各副本独立配额 = 限额 ×N；audit JSONL 追加写还会交错）。无分布式协调（Redis 等）。
2. 预算限额是代码默认值（`budget_policy.py:57`），**不能按租户在配置中定制**，改限额要改代码。
3. 无请求排队/优先级/公平调度：超限直接 429，无队列缓冲；无按租户 SLA 分级。
4. 摄取无调度器/worker：上传只建任务记录，抽取管线只能 CLI 手跑；无定时任务框架。
5. Checkpointer 默认 MemorySaver（磁盘 SQLite 仍是可选项，见 IMPORTANT §2）— 进程重启丢中断查询状态，也阻塞后续异步作业恢复能力。

##### D. 数据安全

已有：

- 密钥仅 env/pydantic-settings，启用 LLM 时校验占位符 — `api/service.py:242`；仓库无硬编码密钥（2026-07-23 架构审计确认）
- Cypher 全参数化 + 标签/关系名白名单正则 — `stores/neo4j_store.py:33,41`
- 入参校验（question ≤2000 字、hops/timeout 有界）— `api/schemas.py:13`
- 错误响应不泄内部细节；审计回查对"不存在"与"跨租户"统一 404 防探测 — `api/app.py:75`、`routes/knowledge.py:172`
- 缓存命中二次校验租户，防串答 — `api/service_query.py:110`
- 前端零 `v-html`/innerHTML（rules.md 评审强制）

缺口：

1. **租户数据隔离缺失**（P4-REL-01 仍开）：DocStore/图谱/向量库无租户维度，任何租户的查询检索**全体语料**；上传文档不打租户标。当前隔离仅覆盖：审计链回查、答案缓存键、预算桶。
2. 上传无大小/类型/数量限制（`routes/knowledge.py:49` 直接读全量），runbook 把限制推给反向代理 — 应用层应兜底。
3. 审计链含用户原始问题与证据全文，**明文 JSONL、无脱敏钩子、无保留期/轮转**。
4. 无静态加密立场说明；无安全响应头/CORS 策略配置点（当前同源部署可接受，需文档化）。
5. 启动期密钥存在性校验不完整：仅 LLM 路径检查；live stores 凭据缺失要到首次连接才暴露。

##### E. 审计日志

已有：

- 推理链 100% 落盘（JSONL + 内存索引），按 query_id 回查、租户隔离 — `generation/audit_store.py:36,56`、`GET /v1/audit/queries/{id}`
- 反馈写回链 metadata 并入审核队列（负反馈）— `api/service.py:168`
- 查询指标含 tenant/user/error_code；预算触发记录 trips — `observability/metrics.py:12`、`budget_policy.py:196`

缺口：

1. **无安全/管理事件审计**：鉴权失败、限流/熔断触发（对象与时间）、审核决议（谁批了什么）、文档上传、配置变更 — 均无记录（也无日志可兜底）。
2. 无导出/对接：不能按时间段/租户批量导出；无 SIEM 友好格式。
3. JSONL 只增不轮转；更新（反馈附加）以追加重复行实现，重启后靠 last-wins（`audit_store.py:44`）— 无防篡改（哈希链/签名）设计。
4. `GET /v1/metrics` 返回**全局**汇总（含各错误码计数），未按租户过滤；`MultiLevelBudget.snapshot()` 有实现但无端点暴露。

##### F. 日志集成

- 设计已写：结构化 JSON 日志、全量携带 query_id/tenant_id、告警阈值表、OTel 全链路 span 规范 — `plan/engineering/cicd-observability.md` §3。
- **实现为零**：无日志器、无 JSON formatter、无文件输出；`/v1/metrics` 是自定义 JSON 而非 Prometheus 文本协议；无 OTel SDK 接线。ELK/Grafana/SIEM 均无可接入面。⚠ 差异（设计有、实现无）。

##### G. RPA 流程集成

已有地基（可复用）：

- 统一 envelope + 机器可读错误码 + OpenAPI（/docs、/openapi.json）
- 推理链 JSON Schema 契约 — `configs/schema/reasoning_chain_v1.json`（`export-reasoning-schema` 再生）
- SSE 事件流（triage/thinking/sub_question/hop_done/answer/error）— `agent/loop_stream_events.py`、`api/sse.py`
- 上传→task_id→`GET /v1/ingest-tasks/{id}` 轮询骨架；审核队列可编程消费（list/decide）
- 全套 CLI 子命令可脚本化（`python -m agentic_graphrag <cmd>`）

缺口：

1. **无 webhook/回调/事件推送**（全仓库无相关实现）；审核队列只能拉不能推。
2. **无异步查询作业**：Agentic 查询（live P95 ~92s，见 IMPORTANT §0）必须挂着 HTTP 连接等答案 — RPA 集成最typical的"提交-轮询/回调"模式不存在。
3. 摄取任务无状态机推进（无 worker，见 C-4）— 轮询接口形同虚设。
4. 无幂等键（重试即重复扣预算/重复建任务）、无批量查询端点、无按时间段导出推理链的接口。
5. API 版本策略仅 `/v1` 前缀约定，无弃用/兼容政策文档。

---

<a id="enterprise-plan-1"></a>

### ENT-01 原始实施规划

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

| 项 | 内容 |
|----|------|
| 交付 | 新模块 `observability/logging_setup.py`：stdlib logging + JSON formatter（无新依赖）；`contextvars` 承载 request_id/query_id/tenant_id/user_id，中间件注入、跨 agent 循环传播；uvicorn access log 并入同格式；`AGR_LOG_LEVEL`/`AGR_LOG_FILE`(+轮转) 环境开关 |
| 落点 | 中间件挂 `api/app.py`；异常处理器（`app.py:75`）、SSE 兜底分支（`service_stream.py:84`）、预算/护栏触发、store 回退路径补 error/warning 日志 |
| 验收 | 任一 4xx/5xx/SSE error 在服务端恰有一条含 request_id+tenant_id+错误码（5xx 含栈）的 JSON 日志；离线 20 case 跑完日志可按 query_id 串起 triage→hops→answer |
| 约束 | 模块 ≤300 行；日志内容经脱敏钩子（预留，ENT-06 实现）；MockLLM 路径零成本 |

<a id="enterprise-plan-2"></a>

### ENT-02 原始实施规划

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

- `request_id` 写入链 metadata（`service_query.py:_finalize_chain` 一行）+ SSE 首事件返回 request_id/query_id。
- 新端点（admin 角色，依赖 ENT-04）：`GET /v1/traces/{query_id}`（暴露既有 Tracer）、`GET /v1/budget/snapshot`（暴露既有 `MultiLevelBudget.snapshot()`）。
- `/healthz` 增加 LLM 熔断器状态、审核队列积压数、checkpointer 后端标识。
- 摄取任务状态机：`queued→extracting→review→done/failed`，任务落盘（复用 JSONL 惯例）替代进程内 `_TASKS`。
- `docs/ops/RUNBOOK.md` 增补：错误码→根因→处置对照表（错误分类学），日志字段字典。
- 验收：任一失败查询可凭 request_id 走通"网关→日志→审计链→trace"四点回查。

<a id="enterprise-plan-3"></a>

### ENT-03 原始实施规划

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

- 新模块 `observability/audit_events.py`：append-only JSONL（`data/processed/audit_events.jsonl`），事件含 ts/tenant/user/action/target/outcome。
- 采集点：鉴权失败（`auth.py:_authenticate`）、限流与预算熔断（携对象租户）、审核决议（reviewer+decision）、文档上传、反馈提交。
- 轮转与保留：按大小/日期滚动，保留期入 `configs/default.yaml`（评审定，cicd-observability §3.4 已留待办）。
- 导出：`agr-audit-export --since --tenant --format jsonl|csv`（CLI，入 `cli/`）。
- 防篡改（后置项）：行级 prev-hash 链；规模化立项后再评估签名。
- 验收：runbook 新增"安全事件回查"手册段；每类事件在单测中可断言落盘字段完整。

<a id="enterprise-plan-4"></a>

### ENT-04 原始实施规划

来源：[docs/reviews/ENTERPRISE_READINESS.md](docs/reviews/ENTERPRISE_READINESS.md)。

- Key 格式扩展：`AGR_API_KEYS=tenant:key:role`（role ∈ admin/operator/reader，缺省 reader，向后兼容两段式）；或 `configs/api_keys.yaml`（支持过期时间、备注；env 优先）。
- 路由级依赖检查：admin → 审核决议、budget snapshot、traces、docs 上传、metrics；reader → query/stream/feedback/audit 自租户。
- `GET /v1/metrics` 按租户过滤（admin 可看全局）。
- 安全默认：发布配置模板将 `AGR_REQUIRE_AUTH=1` 设为生产缺省；`/docs`/`/openapi.json` 公开性可配。
- 明确不做（本期）：SSO/OIDC、细粒度资源 ACL、key 服务端哈希存储 — 记 §8。

## 2026-07-23

<a id="important-snapshot-2"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

审计修复：租户缓存/审计隔离、全局锁/SSE、计划状态机、RRF 合并、引用门禁收紧、UI API Key；**3-hop heldout 仍须重跑**

<a id="important-snapshot-3"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

静态架构审计通过（边界/分层/密钥无违规）；新增 `docs/ARCHITECTURE.md`（模块地图 + 优化建议 P-A1…A5 **仅记录未实施** + 验证清单）；本环境 `.venv` 缺失，门禁未跑

## 2026-07-22

<a id="important-snapshot-1"></a>

### IMPORTANT 状态变更

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

badcase：no_answer 评分 + critic 续跳；真域剧本 `docs/ops/REAL_DOMAIN_PLAYBOOK.md`；P95 脚手架 `p3_load_http.py`

<a id="environment-snapshot"></a>

### 当时的 live 环境观察

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

| 项 | 当时状态 |
|---|---|
| 本环境 | SenseNova chat OK；embedding 易 401（需 `LLM_EMBEDDING_*`）；Qdrant 常未起 |

## 2026-07-21

<a id="runtime-change-3"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

P5-UI-01 / ADR-006：§3 增加 Vue 3.5.13 运行时（浏览器 ESM / vendor，**非 npm**）与 §3.1 vendor 小节

## 2026-07-20

<a id="runtime-change-1"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

首次记录：`/tmp` Temurin 17.0.19 + Neo4j 5.26.0 tarball；对照 `docker-compose.yml` 的 neo4j/qdrant 镜像

<a id="runtime-change-2"></a>

### 外部运行时变更

来源：[docs/ops/EXTERNAL_RUNTIMES.md](docs/ops/EXTERNAL_RUNTIMES.md)。

补充：tarball Neo4j 已能 start，但初始密码/已有 data 导致认证失败；增加重置步骤

<a id="code-size-closeout"></a>

### 超大文件合规拆分关闭

来源：[docs/reviews/IMPORTANT.md](docs/reviews/IMPORTANT.md)。

- 超大文件合规拆分（2026-07-20）**已完成**；新模块保持约 200–400 行（`plan/engineering/repo-structure.md`）。

## 2026-07

<a id="arch-v2-original-review"></a>

### 架构优化 V2 原始核对、基线与第一批验证（含后续 CI 批注）

来源：[docs/design/ARCH_OPTIMIZATION_V2.md](docs/design/ARCH_OPTIMIZATION_V2.md)。

### 架构优化 V2：核对结论与精细化设计

> 状态：**第一批（O-1 ~ O-8）已在 `refactor/arch-v2` 上实现并通过验证**（验证记录见 §7），其余项见 §5。
> 基线：`8942ddb`（Batch 1 + CI 门禁）。
> **后续状态（2026-11-02）**：§1 / §6 / §7 记录的是当时的离线基线与门禁；当前 CI 门禁已提高到 **POC 30/31、G2 196/200**，另加 probe `52/54` 与 `19/20`，金标审计改为 `scripts/audit_gold.py --max-flagged 0`（**阻塞合并**）。因此文中的 21/31、167/200 与「审计标记 29 题且只出报告」均为历史值。
> 输入：外部《架构优化评估》（结论摘要 + §1–§10）。
> 方法：逐条对照代码（标注 `file:line`），并在离线 Profile 下实测 `poc_cases`（31 题）和 `g2_all`（200 题）。
> 关联文档：[REFACTOR_PLAN.md](docs/design/REFACTOR_PLAN.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · 前一份《架构对效果的影响评估》。

---

#### 0. 结论先行

1. **评估的大方向成立，但有 6 处与代码不符**，会直接影响优先级排序（§2 用 ✗ / ◐ 标出）：
   - 度数大于 30 的节点**没有被丢弃**，只是改走关系类型硬过滤，过滤后没有结果会再无过滤重试（`retrieval/graph_beam.py:70-86`）。所以“枢纽实体被系统性丢失”不成立。
   - NLI **不是每个 claim 调一次**，而是把所有 claim 拼成一次 LIGHT 调用（`generation/entailment.py:83-94`）。
   - 缓存键**不是原文**，已经做了小写化和空白归一（`retrieval/cache.py:27-28`）。
   - 边到 chunk 的溯源**已经存在**：`RelationRecord.sources[].chunk_id` 在构图时写入（`knowledge/graph_builder.py:60-71`），Neo4j 也会持久化（`stores/neo4j_codec.py:26-31`）。缺的是**检索阶段没有把它带出来**。
   - Fast→Deep 升级时**会继承 Fast 已拿到的证据**（`agent/fast_path.py:51`），并不是“白白多花一轮”。
   - IndexVersion 比评估说的**更粗**：它是全局单计数器，不分租户（`core/versioning.py:14-27`）。任意租户写入都会让所有租户的缓存失效。
2. **“效果瓶颈在检索排序”只说对了一半。** 实测 g2 的 33 个失败中，只有 6 个的标准答案出现在证据里。但另外 27 个失败的主因是**规划把 3 跳压成了 1 跳**（`ceo_parent_producer` 模板 0/14，全部只生成了 1 个子问题），以及**金标本身的缺陷**（金标审计结果：13 道题在图里有多个合法答案，金标只给了一个；16 道题的 gold_path 把互逆关系写成了反向标签）。重排器有价值，但它不能修复规划和金标问题。
3. **两个 P0 架构风险被证实，而且比评估描述的更严重：**
   - **BM25 不跨进程**：独立 worker 写进的是自己进程内的 BM25（`knowledge/ingest_worker.py:176-179`），从不落盘；API 只在启动时读一次 `bm25.json`（`stores/factory.py:137-138, 170-174`）。
   - **BM25 租户泄漏**：`save()` 不写 `tenant_id`（`stores/fulltext_store.py:96-108`），重新加载后 `tenant_id=""`；而 `search()` 把 `""` 视为对所有租户可见（`:83`）。**重启之后，所有租户都能检索到彼此的 chunk。**
   - **缓存失效静默失败**：`InProcessEventBus.publish` 吞掉所有异常（`core/events.py:96-100`），`invalidate` 失败时 IndexVersion 不会递增，用户会一直拿到旧答案，也没有指标或告警。
4. **评测集确实太小，而且有标注缺陷。** 31 题的 95% Wilson 区间是 [50.1%, 81.4%]，宽 31pp；200 题是 [77.7%, 88.0%]，宽 10pp。另外 g2 只用了 7 个模板；open 类 30 题全部是 1 跳的路径解释题；审计标记了 29 道题（13 道金标有歧义、16 道互逆标签写反，例如 `Northwind Analytics PARENT_OF Apex Holdings`）；poc 的 gold_path 是手写的非规范格式，31 题中有 18 题无法被机器校验。**扩容之前必须先做金标审计**，否则规模越大，噪声越大。

---

#### 1. 实测基线（离线，`--no-llm --memory-graph`）

| 数据集 | 正确 | 准确率 | 95% Wilson CI | 备注 |
|---|---|---|---|---|
| `poc_cases` | 21/31 | 67.7% | [50.1, 81.4] | 当时基线（现 CI 门禁 30/31） |
| `g2_all` | 167/200 | 83.5% | [77.7, 88.0] | 当时基线（现 CI 门禁 196/200）；pilot 三元组 |
| ├ 2hop | 82/90 | 91.1% | | |
| ├ 3hop | 35/60 | 58.3% | [45.7, 69.9] | **主要短板** |
| ├ open | 30/30 | 100% | | 全部来自 `competes_path` 一个模板，区分度为零 |
| └ no_answer | 20/20 | 100% | | 只有 phantom 实体一种 |

g2 按模板：`ceo_parent_producer` 0/14、`competitor_of_producer` 1/5、`ceo_competitor_producer` 35/46、`ceo_of_parent` 36/39、`ceo_prior_employers` 45/46。

**解读**：失败集中在“产品 → 生产商 → …”这类链条。规划器把 `the parent company of the producer of X` 识别成 `What is the parent company of X?`，一跳都没有命中，结果是 `no evidence retrieved`。这是规划问题，加重排器解决不了。

---

#### 2. 逐条核对

图例：✓ 与代码相符　◐ 部分相符或需修正　✗ 与代码不符

##### 2.1 Agent

| 原文判断 | 结论 | 证据 |
|---|---|---|
| critic 每 hop 都调 LLM | ✓ | `agent/loop_runtime.py:161-186`：只要 `allow_llm`，就走 `_llm_critique`（`agent/critic.py:174-177`） |
| 离线有确定性 critic 可用 | ✓（原文没提到） | `agent/critic_offline.py`，加上 `critic_guard.force_incomplete_multihop` |
| `max_llm_calls=16`，`timeout=45s` | ✓ | `configs/default.yaml` 中的 guardrails |
| planner 一次生成 DAG，之后靠 rewrite 修补 | ✓ | `agent/planner.py` + `planner_patterns.py`；实测 3 跳被压成 1 跳 |
| 误判 Fast 后要“白白多花一轮” | ◐ | 升级时会继承 Fast 的证据（`agent/fast_path.py:51`），损失的是 Fast 那一轮的延迟，证据并没有浪费 |

##### 2.2 检索

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 没有重排器，只定义了协议 | ✓ | `retrieval/fusion.py:27-38`：只有 `IdentityReranker` |
| 过滤度数大于 30 的节点 | **✗** | `graph_beam.py:113-120` 只用于判断；判定为高度数后改用 `preferred_relations` 硬过滤，结果为空则无过滤重试（`:70-86`） |
| 三路权重固定 | ✓ | `core/evidence.py:202` 是无权重的 RRF |
| 1200 字定长切分 | ◐ | 1200 字，overlap 150，会在句边界回退（`knowledge/ingest.py:52-80`），不是硬切 |
| 缓存按原文精确匹配 | **✗** | `normalize_query_key` 做了小写和空白归一，但确实没有语义缓存 |

##### 2.3 知识图谱

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 逐块抽取，块间没有上下文 | ✓ | `knowledge/extract_chunk.py` |
| 边和 chunk 之间缺少溯源 | **◐** | 存储层**有**（`graph_builder.py:60-71`，`neo4j_codec.py:26-31`），检索层**没带出来**（`retrieval/graph.py:166-187` 的 Citation 不含 chunk_id） |
| Schema 固定，Schema 外的关系被拒 | ✓ | `configs/schema/domain_v0.yaml`（5 种实体、14 种关系）+ `rejected_triples.jsonl` |
| 消歧逐对判定，超线性增长 | ✓ | `knowledge/resolution.py:135-145`：同类型内 O(n²) |

##### 2.4 存储

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 全文检索只有 BM25 一个实现 | ✓ | `stores/factory.py:93-94` |
| 多进程之间可能不共享 | **✓（已证实）** | worker 只写进程内存（`ingest_worker.py:176-179`）；API 只在启动时加载（`factory.py:137`） |
| 新发现：租户泄漏 | **✓（新）** | `fulltext_store.py:96-108` 的 save 丢弃 `tenant_id`；`:83` 把 `""` 视为全局可见 |
| 新发现：每次 `index()` 全量重建 | ✓（新） | `fulltext_store.py:63-70` 每次都重算整个语料的 IDF，写入成本 O(N) |
| 三个存储之间没有事务 | ✓ | `app/write_path.py` 分别写入 |
| 租户只靠过滤隔离 | ✓ | 各个 store 都用 `tenant_id` 过滤 |
| 新发现：hybrid Profile 用的是内存向量 | ✓（新） | `bootstrap/profile.py`：HYBRID = Neo4j + **memory** vector。worker 写入的向量对 API 同样不可见，与 BM25 是同一类问题（列入 S-2） |

##### 2.5 LLM 网关

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 连续 2 次失败就熔断 20s | ✓ | `llm/circuit.py:21-22` |
| 没有降级目标 | ✓ | `llm/provider.py:150-151`：熔断打开后直接抛错 |
| 新发现：chat 的 STRONG、LIGHT 和 embedding **共用同一个熔断器** | ✓（新） | `provider.py:47`：只有一个 `self.circuit`；embedding 端点故障会把 chat 一起熔断，STRONG 故障也会连带 LIGHT 不可用，因此原文的“降级到 LIGHT”在现有结构下**无法实现** |
| 新发现：`embed_many` 逐条调用 | ✓（新） | `provider.py:133-134` |
| JSON + 校验重试 | ✓ | `llm/structured.py:39` 用的是 `json_object`，不是 `json_schema` |

##### 2.6 生成

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 每个 claim 调一次 LLM 做 NLI | **✗** | 全部 claim 合并成一次调用（`entailment.py:83-94`） |
| 判定器弃权即视为通过 | ✓ | `entailment.py:72-78` |
| 置信度是规则式的 | ✓ | `generation/confidence.py` |

##### 2.7 可观测

| 原文判断 | 结论 | 证据 |
|---|---|---|
| trace_id 和 ReasoningChain 不一定能互相跳转 | **◐（更严重）** | 整个 `src/` 里**没有 `trace_id` 字段**；ReasoningChain 不记录任何 trace 关联 |
| 订阅者异常只记日志 | ✓ | `core/events.py:96-100` |
| 新发现：健康检查里的熔断状态永远不会上报 | ✓（新） | `api/app.py:216-220` 读取 `svc._circuit_breaker`，但整个代码库里没有任何地方给它赋值（列入 X-1） |
| IndexVersion 按租户 | **✗（更粗）** | 全局单计数器（`core/versioning.py:14-27`） |

##### 2.8 评测

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 31 题时 CI 约 ±16pp | ✓ | Wilson 区间 [50.1, 81.4] |
| 需要扩容到 ≥300 条 | ◐ | 先要审计金标：g2 中 13 道题金标有歧义，16 道互逆标签写反，open 类只有 1 个模板且全是 1 跳 |
| 300 条能检出 5–8pp 差异 | ◐ | 非配对比较时，n=300 的最小可检出差异约为 11pp；**配对 McNemar** 才能到 5–8pp，前提是 A/B 跑同一批题 |
| 没有分模块评测 | ✓ | `eval/` 只有端到端评测和证据召回（2026-07 核对时的结论；此后新增 `badcase.py` / `scoring_strict.py` / `stats.py` / `compare.py` / `gold_audit.py` / `search_drift.py` / `drift_alignment.py`） |

---

#### 3. 修订后的优先级

| 优先级 | 项 | 理由 |
|---|---|---|
| **P0** | E-1 统计显著性与配对对比；E-2 金标审计 | 没有这两项，后面所有 A/B 结论都不可信 |
| **P0** | S-1 BM25 租户泄漏 + 跨进程共享 | 正确性和安全问题，不是调参能解决的 |
| **P0** | O-1 事件可靠投递（缓存失效） | 失败时会静默返回旧答案 |
| P1 | R-1 两阶段召回 + 可插拔重排 | 原文的 P0；离线 A/B 没有显著收益（§7），降为 P1，等 live A/B 结果再定 |
| P1 | K-1 边到 chunk 溯源透出 | 数据已经有了，只差检索层透出，成本低 |
| P1 | A-1 分层 critic | LLM 调用减少可以确定 |
| P1 | L-1 熔断滑窗 + 档位降级 | 可用性 |
| **P0** | P-1 规划修复（QueryFrame，见上一份报告） | 3 跳只有 58% 的直接原因（`ceo_parent_producer` 0/14） |
| P2 | 其余项 | 见 §5 |

---

#### 4. 第一批精细化设计（本次实现）

每一项都满足：离线行为不变（当时基线 poc 21/31、g2 167/200），默认关闭或向后兼容，并有单测。

##### O-1（E-1）评测统计与配对对比

- **新增** `eval/stats.py`：
  - `wilson_interval(k, n)`；
  - `mcnemar(a_correct, b_correct)`：精确二项检验，返回 `b01 / b10 / p_value`；
  - `breakdown(rows, cases, key)`：按 `category`、`metadata.template` 分组统计准确率。
- **新增** `scripts/eval_compare.py`：输入两个运行报告 JSONL 和用例文件，输出整体准确率和 CI、分组准确率、配对 McNemar 结果、P50/P95 延迟、LLM 调用均值。
- **合并规则**（写入 `rules.md`）：架构 A/B 必须报告配对 p 值；p < 0.05 且准确率、成本、延迟三者都没有明显退化，才能合并。

##### O-2（E-2）金标审计

- **新增** `eval/gold_audit.py` 和 `scripts/audit_gold.py`。对每个用例，基于三元组做确定性检查：
  1. `gold_path` 的每条边在图中存在（允许互逆：PARENT_OF ↔ SUBSIDIARY_OF，COMPETES_WITH 视为对称）。不存在则报 `path_edge_missing`；只有反向存在则报 `path_direction`。
  2. 从锚点沿 `relation_chain` 遍历，得到全部合法答案集合。若集合大小大于 1 而金标只给了 1 个，报 `ambiguous_gold`。
  3. 金标答案不在合法答案集合中，报 `gold_unreachable`。
- 输出 JSON 报告，并按问题类型计数。**不自动修改数据集**；修正走人工复核。CI 暂不拦截，只产出报告。

##### O-3（S-1）全文检索：修复租户泄漏 + 跨进程共享

- `BM25FulltextStore.save/load` 写入和读取 `tenant_id`。读到旧格式（没有该字段）时保持 `""`，与当前行为一致，并记录一条警告。
- **新增** `SharedFileBM25Store(path)`：
  - `index()` 之后原子写入（临时文件 + `os.replace`）；
  - `search()` 之前检查文件的 `mtime_ns`，有变化就重新加载。
  - 这样 API 和 worker 只要挂同一个卷，就能看到彼此的写入。
- 工厂：Profile 为 `live` / `hybrid` 时使用 `SharedFileBM25Store(indexes_dir/bm25.json)`，`offline` / `eval` 仍用进程内实现。能力矩阵把 fulltext 标为 `shared-file`。
- **说明**：这是在现有部署形态下的正确性修复。多副本或跨主机部署时，应改用服务端实现（Qdrant 稀疏向量或 OpenSearch），列为 S-2，见 §5。

##### O-4（O-1）事件可靠投递

- `InProcessEventBus.subscribe(event_type, handler, *, critical=False, retries=0, on_failure=None)`：
  - 普通订阅者：行为不变；
  - critical 订阅者：失败后按 `retries` 重试；仍失败则调用 `on_failure(event, exc)`，计数加一（`handler_failures[name]`），并通过 `observability.metrics` 记 `agr_event_handler_failures_total{event,handler}`。
- `write_path`：`invalidate` 注册为 critical，`retries=2`，`on_failure` 设为**清空检索缓存和答案缓存**（fail-safe：宁可缓存全部失效，也不返回旧答案）。

##### O-5（R-1）两阶段召回 + 可插拔重排

- 配置 `retrieval.rerank`：
  ```yaml
  rerank:
    kind: none        # none | lexical | http
    candidate_pool: 30
    top_n: 10
    endpoint: ""      # http: TEI / Infinity 兼容的 POST /rerank
    timeout_seconds: 2.0
  ```
- `retrieval/rerankers.py`：
  - `LexicalReranker`：离线可用。按“问题 token 与候选内容的重叠 + 图候选中命中实体加分”重排，同分时保持 RRF 顺序。
  - `HttpCrossEncoderReranker`：调用 `{"query", "texts"}`，读取返回的 `[{"index", "score"}]`。超时或出错时退回输入顺序，并在候选 metadata 中标 `rerank_fallback`。
  - `build_reranker(cfg)` 工厂函数。
- `app/wiring.py` 把 reranker 注入 `Executor`（构造参数已存在：`agent/executor.py:79/107`）。`kind != none` 时，各路召回的 top_k 提升到 `candidate_pool`。
- 默认 `none`，行为不变。

##### O-6（K-1）边到 chunk 溯源透出

- `retrieval/graph.py` 的邻居候选、`graph_paths.py` 的路径候选：从 `RelationRecord.sources` 里取 `doc_id`、`chunk_id`，加入 `Citation`，并写入 `structured["source_chunk_ids"]`。
- 生成阶段的引用检查和 NLI 可以凭这些 id 回查原文（后续接入，列为 K-2）。

##### O-7（A-1）分层 critic

- `guardrails.critic_mode: llm | tiered | offline`，默认 `llm`，与现状一致。
- `tiered`：先运行 `offline_critique`。若结果是“确定性结论”，就不调 LLM；否则再调 LIGHT 模型。确定性结论指：
  - `ANSWER` 或 `NEXT_HOP`，且能抽取出实体结论；
  - 或者证据为空，直接给出 NEXT_HOP / GIVE_UP。
- 记录 `critic_tier` 到 trace 和 metadata，以便统计 LLM 调用节省比例。用单测中的计数 LLM 验证。

##### O-8（L-1）熔断滑窗 + 档位降级

- **熔断器按路由拆分**：`chat:strong`、`chat:light`、`embed` 各用一个熔断器（`CircuitBreakerSet`），这是降级的前提。
- `CircuitBreaker` 新增 `window_seconds` 和 `min_calls`：在时间窗口内，失败率达到 `failure_rate` 且调用数不少于 `min_calls` 时熔断；原有“连续 N 次失败”的规则保留，作为兜底。
- `OpenAICompatibleProvider`：STRONG 档熔断时，若配置 `llm.fallback_to_light: true`，改用 LIGHT 模型完成调用，并在响应 metadata 中标 `degraded_from=strong`。默认关闭。

---

#### 5. 后续批次（只做设计，不在本批实现）

| ID | 项 | 设计要点 | 验收 |
|---|---|---|---|
| E-3 | 评测扩容到 ≥300 | 先完成 E-2 审计和修正；新增模板覆盖 inverse / intersection / qualifier / missing_relation / false_premise（见《评测集扩充提示词》） | 每个子类型 ≥30 条，heldout 不参与调优 |
| E-4 | 分模块评测 | 路由 acc、检索 recall@k / MRR（以 gold_evidence 为准）、抽取 P/R、消歧 pairwise F1、NLI acc、置信度 ECE | 每项有基线文件 |
| P-1 | QueryFrame 规划 | 见上一份报告；直接修复 `ceo_parent_producer` 0/14 | 3hop ≥ 80% |
| R-2 | PPR 图检索 | 应用层 PPR，以问题实体为种子；Neo4j 可选 GDS 实现 | 3hop recall ↑，P95 +<100ms |
| R-3 | 查询自适应融合 | `weighted_rrf(weights_by_qtype)` | 各分类不退化 |
| R-4 | 父子分块 | 子块 350 字参与检索，返回父块，引用落到子块 | 证据精度 ↑ |
| R-5 | 语义缓存 | embedding 相似度 ≥0.95 且实体集合相等才命中 | 误命中率 <0.5% |
| S-2 | 全文服务化 | `QdrantSparseFulltextStore`（BM25 / SPLADE） | 多副本一致 |
| S-3 | Outbox + 分路版本 | `IndexVersion` 改为 `(tenant, channel)` 两级版本；写侧用 outbox | 失败可以重放 |
| K-2 | 溯源接入生成 | 图证据带回原文 span，交给 NLI | 引用准确率 ↑ |
| K-3 | 文档级抽取 | 先做实体和指代表，再逐块抽关系 | 实体碎片 ↓ |
| K-4 | blocking 消歧 | 先按名称和类型分桶，桶内聚类，边界样本才交给 LLM | LLM 调用 ↓ |
| G-1 | 专用 NLI | 用 cross-encoder 批量判定，可与重排共用服务 | 延迟 ↓，准确率不降 |
| G-2 | 置信度校准 | isotonic 校准，需要 E-3 的样本量 | ECE ↓ |
| L-2 | 原生 json_schema 输出 | 在 `structured.py` 中按 provider 能力选择 | 重试率 ↓ |
| X-1 | trace_id 贯通 | ReasoningChain、日志、OTel span 共用同一个 id | 可以端到端跳转 |
| X-2 | 租户级 IndexVersion | 与 S-3 合并 | 跨租户不再互相失效 |

---

#### 6. 验证方法

- 每个提交都必须通过：ruff、format、`check_code_metrics`、单测覆盖率 ≥80%、离线评测 poc 21/31（agentic + triage）。（当时门禁）
- 本批另外要求：g2_all 保持 167/200（默认配置下行为不变）。（当时基线）新开关打开时（`rerank.kind=lexical`、`critic_mode=tiered`），用 `scripts/eval_compare.py` 出配对报告，结果记入 §7。

#### 7. 本批验证记录

所有提交都在 `refactor/arch-v2` 上，每个提交都通过了 ruff、format、code metrics 和单测（最终 385 passed，覆盖率 86.12%）。

| 提交 | 项 | 默认配置下的行为 | 验证 |
|---|---|---|---|
| `4e2bf4e` | O-1 / O-2 评测统计与金标审计 | 无变化 | g2 审计：200 题中 29 题被标记（ambiguous 13、inverse_label 16）；poc 31 题中 18 题的 gold_path 无法机器校验 |
| `da95fc3` | O-3 BM25 租户与共享 | offline / eval 不变；live / hybrid 改为 shared_file | 单测覆盖：重启后不再跨租户可见；worker 写入后 API 可见；快照损坏时保留上一次的正常状态 |
| `7ee91fa` | O-4 事件可靠投递 | 普通订阅者不变 | 单测：重试成功、重试耗尽后执行 fail-safe、fail-safe 自身异常被隔离；失效失败时缓存被清空，并计入 `event_handler_failures` |
| `844e0f3` | O-5 两阶段召回 + 重排 | `kind=none`，不变 | 见下方 A/B |
| `147846f` | O-6 边到 chunk 溯源 | 只是增加字段 | 单测：邻居候选和路径候选都带有 `source_chunk_ids` |
| `d76268d` | O-7 分层 critic | `critic_mode=llm`，不变 | 见下方 A/B |
| `b7a5c62` | O-8 熔断与降级 | 与 V1 等价（阈值 2，冷却 20s，无窗口，不降级） | 单测：路由之间相互隔离、窗口失败率触发熔断、STRONG 熔断后降级到 LIGHT |

**离线评测（默认配置）**：poc 21/31（agentic 和 triage 都是），g2 167/200（agentic 和 triage 都是），与基线完全一致。

**O-5 重排 A/B**（同一份图、同一批题，配对 McNemar）：

| 配置 | poc | g2 | p 值 | P95 延迟 |
|---|---|---|---|---|
| 无重排（基线） | 21/31 | 167/200 | — | 51 / 63 ms |
| lexical | 20/31 | 167/200 | 1.0 / 1.0 | 38 / 57 ms |
| cross-encoder（ms-marco-MiniLM-L-6-v2，CPU，HTTP） | 21/31 | 167/200（5 题变对、5 题变错） | 1.0 / 1.0 | **833 / 465 ms** |

结论：在当前的离线答案生成器（基于图启发式选答案）下，**重排对准确率没有显著影响，在 CPU 上还会让 P95 延迟增加 0.4–0.8 秒**。这与 §1 的诊断一致：失败主要来自规划和金标，而不是排序。重排的价值要等到**真实 LLM 生成**时，通过 live A/B 来确认（需要 GPU 或托管的 bge-reranker-v2-m3）。因此 R-1 保持默认关闭，**不建议把它作为 P0**。

**O-7 分层 critic**：离线运行时统计每次带证据的 critic 调用。

| 数据集 | 带证据的 critic 调用 | 确定性闸门可以直接判定的 | 可以省掉的 LLM critic 调用 |
|---|---|---|---|
| poc | 46 | 38 | **83%** |
| g2 | 346 | 309 | **89%** |

在 live 模式下，相当于每题约省 1.2 次 LLM 调用（poc）。这一项能否保持准确率不下降，需要在 live 环境下用 `AGR_CRITIC_MODE=tiered` 跑配对对比，并用 `scripts/eval_compare.py` 判定。

**实验开关**：`AGR_RERANK=none|lexical|http`，`AGR_CRITIC_MODE=llm|tiered|offline`。对比命令：

```bash
agr-run-cases --cases evals/datasets/g2_all.jsonl --no-llm --memory-graph \
  --seed-triples data/processed/pilot_triples.jsonl --run-name base
AGR_CRITIC_MODE=tiered agr-run-cases ... --run-name cand
python scripts/eval_compare.py reports/base.jsonl reports/cand.jsonl \
  --cases evals/datasets/g2_all.jsonl --fail-on-worse
```

**CI（当时）**：`eval-offline` job 新增 g2 门禁（下限 167/200）和金标审计报告（只出报告，不拦截合并）。

**CI（2026-11-02 现状）**：`eval-offline` 门禁为 POC 30/31、G2 196/200，并新增 probe `52/54` 与 `19/20`；金标审计 `scripts/audit_gold.py --max-flagged 0` **阻塞合并**。

<a id="refactor-fixes"></a>

### 架构核实中发现并修复的问题

来源：[docs/design/REFACTOR_PLAN.md](docs/design/REFACTOR_PLAN.md)。

#### 核实中额外发现、已修复的问题

| 问题 | 位置 | 处理 |
|---|---|---|
| live 模式会静默降级：Qdrant 初始化失败无条件回退内存向量；`QueryService.create_live` 默认 `allow_memory_graph_fallback=True`，Neo4j 不可达时 API 带空内存图启动 | `stores/factory.py`、`api/service.py` | ✅ 移除隐式回退；Neo4j 和 Qdrant 都会 ping；混合部署须声明 `AGR_PROFILE=hybrid` |
| Neo4j 下运行期实体对齐、图谱浏览、词表刷新全部空转（`getattr(graph, "list_entities")` 只有内存图实现） | `knowledge/graph_ingest.py` 等 | ✅ 实现 `Neo4jGraphStore.list_entities()` 并加入 `GraphStore` 协议 |
| 工具注册表与调度表命名不一致（`graph_paths` vs `graph_path`），注册表从未被使用 | `agent/tools/registry.py` | ✅ 统一命名，调度全部改走注册表 |
