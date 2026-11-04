# 技术选型与决策记录（ADR）

**约定**：选型变更必须先在本文件追加 ADR 条目（编号递增，含背景/决策/理由/影响），评审通过后再动代码。状态：`提议` / `已采纳` / `已废弃`。

## 1. 选型总表（初始建议，待 POC 前定稿）

| 层 | 组件 | 建议 | 备选 | 状态 |
|---|---|---|---|---|
| 图数据库 | GraphStore | Neo4j（生态成熟，Cypher 表达力强，团队上手快） | NebulaGraph（数据超亿级边时切换） | 已采纳（ADR-001） |
| 向量库 | VectorStore | Qdrant（POC 默认）；与图库解耦 | Milvus/pgvector | 已采纳（ADR-002） |
| 全文检索 | FulltextStore | POC：rank_bm25 进程内；规模化可换 ES/OpenSearch | Elasticsearch | 已采纳（POC） |
| LLM | LLMProvider | 强/轻双档位，OpenAI 兼容网关，供应商可替换（NFR-10） | — | 已采纳（ADR-003） |
| 服务框架 | API | Python（FastAPI）——LLM/检索生态最全；POC 先 CLI | Go/TS | 已采纳（ADR-004） |
| 前端 | 试用界面 | Vue 3 零构建（钉版 3.5.13，运行时 ESM） | Preact+htm / Alpine | 已采纳（ADR-006） |
| 编排 | Agent 框架 | **LangGraph（StateGraph）+ 自研控制逻辑**（护栏/Memory去重/推理链） | AgentScope（仅深度绑定 Qwen/DashScope 生态时重议） | 已采纳（ADR-005） |
| 可观测 | Trace/监控 | OpenTelemetry + 现有监控栈 | — | 待定 |

> 参考开源实现（立项书附录）：Microsoft GraphRAG、LightRAG、HippoRAG —— POC 前安排 2-3 天源码调研，可借鉴索引构建与社区摘要思路；Agent 循环按本项目设计基于 LangGraph 实现（ADR-005）。

## 2. ADR 条目

### ADR-001：图数据库选 Neo4j（已采纳，2026-07 POC 启动）
- **背景**：PRD 开放问题 #1；试点领域数据量预估在千万边以下。
- **决策**：POC/MVP 用 Neo4j Community；GraphStore 接口抽象隔离，数据规模超阈值时迁移 NebulaGraph。
- **理由**：生态成熟、Cypher 学习曲线平缓、单机即可支撑试点规模；团队图谱经验不足（风险登记 R4），选最低摩擦方案。
- **影响**：图检索工具基于 Cypher 参数化模板实现；迁移成本被接口抽象控制在检索层内部。

### ADR-002：向量库独立于图库 / Qdrant（已采纳，2026-07 POC 启动）
- **背景**：部分图库带向量能力，但成熟度参差。
- **决策**：向量检索用独立 **Qdrant**；不用图库内置向量功能。POC 另提供 `InMemoryVectorStore` 便于离线测试。
- **理由**：三路检索独立演进（NFR-10）；Qdrant 单容器运维成本低。

### ADR-003：LLM 双档位 + 统一网关（已采纳，2026-07 POC 启动）
- **背景**：Agent 多轮调用成本高（风险 R2）；不同角色对模型能力要求不同。
- **决策**：Planner/Critic/生成用强档位，Executor 工具选择/分诊用轻档位；全部经统一 LLMProvider 接口（OpenAI 兼容）+ 缓存 + BudgetTracker。
- **影响**：成本核算与预算熔断（FR-OP-02）在网关层统一实现。

### ADR-004：服务端语言 Python/FastAPI（已采纳，2026-07 POC 启动）
- **背景**：需快速迭代 Prompt 与检索策略。
- **决策**：Python ≥3.12 + FastAPI + Pydantic（schema 校验天然满足 NFR-07）。POC 阶段以 CLI/脚本驱动，API 路由预留。
- **影响**：异步 IO 支撑检索并行化（FR-RT-05）。

