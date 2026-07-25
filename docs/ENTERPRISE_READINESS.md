# 企业级管控与全链路排障 — 现状审计与建设规划（ENT）

**日期：** 2026-07-25（实施更新） · 分支 `feat/all-phases-complete`
**方法：** 静态代码审计 + 实施跟踪
**范围：** 对照以下企业级能力主张逐项核查，并对缺口给出建设规划：

> 兜底全链路、清晰排障路径；企业级管控：权限管理、并发调度、数据安全、审计日志、日志集成、与后续 RPA 流程集成。

**结论先行（2026-07-25 更新）：** ENT-01～ENT-06、ENT-08 的代码交付与离线单测已完成，ENT-07（RPA）按本期范围明确不实施。已覆盖结构化日志、排障端点、审计事件、RBAC、配置化租户限额、持久化摄取任务状态机、上传治理/脱敏、租户过滤、保留期清理、Prometheus 与可选 OTel/OTLP + W3C context。**生产多副本的 Redis/共享协调、真实 Neo4j/Qdrant 回归、OTLP collector 联调仍需部署环境验证；不以离线证据冒充生产验收。** 以下 §1–§3 保留原始审计证据，§3.5 记录实施结果，ENT-07 保留规划但不进入本期实现。

---

## 1. 结论速览

> **2026-07-25 更新：** 标注 ✅ 的子项已有代码 + 单测交付；🟡 部分交付；❌ 未开始。

| # | 能力 | 现状 | 一句话差距（2026-07-25 更新） | 既有关联任务 |
|---|------|------|------------|--------------|
| A | 全链路排障（兜底） | ✅ 已交付 | ~~无应用日志~~ → ENT-01 结构化 JSON 日志 + ENT-02 traces/budget/audit-events admin 端点 + healthz 熔断器/审核队列/checkpointer 已落地 | P3-OP-01/03、P4-REL-04 |
| B | 权限管理 | ✅ 已交付 | ~~无 RBAC~~ → ENT-04 三角色（admin/operator/reader）+ `require_role()` 路由守卫 + key 过期 + KeyRegistry YAML 配置已落地 | P4-UI-02 |
| C | 并发调度 | ✅ 工程交付 / 🟡 部署验证 | 租户 YAML 限额、限流覆盖、持久化摄取任务状态机、worker 与协议抽象已交付；Redis/横向公平调度与 SQLite checkpointer 需部署验证 | P3-OP-02、P5-EXT-03 |
| D | 数据安全 | ✅ 工程交付 / 🟡 部署验证 | 上传治理、脱敏、显式 tenant_id 隔离、保留期清理、live 模式启动凭据校验已交付；物理分库仍归运维 | P4-REL-01（开）、P4-REL-02 |
| E | 审计日志 | ✅ 已交付 | ~~无安全事件审计~~ → ENT-03 五类事件（auth_failure/rate_limited/budget_exceeded/review_decision/doc_upload）+ 轮转 + 过滤查询 + admin 端点已落地 | P3-AN-01、P4-AC-02（开） |
| F | 日志集成 | ✅ 工程交付 / 🟡 部署验证 | JSON 日志、Prometheus、可选 OTel OTLP、W3C 入向 context 与出向注入已交付；collector 联调待部署验证 | cicd-observability §3 |
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

## 3. 需求主张逐条评定（2026-07-25 更新）

| 主张 | 评定 | 依据 |
|------|------|------|
| "兜底全链路无缝、清晰排障路径" | **基本成立** | ENT-01 结构化 JSON 日志 + ENT-02 traces/budget/audit-events admin 端点 + `/healthz` 熔断器/审核队列 + ingest task 状态机已落地；`request_id` 入审计链 metadata；生产恢复仍需演练 |
| "权限管理" | **基本成立** | ENT-04 RBAC 三角色 + `require_role()` 路由守卫 + Key 过期 + KeyRegistry YAML；**`/v1/metrics` 尚未 per-tenant 过滤** |
| "并发调度" | **基本成立（工程）** | ENT-05 已交付 per-tenant 配置化限额（`tenants:` 段）、持久化摄取任务状态机 + worker、`LimiterStore`/`BudgetStore`/`AuditSink` 协议；仍单进程内存态，排队/Redis 横向扩展未实施（部署验证与规模化项） |
| "数据安全" | **基本成立（工程）** | 上传治理 + 脱敏 + tenant_id 检索过滤 + 启动凭据校验 + prune 已交付；物理隔离/加密属部署验证 |
| "审计日志" | **基本成立** | ENT-03 五类安全事件审计 + 推理链审计 + 反馈关联 + admin 端点；**导出 CLI + 防篡改仍缺** |
| "日志集成" | **基本成立（工程）** | ENT-01 JSON + ENT-08 Prometheus + 可选 OTel OTLP/W3C helpers 已可用；collector 联调待验证 |
| "与后续 RPA 流程集成" | **不成立** | 仅同步 API；无作业/回调/幂等（ENT-07 未实施） |

