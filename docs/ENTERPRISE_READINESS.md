# 企业级管控与全链路排障 — 现状审计与建设规划（ENT）

**日期：** 2026-07-24 · 分支 `feat/all-phases-complete`
**方法：** 静态代码审计（未运行项目、未安装依赖；需运行验证的事项统一记入 §7 验证清单）
**范围：** 对照以下企业级能力主张逐项核查，并对缺口给出建设规划：

> 兜底全链路、清晰排障路径；企业级管控：权限管理、并发调度、数据安全、审计日志、日志集成、与后续 RPA 流程集成。

**结论先行：当前系统 *不具备* 上述端到端企业级能力。** 已有试点级脚手架（鉴权/限流/三级预算/推理链审计/指标/运维手册），但：**应用日志为零**（全 `src/` 无任何 `logging` 使用）、可观测状态全部进程内易失、无 RBAC、无租户数据隔离、无安全事件审计、无日志/监控外送、无任何面向 RPA 的异步作业与回调机制。以下 §1–§3 为审计证据，§4–§6 为规划。

---

## 1. 结论速览

| # | 能力 | 现状 | 一句话差距 | 既有关联任务 |
|---|------|------|------------|--------------|
| A | 全链路排障（兜底） | 🟡 部分 | 有 request_id/错误码/healthz/runbook/badcase 工具，但**无应用日志**，trace/metrics 进程内易失且无查询 API | P3-OP-01/03、P4-REL-04 |
| B | 权限管理 | 🟡 部分 | API Key→租户已有；**无角色/RBAC**，运营端点（审核、指标、上传）任意租户可用；默认不开鉴权 | P4-UI-02 |
| C | 并发调度 | 🟡 部分 | 租户级 QPS+并发上限、三级预算、单查询护栏齐备；**全部内存态单进程**，无排队/优先级/横向扩展，摄取任务无 worker | P3-OP-02、P5-EXT-03 |
| D | 数据安全 | 🟡 部分 | 密钥走 env、Cypher 参数化、入参校验、错误不泄栈；**无租户数据隔离**、无上传限制、无脱敏/保留策略 | P4-REL-01（开）、P4-REL-02 |
| E | 审计日志 | 🟡 部分 | 推理链落盘+租户隔离回查+反馈关联；**无安全/管理事件审计**，无导出/轮转/防篡改 | P3-AN-01、P4-AC-02（开） |
| F | 日志集成 | ❌ 缺失 | 设计有（cicd-observability §3.4 结构化 JSON 日志），**实现为零**；无 Prometheus/OTLP 外送 | 仅设计文档 |
| G | RPA 流程集成 | ❌ 基本缺失 | 有 REST+SSE+OpenAPI+推理链 JSON Schema 等地基；**无 webhook/异步作业/幂等键/批量接口**，摄取任务永远停在 queued | 无 |

---

## 2. 现状盘点（证据）

### A. 全链路排障

已有：

- 每请求 `request_id`（`X-Request-Id` 或 uuid4），随 envelope meta 返回，异常时注入 error details — `src/agentic_graphrag/api/routes/query.py:37`、`api/app.py:56`
- 机器可读错误码集合（INVALID_INPUT/BUDGET_EXCEEDED/RATE_LIMITED/…）— `api/errors.py:26`
- SSE `error` 事件（code + 异常类型名，不泄内部细节）— `api/service_stream.py:77`
- `/healthz` 分依赖浅检（graph/vector/ping + 前后端 backend 标识 + allow_llm）— `api/app.py:91`
- 护栏触发后兜底摘要（explored paths）— `agent/guardrails.py:167`；GraphRecursionError 恢复 — `agent/loop_recover.py`
- LLM 连续失败熔断（半开恢复）— `llm/circuit.py`；Neo4j 不可达回退内存图 — `stores/factory.py`
- 抽取管线 journal/retry/quarantine — `config.py:100`（KnowledgeConfig）
- 排障手册（常见故障 5 类 + 告警建议）— `docs/ops-runbook.md`
- badcase 归因 / spotcheck CLI — `eval/badcase.py`、`cli/spotcheck.py`
- 查询级 trace（span + query_id 关联）与指标（P50/95/99、错误码计数、budget_trips）— `observability/trace.py`、`observability/metrics.py`

缺口：