### ADR-005：Agent 循环采用 LangGraph 运行时 + 自研控制逻辑（已采纳，2026-07 修订）
- **背景**：原提议为全自研轻量循环。经 LangGraph vs AgentScope vs 组合方案评估后修订本决策。
- **决策**：Agent 循环基于 LangGraph 的 `StateGraph` 实现（依赖下限见 `pyproject.toml`：`langgraph>=0.2.60`；升级须过评测回归）：分诊/Planner/Executor/Critic/生成为节点，Critic 动作枚举映射为条件边路由。护栏、Memory 去重、推理链构建、引用拦截等控制逻辑仍自研，作为节点/状态逻辑挂载。**不引入 LangChain 的检索与链抽象**——检索走本项目工具接口，LLM 调用仍走自研网关（ADR-003）。
- **理由**：
  - 本项目 Agent 层是显式状态机（分诊→Planner→Executor→Critic→条件回环+硬性出口），与 StateGraph 的节点+条件边模型一一对应，几乎无适配成本。
  - 免费获得工程地基：checkpoint（状态持久化，服务 FR-AN-04 审计回放）、事件流 `astream_events`（直接映射 SSE 事件，FR-API-02）、中断/恢复、`recursion_limit` 最终兜底——这些正是原自研方案中成本最高的健壮性管道。
  - 降低团队 Agent 经验不足风险（R4）。
- **否决的备选**：
  - **AgentScope**：面向自主多智能体消息传递/会话与模拟场景，Qwen/DashScope 集成强、中文文档好；但本项目的 Planner/Executor/Critic 是"单一确定性管线中的角色"而非自主对话智能体，在消息传递框架上实现确定性护栏与状态控制需与框架对抗。仅当团队深度绑定 Qwen/DashScope 生态时重议。
  - **LangGraph + AgentScope 组合**：编排职责重叠（循环控制/记忆/追踪双份）、双运行时双学习曲线（放大 R4）、故障排查面翻倍，且无任何组件需要 AgentScope 的增量能力。否决。
  - **全自研（原方案）**：重试/中断/流式/checkpoint 管道的自研成本高于其带来的可控性收益；可控性通过"控制逻辑自研 + 框架仅作运行时"同样达成。
- **影响**：`agent/loop.py` 为 StateGraph 组装与编译；langgraph 版本锁定，升级须过评测回归（新增风险 R11）；框架依赖限定在 `agent/` 模块内，其余模块无感知，保留替换回自研的退路。

### ADR-006：试用 Web 前端采用 Vue 3 零构建（已采纳，2026-07-21）
- **背景**：rules.md §8 原约束 POC~试点「无前端框架」；阶段四交付的原生 JS 试用 UI（`web/static/app.js` 命令式 DOM，~15 个 render/事件函数）在会话历史、逐 turn 反馈、流中中止、失败重试、复制推理链、健康状态等交叉状态下维护成本超标。tech-stack.md §3「前端技术栈」待决；rules §8 预留「阶段五引入框架需 ADR + 更新 EXTERNAL_RUNTIMES.md」通道。详见执行计划 [phases/p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md)。
- **决策**：试用界面采用 **Vue 3**，钉版 **3.5.13**，产物为 `vue.esm-browser.prod.js`（全量构建，含浏览器内模板编译）。以**运行时 ESM 动态 import** 引入，**不**引入 npm / 打包器 / Node 工具链：
  1. 本地 vendor 优先：`web/static/vendor/vue.esm-browser.prod.js`（一次 vendor 即完全离线，见 `web/static/vendor/README.md`）
  2. 钉版 jsDelivr 镜像
  3. 钉版 unpkg 兜底
  - 组件用 **Options API**；组件模块为纯对象（不 import Vue），框架面收敛在 `web/`。
  - 启动链：`index.html`（in-DOM 根模板）→ `app.js`（`loadVueRuntime` → `createApp` → `registerComponents` → mount）。
- **理由**：
  - 零构建实质保留（无工具链），符合 rules §8 与 EXTERNAL_RUNTIMES「非 npm」边界。
  - 声明式模板适合引用角标切分、路径 chips、进度/反馈状态机等列表密集渲染；Options API + in-DOM 模板上手成本低。
  - 钉版 + vendor-first 可断网演示；CDN 仅作未 vendor 时的开发兜底。