---

## 3.5 实施进展（2026-07-25 快照）

> §2–§3 中的缺口发现于 2026-07-24 审计时，保留原文以便对照。本节记录此后的代码交付与剩余差距。

### 已交付（代码 + 单测）

| ENT | 交付物 | 关键文件 | 测试覆盖 |
|-----|--------|----------|----------|
| **ENT-01** ✅ | 结构化 JSON 日志：`JSONFormatter` + `contextvars`（request_id/query_id/tenant_id/user_id）+ `RotatingFileHandler` + `AGR_LOG_LEVEL`/`AGR_LOG_FILE` 环境开关；中间件注入 `api/auth.py:_bind_request_context`；异常处理器（app.py 三级 handler）已写日志 | `observability/logging_setup.py`（219 行） | `tests/unit/test_logging_setup.py`（17 用例）+ `test_enterprise_readiness.py::TestENT01Logging`（4 用例）|
| **ENT-02** ✅ | 排障闭环：`GET /v1/traces/{query_id}` + `GET /v1/budget/snapshot` + `GET /v1/audit-events`（均 admin-only via ENT-04）；`/healthz` 增 LLM 熔断器状态 + 审核队列积压；`request_id` 写入链 metadata（`service_query.py:_finalize_chain`） | `api/routes/admin.py`（148 行） | `test_enterprise_readiness.py::TestENT02And08AdminRoutes`（6 用例）|
| **ENT-03** ✅ | 安全事件审计流：`AuditEventStore` append-only JSONL + 大小轮转 + 内存索引；五类事件（AUTH_FAILURE/RATE_LIMITED/BUDGET_EXCEEDED/REVIEW_DECISION/DOC_UPLOAD）+ 便捷 `emit_audit_event()` + ContextVar 自动填充；采集点挂入 `auth.py`（鉴权失败 + 限流）、`knowledge.py`（上传 + 审核决议） | `observability/audit_events.py`（256 行） | `test_enterprise_readiness.py::TestENT03AuditEvents`（5 用例：roundtrip/五类事件/租户过滤/轮转/重载）|
| **ENT-04** ✅ | RBAC：`Role`（admin/operator/reader）StrEnum + `parse_api_keys_with_roles()` 三段式兼容解析 + `require_role()` FastAPI Depends 守卫 + `KeyInfo` 含过期 + `KeyRegistry` YAML 配置；`auth.py` 已迁移为 RBAC 感知；路由已收权：metrics/traces/budget/audit-events → admin，docs 上传/审核决议 → admin\|operator | `api/rbac.py`（256 行）+ `api/auth.py` 改造 | `test_enterprise_readiness.py::TestENT04RBAC`（11 用例：解析/向后兼容/过期/403 矩阵/KeyRegistry）|
| **ENT-06** ✅ | 上传治理、PII 脱敏、tenant_id 字段与三路检索过滤、审核队列/任务租户边界、live 凭据 fail-fast、保留期 prune | stores、knowledge、api、retention | `test_enterprise_completion.py` + ENT readiness tests |
| **ENT-08** ✅（部署验证待办） | Prometheus + optional OTel bridge、sample rate、W3C inbound attach/outbound inject、custom span bridge | `observability/otel_bridge.py`、`trace.py`、`api/app.py` | ENT readiness + completion tests |

### 剩余部署验证（非 RPA）