1. **全仓库零应用日志**：`src/`、`scripts/` 无任何 `import logging`/`getLogger`/structlog。未处理 500 仅靠 uvicorn 控制台栈回溯，无 request_id/租户字段，无落盘；SSE 错误对客户端只给类型名，服务端**无处**留存根因。
2. trace/metrics 均为进程内环形缓存（`trace.py:92`、`metrics.py:108`），重启即失；trace **无任何 API/CLI 可查**（路由仅 query+knowledge，无 `/v1/traces`）。
3. API `request_id` 与推理链 `query_id` 的映射仅存在于单次响应 envelope，不入审计链（`api/service_query.py:197` 只写 tenant/user/query_id）— 事后凭网关日志无法回查链路。
4. 摄取任务注册表为进程内 dict 且**无 worker 消费**，状态永远 `queued`（`api/routes/knowledge.py:21,77`）— 排障死胡同。
5. `/healthz` 不反映 LLM 熔断器状态与审核队列积压，降级不可见。

### B. 权限管理

已有：

- API Key→租户映射、Bearer/X-Api-Key 双头、`AGR_REQUIRE_AUTH` 强制开关、租户级限流一体中间件 — `api/auth.py:115`
- 用户预算身份默认绑定 key 摘要，客户端不可伪造 `X-User-Id`（`AGR_TRUST_X_USER_ID` 显式信任）— `api/auth.py:63,169`
- 试用 UI API Key 输入（localStorage + Bearer）— `web/static/js/api.js:5`

缺口：

1. **无角色/权限模型**：任意合法租户 key 可调用运营端点 — 审核决议 `POST /v1/review-queue/{id}/decision`、全局指标 `GET /v1/metrics`、文档上传 `POST /v1/docs`、图谱浏览（`api/routes/knowledge.py:139,195,49,202`）。审核队列列表**跨租户可见**。
2. **默认开放**：`AGR_REQUIRE_AUTH` 未设置时所有端点匿名可用（`auth.py:165` 直接放行为 default 租户）— 非 secure-by-default。
3. Key 无生命周期：明文列在 env、无过期/轮转/吊销、无哈希存储；无 SSO/OIDC 对接点。
4. `/web`、`/docs`、`/openapi.json` 永久公开（`auth.py:133`）— 试点可接受，生产需可配。

### C. 并发调度

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

### D. 数据安全

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

### E. 审计日志

已有：

- 推理链 100% 落盘（JSONL + 内存索引），按 query_id 回查、租户隔离 — `generation/audit_store.py:36,56`、`GET /v1/audit/queries/{id}`
- 反馈写回链 metadata 并入审核队列（负反馈）— `api/service.py:168`
- 查询指标含 tenant/user/error_code；预算触发记录 trips — `observability/metrics.py:12`、`budget_policy.py:196`

缺口：

1. **无安全/管理事件审计**：鉴权失败、限流/熔断触发（对象与时间）、审核决议（谁批了什么）、文档上传、配置变更 — 均无记录（也无日志可兜底）。
2. 无导出/对接：不能按时间段/租户批量导出；无 SIEM 友好格式。
3. JSONL 只增不轮转；更新（反馈附加）以追加重复行实现，重启后靠 last-wins（`audit_store.py:44`）— 无防篡改（哈希链/签名）设计。
4. `GET /v1/metrics` 返回**全局**汇总（含各错误码计数），未按租户过滤；`MultiLevelBudget.snapshot()` 有实现但无端点暴露。

### F. 日志集成

- 设计已写：结构化 JSON 日志、全量携带 query_id/tenant_id、告警阈值表、OTel 全链路 span 规范 — `plan/engineering/cicd-observability.md` §3。
- **实现为零**：无日志器、无 JSON formatter、无文件输出；`/v1/metrics` 是自定义 JSON 而非 Prometheus 文本协议；无 OTel SDK 接线。ELK/Grafana/SIEM 均无可接入面。⚠ 差异（设计有、实现无）。

### G. RPA 流程集成

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

## 3. 需求主张逐条评定

| 主张 | 评定 | 依据 |
|------|------|------|
| "兜底全链路无缝、清晰排障路径" | **不成立** | 零应用日志（§2-A-1）；trace 无法回查（§2-A-2）；request_id 不入审计（§2-A-3）；摄取任务排障死胡同（§2-A-4）。有兜底行为（熔断/回退/护栏），但"链路"在服务端不可追溯 |
| "权限管理" | **部分成立** | 租户级 API Key 有；无 RBAC、运营端点未收权、默认开放（§2-B） |
| "并发调度" | **部分成立** | 限流/预算/护栏齐备但单进程内存态，无排队与横向扩展（§2-C） |
| "数据安全" | **部分成立** | 注入/泄露/密钥面达标；租户数据隔离与上传治理缺失（§2-D） |
| "审计日志" | **部分成立** | 推理链审计达标（AC-3 代码侧）；安全事件审计为零（§2-E） |
| "日志集成" | **不成立** | 无日志可集成（§2-F） |
| "与后续 RPA 流程集成" | **不成立** | 仅同步 API；无作业/回调/幂等（§2-G） |