- **否决的备选**：
  - **React**：无 JSX/构建链时人机工学差，违背零构建实质。
  - **Preact + htm**：体积最小，但模板即标签字符串、生态/中文资料弱于 Vue，团队上手成本高。
  - **Alpine.js**：指令式点缀适合开关，不适合引用角标切分、路径 chips 等列表密集渲染。
  - **htmx**：服务端返回 HTML 片段的范式；本项目 SSE 契约是 JSON 事件流，需重写服务端，否决。
- **影响**：
  - `web/` 模块化：`app.js` 加载器 + `js/root.js` / `js/api.js` / `js/chain-view.js` / `js/components/*`；CSS 拆为 `app.css` / `chat.css` / `panels.css`。
  - 注入安全：动态文本一律 mustache / `textContent`；**禁止 `v-html` 与任何 `innerHTML`**（替代原 `escapeHtml` 条款，见 rules.md §8 V1.1）。
  - 升级流程：改版本必须同步三处——本 ADR、`app.js` 的 `VUE_VERSION` 与加载清单、vendor 文件；过 [p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md) §7 验证清单后合入。
  - 仍明确不做（V1）：多轮上下文、图谱编辑、移动端适配、路径编辑器。

> **2026-10-31 更新**：前端已按 `Prompt.md` v2 重做为「推理笔记本」（[UI_DESIGN](../../docs/design/UI_DESIGN.md) §10）：`js/app.js`（壳 + hash 路由 + 全局快捷键）· `js/shell/*` · `js/views/chat.js` + `js/chat/*` · `js/admin/*` · `js/store/*` · `js/i18n.js`；样式 `css/{tokens,base,shell,notebook,inspector,wizard,admin,responsive}.css`（各 ≤300 行）；图标几何为 vendored Lucide 子集（`web/static/vendor/lucide-icons.js`，生成器 `scripts/build_lucide_icons.mjs`，记录见 [docs/ops/EXTERNAL_RUNTIMES.md](../../docs/ops/EXTERNAL_RUNTIMES.md) §3.2）。仍为零构建、无 npm / 打包器、无新运行时框架，本 ADR 决策不变；响应式布局差异（原「V1 不做」项）挂账见 [docs/reviews/IMPORTANT.md](../../docs/reviews/IMPORTANT.md) §6。

> **2026-11-02 更新**：Vue 运行时改为**静态 ESM import**——各模块直接 `import … from '../vendor/vue.esm-browser.prod.js'`（入口 `web/static/js/app.js`、`web/static/js/product/main.js`），**已移除**上文第 2/3 条的 jsDelivr / unpkg CDN 兜底与 `VUE_VERSION` 常量；vendor 文件已入库。ADR-007 的独立多租户应用由 `web/application.html` + `js/product/*` 提供，并新增 `vue-router`（记录见 [docs/ops/EXTERNAL_RUNTIMES.md](../../docs/ops/EXTERNAL_RUNTIMES.md) §3.3）。

### ADR-007：独立多租户 Web 应用（已采纳，2026-11-01）

- **背景**：2026-10-31 会话已确认账号、Cookie 会话、租户成员及设置页方案，
  并明确要求与 GraphRAG 核心独立；用户已批准在 `feat/multitenant-webapp` 分支实施。
- **决策**：新增 `agentic_graphrag.webapp` 和独立 `agr-webapp` 工厂；身份数据库采用
  SQLAlchemy 2 / Alembic，开发默认 SQLite，可配置 PostgreSQL。Argon2id、PyJWT、
  SQLAlchemy、Alembic 与 PostgreSQL 驱动均归入 `webapp` 可选依赖组。
  核心 API、CLI 和离线评测不导入此模块、不初始化身份数据库。
- **会话**：JWT access / 随机 refresh 凭证使用 HttpOnly、SameSite Cookie；刷新轮换、
  数据库撤销、CSRF、来源检查和持久化登录限流。角色始终从当前成员关系读取。
  数据库 API Key 只存摘要，继续支持核心 API 的 `AGR_API_KEYS` 服务身份。
- **前端**：在现有本地 Vue 3.5.13 上增加本地钉版 Vue Router 4 ESM，使用 history
  路由，无构建工具；认证、设置及应用壳独立于业务组件。视觉沿用当前 UI_DESIGN / Prompt。