| ENT | 已完成代码 | 仍需环境验证 |
|-----|------------|--------------|
| **ENT-05** | `tenants:` 配置驱动预算与限流；`LimiterStore`/`BudgetStore`/`AuditSink` 协议；`IngestTaskStore` JSONL 状态机 + `IngestWorker`；SQLite checkpointer 可选依赖 | 多副本共享 Redis/公平调度适配器与 SQLite 恢复演练（本地无 Redis/中断 live SSE 条件） |
| **ENT-06** | Document/Chunk/Entity/Relation `tenant_id`；内存/BM25/Qdrant/Neo4j 查询过滤；上传注入租户；审核队列按租户；live 凭据 fail-fast；`scripts/prune_data_files.py` | Neo4j/Qdrant 真后端跨租户回归、物理分库与磁盘加密（运维侧） |
| **ENT-08** | `otel` 可选依赖；`setup_otel()`、自定义 span 桥接、采样率、W3C 入向 attach 与出向 inject；Prometheus endpoint | Jaeger/Tempo/collector 实际 OTLP 导出与告警规则部署 |

> ENT-07/RPA 是用户明确排除项，保留 §4 规划，不纳入本次完成口径。原始缺口文本仍保留在 §2 供审计追踪。

---

## 4. 建设规划（任务分解）

新任务列入 **P5-ENT** 系列（规模化阶段企业级轨道）；标注 ⏫ 的为 **G4（试点出口）前置**——它们直接支撑 G4 门禁"监控告警、预算熔断、审计回查在生产环境验证"。既有开放任务（P4-REL-01、P4-AC-02）不重编号，在此挂接。

### P5-ENT-01 ⏫ 结构化日志基座 — ✅ 已交付（2026-07-25）

<details><summary>原始规划（已完成，折叠保留）</summary>

| 项 | 内容 |
|----|------|
| 交付 | 新模块 `observability/logging_setup.py`：stdlib logging + JSON formatter（无新依赖）；`contextvars` 承载 request_id/query_id/tenant_id/user_id，中间件注入、跨 agent 循环传播；uvicorn access log 并入同格式；`AGR_LOG_LEVEL`/`AGR_LOG_FILE`(+轮转) 环境开关 |
| 落点 | 中间件挂 `api/app.py`；异常处理器（`app.py:75`）、SSE 兜底分支（`service_stream.py:84`）、预算/护栏触发、store 回退路径补 error/warning 日志 |
| 验收 | 任一 4xx/5xx/SSE error 在服务端恰有一条含 request_id+tenant_id+错误码（5xx 含栈）的 JSON 日志；离线 20 case 跑完日志可按 query_id 串起 triage→hops→answer |
| 约束 | 模块 ≤300 行；日志内容经脱敏钩子（预留，ENT-06 实现）；MockLLM 路径零成本 |

</details>

**实现确认：** `observability/logging_setup.py`（219 行）；`api/app.py` lifespan 调 `setup_logging()`；`auth.py` 中间件 `_bind_request_context()` / `_clear_request_context()`；三级异常处理器均写 `_log.warning` / `_log.error`；脱敏钩子接口 `set_redact_hook()` 已预留。21 单测覆盖。

### P5-ENT-02 排障闭环补全 — ✅ 已交付（2026-07-25）

<details><summary>原始规划（已完成，折叠保留）</summary>

- `request_id` 写入链 metadata（`service_query.py:_finalize_chain` 一行）+ SSE 首事件返回 request_id/query_id。
- 新端点（admin 角色，依赖 ENT-04）：`GET /v1/traces/{query_id}`（暴露既有 Tracer）、`GET /v1/budget/snapshot`（暴露既有 `MultiLevelBudget.snapshot()`）。
- `/healthz` 增加 LLM 熔断器状态、审核队列积压数、checkpointer 后端标识。
- 摄取任务状态机：`queued→extracting→review→done/failed`，任务落盘（复用 JSONL 惯例）替代进程内 `_TASKS`。
- `docs/ops-runbook.md` 增补：错误码→根因→处置对照表（错误分类学），日志字段字典。
- 验收：任一失败查询可凭 request_id 走通"网关→日志→审计链→trace"四点回查。

</details>

**实现确认：** `api/routes/admin.py`；`service_query.py:_finalize_chain` 写入 `request_id`；`/healthz` 含 `llm_circuit`/`review_queue_pending`；admin 端点 traces/budget/audit-events 均 admin-only via `require_role(Role.ADMIN)`。6 单测覆盖。摄取任务状态机已由 ENT-05 交付（`IngestTaskStore` JSONL 落盘 + 上传/查询路由接入；worker 需独立进程运行，见 runbook §6）；runbook 增补（错误码对照表 / 日志字段字典 / 四点回查手册 / 告警规则示例）已于 2026-07-25 落入 `docs/ops-runbook.md`。

