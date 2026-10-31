# 工作流：服务接口与问答界面

**覆盖需求**：FR-API-01 ~ 05、FR-AN-03、NFR-06/07 · **相关阶段任务**：P2-ARCH-03、P3-PERF-06、P3-KG-04、P4-UI-*
**负责人**：检索系统工程 / 前端支援（试点阶段）
**版本**：V1.6（2026-10-31）— Web 工作台按 `Prompt.md` v2 重做「推理笔记本」（§2.2 / §2.6 / §2.8 / §2.9）；P5-UI-02 控制台状态修正为**已交付**（§2.7）。前版 V1.5（2026-08-08）：P5-UI-02 前端重规划立项（角色感知控制台，§2.7）；V1.4（2026-07-25）ENT 增补：RBAC 三角色收权、admin 排障端点、上传治理、`FORBIDDEN` 错误码、`tenant_id` 数据级隔离；V1.3（2026-07-21）P5-UI-01 Vue 3 零构建重构；ADR-006。

实现入口：`src/agentic_graphrag/api/`（`app.py` 组装与异常处理、`routes/query.py`、`routes/knowledge.py`、`routes/admin.py`、`auth.py`、`rbac.py`、`envelope.py`、`sse.py`、`errors.py`、`service*.py`）；前端 `web/`。

## 1. API 设计与实现现状

### 1.1 统一响应封装（FR-API-04）[x]

```json
{ "success": true, "data": { }, "error": null, "meta": { "total": 0, "page": 1, "limit": 20 } }
```

错误时 `success=false`，`error` 含 `code`（机器可读枚举）与 `message`（用户友好，不泄露内部细节，NFR-06）。实现：`envelope.py` + `app.py` 三级异常处理器（`ApiError` / 入参校验 / 兜底 500，兜底仅返回异常类型名，不含堆栈）。

错误码全集（`errors.py`）：`INVALID_INPUT`、`BUDGET_EXCEEDED`、`TIMEOUT_PARTIAL`、`RATE_LIMITED`、`UNAUTHORIZED`、`FORBIDDEN`（ENT-04 角色不足）、`INTERNAL_ERROR`、`SERVICE_UNAVAILABLE`。

### 1.2 查询 API（FR-API-01）[x]

`POST /v1/query`（`routes/query.py`）

| 入参 | 类型 | 说明 |
|---|---|---|
| question | string，必填 | 1~2000 字符，schema 校验（NFR-07） |
| max_hops | int，可选 | 上限受服务端硬顶约束 |
| timeout_ms | int，可选 | 同上 |
| force_agentic | bool，可选 | 跳过分诊强制 Agentic（调试用；同时绕过答案缓存） |