- **影响**：公开注册与 Vue Router 是本轮用户明确批准的产品范围扩展，替代旧 V1
  对这两项的排除。数据隔离仍在核心存储执行，仅传递字符串 tenant_id / user_id；
  不引入身份表外键或 Web 依赖。实现与验证记录见
  [多租户实施记录](../workstreams/multitenant-webapp.md)。

### ADR-008：依赖解析与安装统一到 uv（已采纳，2026-11-02）

- **背景**：`uv.lock` 已入库，但本地文档一律用 `uv pip install -e ".[...]"`、CI 用
  `pip install -e ".[...]"`——两者都不读锁文件；`uv lock --check` 显示锁文件已落后于
  `pyproject.toml`。结果是"锁定文件"既不约束开发环境也不约束 CI，同一提交在不同机器上
  可能解析出不同依赖集合（设计与缺陷清单见 [SETUP_DESIGN.md](../../docs/design/SETUP_DESIGN.md) §1.2 S-01/S-02）。
- **决策**：依赖解析、环境同步与脚本执行统一走 uv。本地入口为 `./scripts/setup.sh`
  （内部执行 `uv sync --extra …`），CI 为 `uv sync --locked` 与 `uv run --locked …`；
  新增 `.python-version`（`3.12`）并入库，作为解释器版本事实源；`uv.lock` 为唯一版本
  事实源并保持 `uv lock --check` 通过。
- **理由**：不引入新工具——uv 已是本仓库文档中的既有工具，且当前系统未安装 `python3.12`，
  uv 可按 `.python-version` 自动获取解释器（本机系统 Python 为 3.14，与 CI 的 3.12 不一致）。
  一次 `uv sync` 同时完成解释器选择、虚拟环境创建、本包 editable 安装与锁文件校验；
  `--locked` 把"锁文件过期"变成 CI 可见的失败，而不是静默漂移（uv 的 `--frozen` 只表示"不更新锁文件"，实测不校验时效性，故不用于门禁）。
- **影响**：CI 三个 job 改用 `astral-sh/setup-uv`（钉版本）并缓存 `uv.lock`；文档中
  `uv pip install -e ".[...]"` 的写法统一替换为 `./scripts/setup.sh --extras …`（[README](../../README.md)
  快速开始为唯一权威入口）。`dev` 仍是可选依赖组，不迁移到 `[dependency-groups]`
  （取舍见设计文档 §4）。pip 仍是构建后端的互操作目标，第三方环境仍可用
  `pip install -e ".[otel]"`，但它不再是仓库推荐的安装路径。

### ADR-009：统一文档解析端口与离线结构化分块（已采纳，2026-11-03）

- **决策**：上传与 CLI 共用 `DocumentParser` 端口。文本经 charset-normalizer 检测编码，
  HTML 用 trafilatura 提取正文、BeautifulSoup 兜底；PDF、Office 与扫描图片显式启用
  MinerU HTTP 服务后解析。扩展名必须与文件签名一致，不能把任意二进制当作文本。
- **分块**：markdown-it-py 识别标题层级、表格、列表与代码块；tiktoken 按 token 计量，
  默认随包携带 `cl100k_base` 词表，首次运行也无需网络。允许配置本地编码器定义。
  breadcrumb 占用预算，表格按整行拆分并重复表头；无法容纳的原子结构明确报错。
- **边界**：MinerU 默认关闭；不安装 OCR 模型或在离线路径下载资源。不改变文档 ID、
  chunk ID、索引及 embedding 逻辑。解析器版本、源 MIME 和标题路径进入元数据。

### ADR-010：知识库级双模图谱与版本化摘要索引（已采纳，2026-11-04）

- **背景**：当前知识写入强制领域 Schema，作用域主要为租户；开放抽取与概括问题
  需要知识库配置、自由关系表达和可追溯的派生摘要。
- **决策**：引入 `KnowledgeScope`、知识库活动 generation、来源版本与持久化变更记录；
  Schema/开放抽取共享解析、任务和图端口，采用各自门禁。开放图物理编码使用固定标签/边，
  自由类型存属性。通过 `CommunityDetector` 接入本地 Leiden，文档/社区摘要经后台任务生成。
  具体 Leiden 包与版本在 E0 验证后锁定，不强依赖 Neo4j GDS 或整个 GraphRAG 框架。