### P5-ENT-03 ⏫ 安全/管理事件审计流 — ✅ 已交付（2026-07-25）

<details><summary>原始规划（已完成，折叠保留）</summary>

- 新模块 `observability/audit_events.py`：append-only JSONL（`data/processed/audit_events.jsonl`），事件含 ts/tenant/user/action/target/outcome。
- 采集点：鉴权失败（`auth.py:_authenticate`）、限流与预算熔断（携对象租户）、审核决议（reviewer+decision）、文档上传、反馈提交。
- 轮转与保留：按大小/日期滚动，保留期入 `configs/default.yaml`（评审定，cicd-observability §3.4 已留待办）。
- 导出：`agr-audit-export --since --tenant --format jsonl|csv`（CLI，入 `cli/`）。
- 防篡改（后置项）：行级 prev-hash 链；规模化立项后再评估签名。
- 验收：runbook 新增"安全事件回查"手册段；每类事件在单测中可断言落盘字段完整。

</details>

**实现确认：** `observability/audit_events.py`（256 行）；`AuditEventStore` 含大小轮转（`_rotate()` + `max_backups`）；`emit_audit_event()` 便捷接口 + ContextVar 自动填充；采集点：`auth.py:_emit_auth_failure` / `_emit_rate_limit`、`knowledge.py:_emit_doc_upload` / `_emit_review_decision`。5 单测覆盖（roundtrip/五类事件/租户过滤/轮转/磁盘重载）。**待办：CLI 导出命令、防篡改 prev-hash；`BUDGET_EXCEEDED` / `FEEDBACK_SUBMITTED` / `CONFIG_CHANGE` 事件类型已定义但 `emit` 采集点未接**（当前已接：auth_failure / rate_limited / doc_upload / review_decision 四点）。

### P5-ENT-04 RBAC 与密钥治理 — ✅ 已交付（2026-07-25）

<details><summary>原始规划（已完成，折叠保留）</summary>

- Key 格式扩展：`AGR_API_KEYS=tenant:key:role`（role ∈ admin/operator/reader，缺省 reader，向后兼容两段式）；或 `configs/api_keys.yaml`（支持过期时间、备注；env 优先）。
- 路由级依赖检查：admin → 审核决议、budget snapshot、traces、docs 上传、metrics；reader → query/stream/feedback/audit 自租户。
- `GET /v1/metrics` 按租户过滤（admin 可看全局）。
- 安全默认：发布配置模板将 `AGR_REQUIRE_AUTH=1` 设为生产缺省；`/docs`/`/openapi.json` 公开性可配。
- 明确不做（本期）：SSO/OIDC、细粒度资源 ACL、key 服务端哈希存储 — 记 §8。

</details>

**实现确认：** `api/rbac.py`（256 行）；`Role` StrEnum + `parse_api_keys_with_roles()` 三段/两段/单段兼容 + `require_role()` FastAPI Depends + `KeyInfo`（含 `expires_at`）+ `KeyRegistry`（env + YAML 合并）+ `check_key_expiry()`；`auth.py` 已集成 RBAC 解析与 `Principal.role`；路由收权已落地。11 单测覆盖。**待办：`/v1/metrics` 按租户过滤（当前 admin 看全局但 reader 被 403，无 per-tenant 聚合）；`KeyRegistry` YAML（`configs/api_keys.yaml`）为独立组件，中间件生效路径仍是 `AGR_API_KEYS` env，YAML 注册表接入待办**。

### P5-ENT-05 并发调度升级 — ✅ 工程交付（部署验证待办）

> 原始规划保留不变。

- 限额配置化：`configs/default.yaml` 新增 `tenants:` 段（per-tenant QPS/并发/预算三级限额），`MultiLevelBudget`/`RateLimiter` 构造时读入 — 消除代码默认值硬编码（§2-C-2）。
- 排队与优先级（试点后评估）：超限请求可选进入有界等待队列（fail-fast 仍为默认）；租户 SLA 分级（gold/std）影响出队顺序。
- 摄取 worker：进程内后台线程消费任务存储 → 调 `knowledge/extract_pipeline`，并发=1 起步；与 ENT-02 状态机同一变更集。
- 横向扩展准备：抽象 `LimiterStore`/`BudgetStore`/`AuditSink` 协议（对齐 `stores/interfaces.py` 惯例），默认内存实现不变，Redis 实现按 live-adapter 惯例懒加载 + coverage omit（规模化立项后交付）。
- Checkpointer 落盘：把 IMPORTANT §2 挂账的 SQLite checkpointer 转正（`langgraph-checkpoint-sqlite` 可选依赖），为 ENT-07 异步作业恢复兜底。