---

## 4. 建设规划（任务分解）

新任务列入 **P5-ENT** 系列（规模化阶段企业级轨道）；标注 ⏫ 的为 **G4（试点出口）前置**——它们直接支撑 G4 门禁"监控告警、预算熔断、审计回查在生产环境验证"。既有开放任务（P4-REL-01、P4-AC-02）不重编号，在此挂接。

### P5-ENT-01 ⏫ 结构化日志基座（其余任务的地基）

| 项 | 内容 |
|----|------|
| 交付 | 新模块 `observability/logging_setup.py`：stdlib logging + JSON formatter（无新依赖）；`contextvars` 承载 request_id/query_id/tenant_id/user_id，中间件注入、跨 agent 循环传播；uvicorn access log 并入同格式；`AGR_LOG_LEVEL`/`AGR_LOG_FILE`(+轮转) 环境开关 |
| 落点 | 中间件挂 `api/app.py`；异常处理器（`app.py:75`）、SSE 兜底分支（`service_stream.py:84`）、预算/护栏触发、store 回退路径补 error/warning 日志 |
| 验收 | 任一 4xx/5xx/SSE error 在服务端恰有一条含 request_id+tenant_id+错误码（5xx 含栈）的 JSON 日志；离线 20 case 跑完日志可按 query_id 串起 triage→hops→answer |
| 约束 | 模块 ≤300 行；日志内容经脱敏钩子（预留，ENT-06 实现）；MockLLM 路径零成本 |

### P5-ENT-02 排障闭环补全

- `request_id` 写入链 metadata（`service_query.py:_finalize_chain` 一行）+ SSE 首事件返回 request_id/query_id。
- 新端点（admin 角色，依赖 ENT-04）：`GET /v1/traces/{query_id}`（暴露既有 Tracer）、`GET /v1/budget/snapshot`（暴露既有 `MultiLevelBudget.snapshot()`）。
- `/healthz` 增加 LLM 熔断器状态、审核队列积压数、checkpointer 后端标识。
- 摄取任务状态机：`queued→extracting→review→done/failed`，任务落盘（复用 JSONL 惯例）替代进程内 `_TASKS`。
- `docs/ops-runbook.md` 增补：错误码→根因→处置对照表（错误分类学），日志字段字典。
- 验收：任一失败查询可凭 request_id 走通"网关→日志→审计链→trace"四点回查。

### P5-ENT-03 ⏫ 安全/管理事件审计流

- 新模块 `observability/audit_events.py`：append-only JSONL（`data/processed/audit_events.jsonl`），事件含 ts/tenant/user/action/target/outcome。
- 采集点：鉴权失败（`auth.py:_authenticate`）、限流与预算熔断（携对象租户）、审核决议（reviewer+decision）、文档上传、反馈提交。
- 轮转与保留：按大小/日期滚动，保留期入 `configs/default.yaml`（评审定，cicd-observability §3.4 已留待办）。
- 导出：`agr-audit-export --since --tenant --format jsonl|csv`（CLI，入 `cli/`）。
- 防篡改（后置项）：行级 prev-hash 链；规模化立项后再评估签名。
- 验收：runbook 新增"安全事件回查"手册段；每类事件在单测中可断言落盘字段完整。

### P5-ENT-04 RBAC 与密钥治理

- Key 格式扩展：`AGR_API_KEYS=tenant:key:role`（role ∈ admin/operator/reader，缺省 reader，向后兼容两段式）；或 `configs/api_keys.yaml`（支持过期时间、备注；env 优先）。
- 路由级依赖检查：admin → 审核决议、budget snapshot、traces、docs 上传、metrics；reader → query/stream/feedback/audit 自租户。
- `GET /v1/metrics` 按租户过滤（admin 可看全局）。
- 安全默认：发布配置模板将 `AGR_REQUIRE_AUTH=1` 设为生产缺省；`/docs`/`/openapi.json` 公开性可配。
- 明确不做（本期）：SSO/OIDC、细粒度资源 ACL、key 服务端哈希存储 — 记 §8。

### P5-ENT-05 并发调度升级