出参 `data` = [推理链契约 JSON](./agent-orchestration.md#3-推理链契约fr-an-02p2-ag-06-定稿)（含 answer、claims+引用、status、cost、`metadata.confidence`）。服务默认**离线**装配（内存图 + seed 三元组 + MockLLM，`service.build_default_service`）；`AGR_ALLOW_LLM=1` 且配置有效 `LLM_API_KEY` 时启用真实 LLM。

### 1.3 流式响应（FR-API-02 / P3-PERF-06）[x]

`POST /v1/query/stream`（SSE，`sse.py` 逐帧编码）。事件序列（`service_stream.py` + `agent/loop_stream.py`）：

| 事件 | 载荷 | 触发 |
|---|---|---|
| `cache_hit` | `{query_id}` | 命中答案缓存（随后直接发 `answer`；`force_agentic` 时绕过） |
| `triage` | 分诊决策 JSON | 非缓存查询开头；`force_agentic` 时发合成帧 `{route:agentic, rule_hit:force_agentic}` |
| `thinking` | `{stage, text, detail?}` | Planner 拆解完成（`stage=plan`）或 Executor 检索工具（`stage=retrieve`）；Fast Path 亦发 plan 帧 |
| `sub_question` | `{hop, sub_question}` | Executor 节点完成时（LangGraph `stream(updates)` 实时） |
| `hop_done` | `{hop, conclusion, critic_action}` | Critic 节点完成时 |
| `answer` | 完整推理链 JSON | 结束（finalize + audit/cache/metrics 之后） |
| `error` | `{code, message}` | 异常终止（`ApiError` 保留错误码；其余仅异常类型名） |

> **实现说明**：设计文档写的是 `astream_events`；同步 FastAPI 生成器用 LangGraph `graph.stream(..., stream_mode="updates")` 做节点级映射，语义等价（每个节点完成后立即 yield），见 `agent/loop_stream.py`。

### 1.4 知识管理 API（FR-API-03）[x]

均在 `routes/knowledge.py`，前缀 `/v1`：

| 端点 | 用途 |
|---|---|
| `POST /docs` | 批量上传（含来源元数据） |
| `GET /ingest-tasks/{task_id}` | 抽取任务状态 |
| `GET /review-queue` · `POST /review-queue/{item_id}/decision` | 审核队列（P3-KG-03） |
| `GET /audit/queries/{query_id}` | 推理链审计回查（FR-AN-04 / P3-AN-01） |
| `POST /feedback` | 反馈回路（FR-OP-03）：负反馈入 ReviewQueue 并写回 audit metadata |
| `GET /metrics`（admin） | 监控指标（P4-REL-03；ENT-04 收权 admin-only） |
| `GET /graph/entities` | 图实体浏览脚手架（P5-CAP） |

**ENT 增补（2026-07-25）**：`routes/admin.py`（前缀 `/v1`，均 admin）— `GET /traces/{query_id}`（trace 回查）、`GET /budget/snapshot`（三级预算快照）、`GET /audit-events`（安全事件，支持 since/until/tenant_id/action 过滤）；app 级 `GET /metrics-prom`（Prometheus 文本，免鉴权，ENT-08）。上传治理（ENT-06）：`POST /docs` 收权 operator+，单文件 ≤5MB、单批 ≤20、白名单 md/txt/pdf，超限 413；任务落盘 `IngestTaskStore`（worker 独立运行，见 runbook §6）。

### 1.5 横切要求 [x]（部分）

- 鉴权 + 速率限制（P4-UI-02，`auth.py` 中间件）：`AGR_REQUIRE_AUTH=1` 强制 API Key；`AGR_API_KEYS=tenant:key:role,...`（ENT-04 三角色 admin/operator/reader，缺省 reader，兼容旧两段式）+ `require_role()` 路由守卫 + key 过期；租户级 `AGR_RATE_LIMIT_QPS`（默认 20）与并发 `AGR_RATE_LIMIT_CONCURRENT`（默认 10），per-tenant 覆盖走 `configs/default.yaml` `tenants:` 段（ENT-05）。
- 全部入参 schema 校验，fail fast（NFR-07）[x]。
- 请求生成/携带 `query_id`，贯穿推理链与审计存储（NFR-08）[x]；`request_id` 入链 metadata 与 JSON 日志 contextvars（ENT-01/02）[x]。
- 租户**数据级**隔离：代码侧已由 ENT-06 承接（`tenant_id` 贯穿 stores / 三路检索 / agent，跨租户零命中单测；租户作用域绕过检索缓存）[x]；运维侧物理分库与真实 Neo4j/Qdrant 回归仍在 P4-REL-01。

## 2. Web 界面（FR-API-05 / P4-UI-01 · P5-UI-01 已交付；P5-UI-02 已交付 · v2 重设计）

**定位**：内部试用工具，功能优先于视觉；Claude 风格浅色对话壳，Vue 3 零构建单页应用（ADR-006）。

> **重规划（2026-08-08 立项，2026-09-07 交付）**：定位从单一问答页升级为「问答 + 知识运维 + 审核 + 可观测（+ 图谱浏览）」的角色感知控制台——壳 + hash 路由 + 五视图与 M0 前置（`GET /v1/me`、`GET /v1/ingest-tasks`）均已交付；2026-10-31 v2 起管理视图位于 `js/admin/*`（结构见 §2.2，视觉见 [docs/design/UI_DESIGN.md](../../docs/design/UI_DESIGN.md) §10）。§2.1–§2.4 描述**已交付**现状；§2.7 保留立项概要；立项时的任务分解与验证清单见 [phases/p5-ui-02-console-replan.md](../phases/p5-ui-02-console-replan.md)。

### 2.1 技术形态（零构建 + 钉版 Vue 3）

| 项 | 现状 |
|---|---|
| 框架 | Vue 3.5.13（Options API；in-DOM 根模板 + 组件 string template）；[ADR-006](../engineering/tech-stack.md) |
| 加载 | vendored-first → 钉版 jsDelivr → 钉版 unpkg；**无** npm / 打包器（见 [docs/ops/EXTERNAL_RUNTIMES.md](../../docs/ops/EXTERNAL_RUNTIMES.md) + `web/static/vendor/README.md`） |
| 模块 | `js/app.js`（壳 + hash 路由 + 全局快捷键）· `js/theme.js`（双主题）· `js/shell/{use_shell,use_palette,use_inspector,tpl_topbar,tpl_notebook,tpl_inspector}.js` · `js/api.js` / `js/stream.js` / `js/responses.js` / `js/contracts.js` · `js/i18n.js`（全部文案）· `js/views/chat.js`（问题页）· `js/chat/*`（HopRail 模型、分页、向导 / 澄清 / 进度 / 子图 / 追问纯函数、composer composable、模板片段）· `js/admin/{graph,knowledge,review,ops}.js`（管理视图）· `js/store/*`（localStorage 会话、偏好、壳层开关、图谱聚焦）· `js/knowledge/ingest_wizard.js` |
| 样式 | `tokens.css`（双主题令牌）· `base.css` · `shell.css` · `notebook.css` · `inspector.css` · `wizard.css` · `admin.css` · `responsive.css`；各 ≤300 行；视觉规范见 [docs/design/UI_DESIGN.md](../../docs/design/UI_DESIGN.md) §10 |
| 挂载 | `agr-api` 静态挂载：`GET /web` → `index.html`，资源 `/web/static/*` |
| SSE 消费 | `js/api.js`：`fetch` + `ReadableStream` 手工解析（**非** `EventSource`，因需 POST + JSON body） |
| 结构冒烟测试 | `tests/unit/test_web_claude_ui.py`（文件全集、钉版、注入安全、静态资源 200）· `tests/unit/test_web_wizard_ui.py`（向导 / 澄清 / 进度 / 子图纯模块的 Node 运行时断言、FeatureFlag 默认关闭、文案集中） |

### 2.2 页面结构与功能（实现，2026-10-31「推理笔记本」v2）

1. **推理笔记本布局**：48px 顶栏（品牌与知识库范围、⌘K 命令面板、模式、健康圆点、主题、设置）· 左栏「笔记本」（新问题、按天分组的历史、按角色显示的管理组）· 中栏「问题页」（最大 760px）· 右栏「检查器」（证据 / 子图 / 日志，320–520 可拖拽，E 收起）；每个问题一页：标题 → HopRail → 结论与引用 → 元信息 → 追问。API Key 在设置弹窗。断点与令牌见 UI_DESIGN §10。
2. **欢迎页**：示例问题按「2 跳 / 3 跳 / 对比 / 交集」分组为卡片（取自 `evals/datasets/poc_cases.jsonl`），点击只填入输入框，不直接发送。
3. **输入区**：工具栏 = 「引导提问」向导 · 模式切换（快速 / 多跳 / 先确认计划）· 跳数预算；输入 `@` 触发实体补全（`GET /v1/graph/entities?query=`）；Enter 发送 / Shift+Enter 换行。
4. **提问向导**（4 步，Enter 下一步 / Esc 跳过，`role="dialog"`）：起始实体（自动补全）→ 关系链（目录镜像自 `configs/schema/domain_v0.yaml`）→ 约束（时间 / 数量 / 交集）→ 预览（子问题草案可删除、重排；生成的英文问题可直接修改）。
5. **澄清卡片**：发送前对问题中的实体名查实体索引，同名多实体 → 单选卡；过短且无实体的问句 → 宽泛卡（用向导补全 / 直接发送 / 跳过）；答案 `status=partial|no_answer` 且 `missing_info` 非空 → 多选补充卡。
6. **推理路径 HopRail**：由 SSE 事件实时填充——骨架节点 → 「规划子问题…」虚线节点与计划行 → 第 N 跳节点依次点亮（跳色外环脉冲，客户端计时）→ 作答 ◆；完成后用最终链补上每跳证据数（来自 `steps[].evidence_ids`），路径保持展开；执行中可停止（Esc）。点击节点在检查器中按该跳筛选证据；悬停显示完整子问题与结论；⋯ 菜单「从此跳重跑」按 FeatureFlag 降级。
7. **结论区**：正文 + 句末引用角标 `[n]`（`claims[].evidence_ids` → 证据目录序号，下划线取所属跳色；悬停预览原文前 120 字；点击在检查器定位并高亮 1.2 s）· `metadata.confidence` 置信度标签（仅已知等级）· 非 `answered` 时列出 `missing_info`，`no_answer` 时显示「未能得出结论」与后端原因并提供「去补充知识」· 2–3 个追问 chips · 元信息行（跳数 · 耗时 · 调用 · tok）· 重新提问 / 复制（结论、query_id）/ 👍 👎（👎 选原因后提交 `reason`）。
8. **知识导入向导**（knowledge 视图）：选择文档（前端预检 5 MiB / 20 个 / md·txt）→ 预览（文本片段；抽取预览接口缺失时显式说明）→ 确认入库（`POST /v1/docs`）→ 查看进度（轮询 `GET /v1/ingest-tasks/{id}`，≥2s，页面隐藏即停；`review` 状态可跳转审核）。
9. **每次请求独立**：会话历史仅是展示与回看，不向后端携带上下文。

### 2.3 推理链可视化交付（P4-UI 增强 + P5 增强）[x]

| 能力 | 实现 |
|---|---|
| 论断内联引用**角标** | `buildAnswerSegments` + `cite-btn`；点击高亮对应论断（`claim-active`）并滚动 |
| 子问题分解**树** | `plan-tree` 组件：`buildPlanNodes`，状态色（sufficient/partial/fail），展示 depends_on |
| 图路径**可视化** | `path-list`：`buildPathRows` → node/edge chips；`MAX_PATH_ROWS=40` 后显式溢出提示 |
| 复制推理链 | 折叠区「复制」→ `navigator.clipboard`（localhost secure context） |

仍明确不做：图路径**编辑器**、多轮上下文、移动端适配（见 §2.5）。

### 2.4 前端必须遵守的规则

见 [engineering/rules.md](../engineering/rules.md) §8。核心：零构建 + 仅钉版 Vue 3（ADR-006）；动态文本 mustache/`textContent`，禁 `v-html`/`.innerHTML`；只调 `/v1/*` 且遵守 envelope；改动后保持 `test_web_claude_ui.py` 同步。

### 2.5 界面明确不做（V1）

- 多轮对话上下文（每次提问独立）
- 图谱编辑能力（治理走独立审核界面 P3-KG-03）
- 移动端适配 — **⚠ 差异（2026-10-31）**：应产品侧要求，本次改版已落地响应式布局（<1024px 工作区抽屉、<768px 侧栏抽屉 / 单列欢迎卡），PRD 与 rules §8 尚未同步修订，挂账 IMPORTANT §6
- 图路径可视化**编辑器**

> 2026-08-08 注：知识运维 / 审核 / 可观测界面**不属于**上列「不做」——P5-UI-02（§2.7）已交付。上列四项中「移动端适配」已按产品要求以响应式落地（⚠ 差异见上条），其余维持不做（rules.md §8；改动须先改 PRD）。

### 2.6 验证清单

工程冒烟（CI）：`tests/unit/test_web_claude_ui.py`、`tests/unit/test_web_wizard_ui.py`。浏览器与离线 vendor 等人工项见执行计划 [phases/p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md) §7；v2 真实浏览器走查与设计门禁见本节最后一项。

- [x] `GET /web` 与 `/web/static/*` 挂载存在；文件全集与钉版断言
- [x] 请求体字段与 `QueryRequest` schema 一致（question/force_agentic/max_hops）
- [x] SSE 分支覆盖 §1.3 全部事件类型（含 `cache_hit`）— 未知事件静默忽略
- [x] 反馈按 turn 携带 `query_id` 且处理 `success=false`
- [x] 全前端 `v-html` / `.innerHTML` 零命中
- [x] 全部 Vue string template 在 Node 下用钉版 Vue 编译器编译通过（`node scripts/check_web_templates.mjs`，由 `test_web_wizard_ui.py` 调用）；`T.*` 文案键全部存在于 `i18n.js`
- [x] 真实浏览器走查（2026-10-31 v2）：`.taste-skill/drive.mjs` 在 headless Chromium 中走完欢迎 / 流式 / 回答 / 引用定位 / 跳筛选 / 子图 / 日志 / 未找到答案 / 澄清 / 向导 / 命令面板 / 设置 / 各断点 / 管理视图并留存截图；`design-taste-frontend` 的 `inspect.mjs --app` 对实时首屏与状态快照 0 FAIL（见 UI_DESIGN §10）

### 2.7 P5-UI-02 前端重规划概要（2026-08-08 立项；2026-09-07 交付，管理视图现于 `js/admin/*`）

试用问答 → 角色感知控制台，hash 视图五个：`#/chat`（现状行为冻结）、`#/knowledge`（operator+：上传 + 抽取任务列表/轮询）、`#/review`（operator+：审核队列 + 决策，文案不暗示图谱写回——BL-01/03 挂账）、`#/ops`（admin：指标 / 预算 / 安全事件 / 按 query_id 审计回查复用 chain-view）、`#/graph`（reader：实体分页表，低优先）。

- **技术形态不变**：ADR-006 零构建、Vue 3.5.13 钉版、无新运行时依赖（视图切换自研 hash，不引入 vue-router）——**无需新 ADR**。
- **API 前置两项**（M0，已交付）：`GET /v1/me` 身份回显（导航按角色显隐，避免 403 试探污染 ENT-03 审计流）；`GET /v1/ingest-tasks` 列表分页。
- **视觉基准（2026-08-08）**：「制图室」方向——宋体展示层 + 图纸纸面网格 + 墨青/朱砂令牌 + 印章状态语言；唯一权威 [docs/design/UI_DESIGN.md](../../docs/design/UI_DESIGN.md)。**2026-10-31 更新**：v2 已按 `Prompt.md` 重做为「推理笔记本」（§10）；「制图室」方向不再执行，保留备案（见 IMPORTANT §6）。
- 权威计划（模块行数预算 / 里程碑 U-01…U-15 / 验证清单）：[phases/p5-ui-02-console-replan.md](../phases/p5-ui-02-console-replan.md)。

### 2.8 前端已声明、后端尚未实现的接口契约（2026-10-31）

开关集中在 `web/static/js/contracts.js` 的 `FeatureFlag`（全部默认 `false`），路径常量在 `ProposedEndpoint`。关闭时前端按下表降级；`tests/unit/test_web_wizard_ui.py` 断言业务代码从不引用 `ProposedEndpoint` 或这些路径。端点落地后只需实现契约、翻转开关。

| 能力 | 开关 | 契约（均走统一 envelope） | 关闭时的前端降级 |
|---|---|---|---|
| 计划预览 / 确认 | `PLAN_PREVIEW` | `POST /v1/query/plan` body `{question, max_hops?}` → `{plan_id, sub_questions:[{id, text, depends_on[]}], estimated_hops}`；执行时 `POST /v1/query/stream` 接受可选 `plan_id` 与编辑后的 `sub_questions` | 「先确认计划」模式按多跳（`force_agentic`）执行并在对话中提示；向导第 4 步提供客户端子问题草案的删除 / 重排 |
| 从某一跳重跑 | `RERUN_FROM_HOP` | `POST /v1/query/{query_id}/rerun` body `{from_hop, sub_questions?}` → 与 `/v1/query/stream` 相同的 SSE 事件序列（复用 checkpointer 状态） | 按钮把该跳子问题填回输入框并提示需重新提问 |
| 抽取预览 | `EXTRACTION_PREVIEW` | `POST /v1/docs/preview`（multipart `files`，不落库）→ `{entities:[{name, type, confidence}], relations:[{head, relation, tail, confidence}], rejected:[{reason, count}]}` | 向导第 2 步只显示文档文本片段，并说明 worker 异步抽取、结果进入审核队列 |
| 邻域子图 | `GRAPH_NEIGHBORS` | `GET /v1/graph/entities/{id}/neighbors?hops=1&limit=` → `{nodes:[{id, name, type}], edges:[{head_id, relation, tail_id, evidence_ids[]}]}` | 子图标签页只解析答案证据目录中的图谱行（id 前缀 `nbr:` / `path:`） |
| 关系目录 | `RELATION_CATALOG` | `GET /v1/graph/relations` → `[{type, head_types[], tail_types[], description}]` | 向导使用 `js/chat/wizard.js` 内镜像自 `configs/schema/domain_v0.yaml` 的静态目录 |

### 2.9 图标与动效（2026-10-31 增补）

- **图标集**：按 `Prompt.md` §3.3 使用 Lucide 线性图标（16px、1.5 描边、`currentColor`）。`web/` 零构建，不引入 npm 依赖：`lucide-static@1.65.0` 的 `icon-nodes.json` 经 `scripts/build_lucide_icons.mjs` 生成为 `web/static/vendor/lucide-icons.js`（`ICON_IDS` 名称表 + `LUCIDE` 几何，ISC，与 Vue 同样 vendored + 固定版本，ADR-006）。`web/static/js/icons.js` 只做名称到节点的解析与 `<ag-icon>` 渲染；品牌标记 `mark` 为唯一自绘字形。`tests/unit/test_web_claude_ui.py` 断言模板/模块引用的每个图标名都在 vendored 表内，并断言生成文件与上游版本一致（`--check`）。
- **动效**：词表与参考 `demo/` 一致 —— `enter-note`（页面/浮层上浮 8px，220ms）、`enter-node`（跳节点右移 8px，200ms，按 `--t-stagger` 55ms 级联）、`panel-in`（检查器自右边缘进入；1200–1439 抽屉档关闭，避免与其 transform 冲突）、`breathe` / `spin` / `fade-in`；`prefers-reduced-motion` 下取消位移与延迟，只保留透明度。均 ≤220ms，符合 §6 可达性约定。