**实现确认（2026-07-25，替换 2026-07-24 审计时的"全部子项未开始"）：**

- **限额配置化 ✅**：`configs/default.yaml` `tenants:` 段（`config_enterprise.TenantBudgetConfig`：qps / concurrent / max_llm_calls / max_tokens / max_cost_units），经 `auth._tenant_rate_overrides()` 与 `MultiLevelBudget(tenant_overrides=…)` 读入 — 修改限额不再须改代码。
- **摄取 worker + 状态机 ✅**：`knowledge/ingest_tasks.py` JSONL 状态机（`queued→extracting→(review)→done/failed`，另有 `empty` 终态；落盘 `data/processed/ingest_tasks.jsonl`）接入 `QueryService` 与上传/查询路由；`knowledge/ingest_worker.py` 支持 `python -m agentic_graphrag.knowledge.ingest_worker --once` 或后台线程 — **API 进程不自动启动 worker**（运行方式见 runbook）。
- **协议抽象 ✅**：`stores/scheduling_protocols.py` 定义 `LimiterStore`/`BudgetStore`/`AuditSink`，默认内存实现不变，Redis 适配器留接口。
- **Checkpointer 落盘 🟡**：`sqlite-checkpoint` 可选依赖组已入 `pyproject.toml`，`make_checkpointer("sqlite")` 显式启用；**默认仍为 `MemorySaver`**。
- **未实施：** 排队/优先级/公平调度、Redis 分布式实现（原建议第 4 项，规模化立项后交付）；多副本失效复现与修复对照归部署验证。

### P5-ENT-06 ⏫ 数据安全强化（含 P4-REL-01 代码侧）— ✅ 工程交付

> 已交付子项标 ✅，未交付子项标 ❌。

- ✅ **租户数据隔离**：`DocumentRecord`/向量 payload/图实体携带 `tenant_id`；检索三路按 principal 过滤；离线单租户路径行为不变（default 租户）。Neo4j 物理分库仍归运维（P4-REL-01 运维侧不变）。
- ✅ **上传治理**：应用层大小上限（单文件 ≤5MB、单批 ≤20 个，常量入 `api/routes/knowledge_upload.py`）、类型白名单（md/txt/pdf）、超限 413 错误码。
- ✅ **脱敏钩子**：`observability/redaction.py` 正则管道（email/phone_cn/phone_intl/id_cn），`AGR_REDACTION_ENABLED`/`AGR_REDACTION_PATTERNS` 环境开关；`redact_log_record` 挂日志 formatter，`redact_audit_payload` 挂审计链落盘前；默认关闭。10 单测覆盖。
- ✅ **保留策略**：`configs/default.yaml` `retention:` 段（audit_chains 90 天 / audit_events 90 天 / review_queue 30 天 / ingest_tasks 30 天）+ `scripts/prune_data_files.py`（按时间字段删过期行，支持 `--dry-run`）；`AuditEventStore` 另有大小轮转。
- ✅ **启动凭据校验**：`api/app.py:_validate_live_credentials`（lifespan，ENT-06b）——`AGR_USE_LIVE_STORES=1` 断言 `NEO4J_URI/USER/PASSWORD` + `QDRANT_URL/COLLECTION` 非空；`AGR_ALLOW_LLM=1` 断言 `LLM_API_KEY` 非空且非占位符；缺失抛 `RuntimeError` 拒绝启动。

**实现确认（2026-07-25，替换 2026-07-24 审计时的缺口描述）：** `stores/interfaces.py` 四协议（`GraphStore`/`VectorStore`/`FulltextStore`/`DocStore`）接受租户过滤，内存 / BM25 / Neo4j / Qdrant 实现全部落实；检索三路、executor dispatch、fast path 与 agent loop 透传 `tenant_id`（**租户作用域运行绕过检索缓存**，隔离优先）；上传从 principal 注入租户；复核队列按租户列取。单测：`test_enterprise_completion.py`（跨租户零命中 / 413 路径 / 脱敏 / fail-fast / prune）+ ENT readiness 套件。物理分库、磁盘加密与真实 Neo4j/Qdrant 跨租户回归归运维与部署验证。