- 限额配置化：`configs/default.yaml` 新增 `tenants:` 段（per-tenant QPS/并发/预算三级限额），`MultiLevelBudget`/`RateLimiter` 构造时读入 — 消除代码默认值硬编码（§2-C-2）。
- 排队与优先级（试点后评估）：超限请求可选进入有界等待队列（fail-fast 仍为默认）；租户 SLA 分级（gold/std）影响出队顺序。
- 摄取 worker：进程内后台线程消费任务存储 → 调 `knowledge/extract_pipeline`，并发=1 起步；与 ENT-02 状态机同一变更集。
- 横向扩展准备：抽象 `LimiterStore`/`BudgetStore`/`AuditSink` 协议（对齐 `stores/interfaces.py` 惯例），默认内存实现不变，Redis 实现按 live-adapter 惯例懒加载 + coverage omit（规模化立项后交付）。
- Checkpointer 落盘：把 IMPORTANT §2 挂账的 SQLite checkpointer 转正（`langgraph-checkpoint-sqlite` 可选依赖），为 ENT-07 异步作业恢复兜底。

### P5-ENT-06 ⏫ 数据安全强化（含 P4-REL-01 代码侧）

- 租户数据隔离：`DocumentRecord`/向量 payload/图实体 source 增加 `tenant_id`；检索三路按 principal 过滤；离线单租户路径行为不变（default 租户）。Neo4j 物理分库仍归运维（P4-REL-01 运维侧不变）。
- 上传治理：应用层大小上限（如单文件 ≤5MB、单批 ≤20 个，常量入 config）、类型白名单（md/txt/pdf-text）、超限 413 错误码。
- 脱敏钩子：`observability/redaction.py` 正则管道（手机号/邮箱/身份证可配），挂日志 formatter 与审计链落盘前；默认关闭、试点域按需启用。
- 保留策略：audit_chains/audit_events/review_queue 统一保留期与清理脚本 `scripts/prune_data_files.py`。
- 启动校验：`AGR_USE_LIVE_STORES=1`/`AGR_ALLOW_LLM=1` 时在 lifespan 中断言必需凭据存在，缺失 fail-fast（对齐全局安全规则"启动期校验密钥"）。

### P5-ENT-07 RPA 集成层

- 异步作业 API：`POST /v1/query/jobs`（即刻返回 job_id）→ `GET /v1/query/jobs/{id}`（pending/running/succeeded/failed + 结果=推理链）；作业记录落盘；执行复用现有 run_query 线程；依赖 ENT-05 checkpointer 落盘做恢复。
- Webhook 回调：作业级 `callback_url`（HTTPS）+ `X-AGR-Signature`（HMAC-SHA256，密钥按租户配置）；重试 3 次指数退避；投递结果入 ENT-03 审计事件。SSRF 防护：目标域白名单配置。
- 幂等：变更类端点接受 `Idempotency-Key` 头，键+租户去重窗口 24h。
- 批量：`POST /v1/query/batch`（≤20 条，逐条独立预算）— RPA 批处理典型形态。
- 导出：`GET /v1/audit/queries?since=&until=&tenant=`（分页）供下游流程拉取推理链。
- 契约固化：SSE 事件 schema 文档 + webhook payload schema 入 `configs/schema/`；`/v1` 兼容政策写入 `plan/workstreams/api-and-ui.md`。

### P5-ENT-08 监控外送（Prometheus/OTel）

- `GET /metrics-prom`：Prometheus 文本协议暴露现有 MetricsRegistry（计数器/直方图映射），无第三方依赖可先手写 exposition；告警规则示例入 runbook（阈值沿用 cicd-observability §3.3）。
- OTel（可选依赖，懒加载）：`observability/trace.py` 的 span 桥接 OTLP exporter；采样率可配。
- 与 ENT-01 同构：全部字段携带 tenant_id/query_id。

### 依赖关系

```
ENT-01 日志 ──► ENT-02 排障闭环 ──► ENT-08 外送
   │                │
   └──► ENT-03 审计事件 ──► ENT-07 webhook 投递审计
ENT-04 RBAC ──► ENT-02 admin 端点 / ENT-07 管理面
ENT-05 调度(worker+checkpointer) ──► ENT-07 异步作业
ENT-06 与 ENT-01 并行（脱敏钩子挂日志） 
```

---

## 5. 排期与优先级建议

| 批次 | 任务 | 门禁挂钩 | 粗估工作量 |
|------|------|----------|-----------|
| 第 1 批（G4 前必做）⏫ | ENT-01、ENT-03、ENT-06（隔离+上传+启动校验） | G4"监控告警/审计回查生产验证"、P4-REL-01、P4-AC-02、NFR-06 | 各 2–4 人日 |
| 第 2 批（试点期） | ENT-02、ENT-04、ENT-08（Prometheus 先行） | P4-REL-03 告警落地、AC-6 生产告警 | 各 2–3 人日 |
| 第 3 批（规模化立项后） | ENT-05（队列/Redis）、ENT-07、ENT-08（OTel） | P5 立项、RPA 对接排期 | 各 3–5 人日 |