- **理由**：复用现有 GraphStore、任务恢复与引用链，同时支持同租户多库和社区增量更新。
  摘要依赖指纹区分拓扑更新与内容更新；局部修复必要时可扩大范围。
- **影响**：四种 store、缓存、worker、审计均需贯穿知识库与版本；旧数据迁入默认库。
  核心配置元数据不依赖 Web 身份库；离线默认保留，生成式新能力显式要求 LLM。
- **状态**：用户要求执行扩展计划后采纳；保持默认离线路径和现有产品门禁。
  首版以绑定作用域的存储适配器映射底层隔离键，默认库复用旧租户键；
  SQLite 保存配置、不可变快照、变更记录和删除标记，不依赖 Web 身份库。
  任务与迁移见 [扩展计划](../workstreams/graph-platform-extension.md) E0～E4。

### ADR-011：按角色用模与能力适配（已采纳，2026-11-04）

- **背景**：现有 OpenAI 兼容 provider 已支持聊天、embedding 端点拆分，
  但强/轻档无法完整表达七角色、不同能力和知识库绑定。
- **决策**：在现有接口上增加角色/profile 解析，分离 completion、embedding、rerank
  能力；保留 Tier 兼容迁移。优先直连兼容 HTTP，LiteLLM 可作为外部代理，
  嵌入式依赖作为后续适配方案。管理端提供版本化配置与真实小请求能力测试。
- **理由**：沿用现有计量/熔断/缓存边界，避免更换网关时重写领域逻辑；
  协议兼容通过部署版本与模型能力矩阵验证，不由厂商名称推定。
- **影响**：provider、预算与缓存按角色/端点版本隔离；embedding 切换重建向量空间；
  无鉴权本地服务显式配置。密钥仍只由 Settings/密钥服务解析。
  实施时同步当前全局 API key 检查涉及的 Spec、CLAUDE 与运维说明。
- **状态**：用户要求执行扩展计划后采纳；保留 ADR-003 双档 API 的兼容入口。
  详见 [扩展计划](../workstreams/graph-platform-extension.md) E1。

### ADR-012：SDK 与 MCP 复用公共应用接口（已采纳，2026-11-04）

- **背景**：当前 REST/SSE 与 FastAPI 文档已有实现，缺少可独立分发的客户端和 MCP Server；
  原文引用/审计持久化状态还需成为清晰的公共契约。
- **决策**：保持 v1 兼容并补充类型化响应、事件、原文 resolver 和审计回执。
  Python SDK 独立打包；MCP 作为调用 SDK/HTTP 的薄适配器，先交付 stdio 只读工具，
  再扩展 Streamable HTTP。两者复用服务端权限、预算与审计，避免直接访问底层存储。
- **理由**：接入可以在图谱新算法前交付，降低客户端维护成本，保留一种业务执行语义。
- **影响**：增加 API 契约回归和实际客户端验收；不让 SDK 用户依赖完整 GraphRAG 运行环境。
  `audit_required` 明确区分回答成功与审计成功，现有客户端保留可用行为。
- **状态**：用户要求执行扩展计划后采纳；远程 MCP 和事件断点重放不纳入首版。
  详见 [扩展计划](../workstreams/graph-platform-extension.md) I1。

## 3. 待决策清单（评审会）

- [x] ADR-001~004 在 POC 启动时按默认采纳（仍可在评审会改选）
- [x] 向量库具体产品：Qdrant（POC）
- [x] 前端技术栈：Vue 3 零构建（ADR-006，2026-07-21）
- [ ] 部署形态：K8s / 单机 Docker Compose（试点规模决定）
- [ ] 正式试点领域与语料替换当前 interim 公司关系语料（P1-GOV-01 / R5）— 见 [g1-to-g2-transition.md](../phases/g1-to-g2-transition.md) C1 · `data/pilot/`
- [x] ADR-010～012：用户要求实施后采纳；具体契约和验证进度见 [扩展计划](../workstreams/graph-platform-extension.md)。