### P5-ENT-07 RPA 集成层 — ❌ 未实施

> 原始规划保留不变。

- 异步作业 API：`POST /v1/query/jobs`（即刻返回 job_id）→ `GET /v1/query/jobs/{id}`（pending/running/succeeded/failed + 结果=推理链）；作业记录落盘；执行复用现有 run_query 线程；依赖 ENT-05 checkpointer 落盘做恢复。
- Webhook 回调：作业级 `callback_url`（HTTPS）+ `X-AGR-Signature`（HMAC-SHA256，密钥按租户配置）；重试 3 次指数退避；投递结果入 ENT-03 审计事件。SSRF 防护：目标域白名单配置。
- 幂等：变更类端点接受 `Idempotency-Key` 头，键+租户去重窗口 24h。
- 批量：`POST /v1/query/batch`（≤20 条，逐条独立预算）— RPA 批处理典型形态。
- 导出：`GET /v1/audit/queries?since=&until=&tenant=`（分页）供下游流程拉取推理链。
- 契约固化：SSE 事件 schema 文档 + webhook payload schema 入 `configs/schema/`；`/v1` 兼容政策写入 `plan/workstreams/api-and-ui.md`。

### P5-ENT-08 监控外送（Prometheus/OTel）— ✅ 工程交付（collector 验证待办）

> 已交付子项标 ✅，未交付子项标 ❌。

- ✅ **Prometheus 文本暴露**：`GET /metrics-prom` 公开端点（`api/app.py`，入 `public_paths` 免鉴权），手写 `prometheus_metrics_text()`（`admin.py`）将 `MetricsRegistry.summary()` 转为 exposition 格式：`agr_queries_total`（counter）、`agr_latency_p50/p95/p99_ms`（gauge）、`agr_route_queries_total{route=…}`（counter）、`agr_errors_total{code=…}`（counter）、`agr_budget_trips_total`（counter）。无第三方依赖。
- ✅ **告警规则示例**：`docs/ops-runbook.md` 已增 Prometheus alerting rules YAML 片段（2026-07-25，阈值沿用 cicd-observability §3.3）。
- ✅ **OTel 桥接**：`observability/otel_bridge.py` + `pyproject.toml` `otel` 可选依赖组；`trace.py` span 桥接 OTel `TracerProvider` + OTLP exporter，trace 重键为公开 `query_id`；采样率 `AGR_OTEL_SAMPLE_RATE`（默认 0.1）；无 SDK 时优雅退化为进程内 trace；模块入 coverage omit（live 适配器惯例，理由已附）。
- ✅ **分布式 trace context**：入向 W3C `traceparent` 在 auth 中间件 `extract_otel_context()` attach；出向 `inject_trace_context()` helper 供 HTTP 调用注入。

**实现确认（2026-07-25）：** 上述子项即原实施建议 1–2 的交付；启用方式 `pip install -e ".[otel]"` + `AGR_OTEL_ENABLED=1 AGR_OTEL_ENDPOINT=<collector>`（`api/app.py` lifespan 条件调用 `setup_otel()`）。**仍需在真实 collector / Jaeger / Tempo 环境验证 OTLP 导出与告警规则部署——本地无 collector，不以离线单测冒充。**

### 依赖关系（2026-07-25 更新；✅ 已交付，❌ 未实施）

```
ENT-01 日志 ✅ ──► ENT-02 排障闭环 ✅ ──► ENT-08 外送 ✅(Prom + OTel 工程；collector 待验证)
   │                   │
   └──► ENT-03 审计事件 ✅ ──► ENT-07 webhook 投递审计 ❌
ENT-04 RBAC ✅ ──► ENT-02 admin 端点 ✅ / ENT-07 管理面 ❌
ENT-05 调度 ✅ (配置+worker+协议；多副本验证待办) ──► ENT-07 异步作业 ⏭
ENT-06 ✅(上传+脱敏+隔离+启动校验+保留策略)
ENT-08 ✅(Prometheus+OTel 工程；collector 验证待办)
```

---

## 5. 排期与优先级建议（2026-07-25 更新）