> 工作量为静态估算（未含联调）；ENT-07 需产品先确认 RPA 对接方与回调安全要求（新增 PRD 开放问题，见 §6）。

---

## 6. 与现有计划/账本的衔接

- **P4-REL-01**（租户数据隔离）：代码侧由 ENT-06 承接；运维侧（分库/网络隔离）不变。
- **P4-AC-02**（生产审计抽样）：前置依赖 ENT-01/03（无日志与事件流则抽样无从谈起）。
- **P5-EXT-03**（多租户配额脚手架 [x]）：ENT-05 是其"配置化 + 分布式"续章。
- **P5-GOV-04**（成本优化）：ENT-08 指标外送是其数据来源。
- **cicd-observability.md §3**：ENT-01/02/08 即该设计的实现任务；落地后在该文档标注实现状态，消除 ⚠ 差异。
- **PRD**：RPA 集成与回调安全不在现行 PRD 需求表内（无对应 FR），建议在 PRD §9 开放问题追加"#6 RPA/下游系统对接方式与回调安全要求"，评审后再把 ENT-07 升格为 FR-API-06/07。
- **docs/IMPORTANT.md**：已增 2026-07-24 快照行与 §5 阶段五挂账（本文档为权威细节）。
- **架构边界**：全部新模块遵守 `stores/interfaces.py` 协议注入 + live 依赖懒加载 + coverage omit 需附理由的既有惯例；文件 ≤300 行、函数 ≤50 行（`scripts/check_code_metrics.py` 把关）。

## 7. 验证清单（实现时逐项补测；本次静态审计未运行项目）

单测点（进 `tests/unit/`，维持覆盖率 ≥80%）：

1. ENT-01：日志 contextvar 注入/清理；JSON 字段齐全；异常处理器写日志且响应体不含栈。
2. ENT-03：五类事件各断言一次落盘 schema；轮转触发。
3. ENT-04：三角色×代表端点的 403 矩阵；两段式 key 向后兼容；过期 key 拒绝。
4. ENT-05：per-tenant 限额从 YAML 生效；worker 状态机各迁移；队列有界拒绝。
5. ENT-06：跨租户检索零命中；413 路径；脱敏正则用例；缺凭据 fail-fast。
6. ENT-07：作业状态机；幂等键重放去重；webhook 签名可验证、重试计数、白名单拒绝。
7. ENT-08：Prometheus 文本格式解析通过（用 promtext 简易 parser 断言）。

需运行环境的验证（记录待办，本环境不执行）：

- 多进程（`uvicorn --workers 2`）下限流/预算失效的复现与 Redis 实现修复对照。
- live SSE 长查询中途断连后作业恢复（ENT-05 checkpointer + ENT-07）。
- `p3_load_http.py` 压测下日志量与延迟开销（目标：P95 增幅 <3%）。
- Grafana/Prometheus 实际抓取 `/metrics-prom`；SIEM 摄取 audit_events 样例。

## 8. 本期明确不做（防范围蔓延；变更须过评审）

- SSO/OIDC/LDAP 对接、细粒度资源级 ACL（角色三档止步）
- 审计签名/区块链式防篡改（仅 prev-hash 预研）
- 多区域/多活部署、K8s Operator
- SIEM 产品选型（只保证 JSONL/OTLP 可摄取）
- 消息总线（Kafka 等）事件推送 — webhook 满足 RPA 场景后再评估

---

## 附录 A：审计覆盖文件

`api/`（app、auth、errors、envelope、schemas、routes/query、routes/knowledge、service、service_query、service_stream、sse）、`observability/`（metrics、trace）、`llm/`（budget、budget_policy、circuit）、`agent/`（guardrails、loop_stream_events）、`generation/audit_store.py`、`knowledge/review/queue.py`、`stores/neo4j_store.py`、`config.py`、`web/static/js/api.js`、`docs/ops-runbook.md`、`plan/engineering/cicd-observability.md`、`plan/phases/phase-4-pilot.md`、`phase-5-scale.md`、`docs/IMPORTANT.md`、`plan/roadmap.md`、`PRD.md`（需求索引）。检索确认零命中：`logging/getLogger/structlog`（src+scripts）、`webhook/callback_url`（src+configs）、`CORSMiddleware`。