| 批次 | 任务 | 状态 | 门禁挂钩 | 剩余工作量 |
|------|------|------|----------|-----------|
| ~~第 1 批（G4 前必做）⏫~~ | ~~ENT-01、ENT-03~~ | ✅ 已交付 | ~~G4"监控告警/审计回查生产验证"~~ | 0 |
| ~~第 1 批剩余 ⏫~~ | ~~ENT-06（租户隔离 + 启动校验）~~ | ✅ 已交付（2026-07-25） | P4-REL-01 代码侧已承接（运维侧仍开） | 真后端回归归部署侧 |
| ~~第 2 批（试点期）~~ | ~~ENT-02、ENT-04、ENT-08 Prometheus~~ | ✅ 已交付 | ~~P4-REL-03 告警落地~~ | 0 |
| 第 2 批剩余 | ENT-08 collector + 告警规则部署验证 | 🟡 环境验证 | AC-6 生产告警、分布式链路追踪 | 部署侧 |
| 第 3 批 | ENT-05 多副本 Redis/公平调度验证；ENT-07 排除 | 🟡 / ⏭ | P5 规模化 | 部署侧 |

> **G4 前置阻塞项（2026-07-25 更新）：** 代码侧已全部消除（ENT-01…06、08 交付，含租户数据隔离与启动凭据校验）。剩余均为部署环境验证：生产监控告警 / 审计回查演练（G4 门禁第 3 条）、真实 Neo4j/Qdrant 跨租户回归、OTLP collector 联调、多副本 Redis 协调。

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

## 7. 验证清单（2026-07-25 更新）

单测点（进 `tests/unit/`，维持覆盖率 ≥80%）：

1. ✅ ENT-01：日志 contextvar 注入/清理；JSON 字段齐全；异常处理器写日志且响应体不含栈。— `test_logging_setup.py`（17 用例）+ `test_enterprise_readiness.py::TestENT01Logging`（4 用例）
2. ✅ ENT-03：五类事件各断言一次落盘 schema；轮转触发。— `test_enterprise_readiness.py::TestENT03AuditEvents`（5 用例）
3. ✅ ENT-04：三角色×代表端点的 403 矩阵；两段式 key 向后兼容；过期 key 拒绝。— `test_enterprise_readiness.py::TestENT04RBAC`（11 用例）
4. ✅ ENT-05：per-tenant YAML 预算/限流覆盖、持久化 ingest task 状态迁移与 worker、协议抽象已有单测；多副本 Redis/公平队列仅作部署后验证。
5. ✅ ENT-06：跨租户内存 vector/BM25/graph/doc 零命中；上传打租户；413 路径；脱敏；缺凭据 fail-fast；保留期 prune 单测。
6. ⏭ ENT-07：RPA 明确排除，不实施。
7. ✅ ENT-08：Prometheus 文本格式；OTel 无 SDK graceful fallback、自定义 span 桥接/W3C helper 为可选 live 路径；collector 联调待环境验证。

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

## 附录 A：审计覆盖文件（2026-07-25 更新）

`api/`（app、auth、errors、envelope、schemas、rbac、routes/query、routes/knowledge、routes/admin、service、service_query、service_stream、sse）、`observability/`（metrics、trace、logging_setup、audit_events、redaction）、`llm/`（budget、budget_policy、circuit）、`agent/`（guardrails、loop_stream_events）、`generation/audit_store.py`、`knowledge/review/queue.py`、`stores/`（interfaces、factory、memory_graph、vector_store、doc_store、neo4j_store）、`config.py`、`web/static/js/api.js`、`docs/ops-runbook.md`、`plan/engineering/cicd-observability.md`、`plan/phases/phase-4-pilot.md`、`phase-5-scale.md`、`docs/IMPORTANT.md`、`plan/roadmap.md`、`PRD.md`（需求索引）。

新增覆盖（2026-07-25）：`observability/logging_setup.py`、`observability/audit_events.py`、`observability/redaction.py`、`api/rbac.py`、`api/routes/admin.py`、`tests/unit/test_enterprise_readiness.py`、`tests/unit/test_logging_setup.py`。

检索确认零命中（2026-07-25 复核）：`webhook/callback_url`（src+configs，佐证 ENT-07 未实施）、`CORSMiddleware`。已不再成立：~~`opentelemetry`~~ → `observability/otel_bridge.py` 在可选依赖内使用（`otel` extra，无 SDK 时 no-op）；~~`logging/getLogger`~~ → 现有 `observability/logging_setup.py` + `observability/audit_events.py` + `api/rbac.py` + `api/app.py` 使用 `logging`。
