# 架构说明（ARCHITECTURE）

**更新日期：** 2026-07-25（ENT-01…08 增补；§6 审计结论为 2026-07-23 快照）
**定位：** 描述性文档 — 模块地图、查询生命周期、离线/在线双轨、边界规则，以及 2026-07-23 静态架构审计的结论与优化建议跟踪。强制性规则以 [`plan/engineering/rules.md`](../plan/engineering/rules.md) 为准；债务与延期以 [`IMPORTANT.md`](./IMPORTANT.md) 为准；ENT 企业级能力状态以 [`ENTERPRISE_READINESS.md`](./ENTERPRISE_READINESS.md) §3.5 为准。

---

## 1. 分层总览

```
                    ┌─────────────────────────────┐
  web/ (Vue 3 零构建) │  api/  FastAPI + auth/限流    │  cli/  agr-* 入口
                    └──────────────┬──────────────┘
                                   │ QueryService (api/service.py)
                    ┌──────────────▼──────────────┐
                    │  agent/  triage → fast_path  │
                    │  或 LangGraph StateGraph:     │
                    │  planner → executor → critic │
                    │  → answer（guardrails 全程）  │
                    └──────┬───────────────┬──────┘
              retrieval/    │               │  generation/（answer 在线 /
              vector·graph_beam·fulltext    │  offline_answer 离线；trace=
              → fusion (RRF) → cache        │  ReasoningChain 输出契约）
                    ┌──────▼───────────────▼──────┐
                    │  stores/  Protocol 接口 +     │   llm/  provider·budget·
                    │  factory 组合根（唯一入口）    │   budget_policy·circuit
                    └─────────────────────────────┘
  knowledge/  ingest → extract_* → graph_builder → incremental/resolution
              → review/queue（人工复核）→ ingest_tasks/ingest_worker（ENT-05）
  observability/（横切）logging_setup·trace·metrics·audit_events·redaction·otel_bridge
  eval/  金标·评分·baseline        api/rbac.py  三角色路由守卫（ENT-04）
```

依赖方向自上而下单向；应用代码只依赖 `stores/interfaces.py` 的
`GraphStore` / `VectorStore` / `FulltextStore` / `DocStore` 协议（自 ENT-06 起均接受
`tenant_id` 过滤；调度侧协议 `LimiterStore`/`BudgetStore`/`AuditSink` 见
`stores/scheduling_protocols.py`），
Neo4j / Qdrant 客户端类型只允许出现在 `stores/` 内部（factory 内惰性导入）。

## 2. 离线 / 在线双轨（默认离线）

| 层 | 离线（默认） | 在线（显式开启） |
|---|---|---|
| 图 | `InMemoryGraphStore` + `data/processed/seed_triples.jsonl` | Neo4j（`AGR_USE_LIVE_STORES=1` 或 `--neo4j`） |
| 向量 | 进程内 + 已持久化 embedding | Qdrant |
| LLM | `MockLLMProvider` + `generation/offline_answer.py` 启发式 | 真实 provider（`AGR_ALLOW_LLM=1` + `LLM_API_KEY`） |
| API | lifespan 中 `build_default_service()` → offline bundle | 同上两个开关叠加 |

`generation/offline_heuristics/` 是**只服务演示语料**的确定性规则集（保 CI 与 20 case
评测可复现），不是生产路径；在线行为的问题不要改它，反之亦然。

## 3. 一次查询的生命周期

1. `POST /v1/query`（`api/routes/query.py`）→ 中间件 `AuthRateLimitMiddleware`
   （`api/auth.py`：API Key → tenant、QPS/并发限流、`Principal` 注入）。
2. `QueryService.run_query`（`api/service.py`，持有 StoreBundle / audit store /
   review queue / `RetrievalCache` / `MultiLevelBudget`）→ `service_query.execute_run_query`
   （答案缓存按 tenant/user/params 取键；预算原子预扣）。
3. `agent/triage.py` 分诊：Fast Path（`agent/fast_path.py`，弱证据时
   `should_escalate_fast_path` 回退 Agentic）或 Agentic。
4. Agentic = LangGraph `StateGraph`：`planner → executor → critic →（回 executor｜answer）`，
   checkpointer（`agent/checkpointer.py`）保存状态；节点实现在
   `loop_runtime.py` / `loop_handlers.py`，`loop.py` 只负责建图。
5. Executor（`executor.py` + `executor_plan.py` / `executor_dispatch.py`）并行三路检索
   （向量 / 图 beam / BM25）→ `retrieval/fusion.py` RRF 融合，经 `RetrievalCache`。
6. Guardrails（`agent/guardrails.py`）全程约束：max hops、LLM 调用数、token 预算
   （`llm/budget.py`，租户级 `llm/budget_policy.py`）、超时、递归上限。
7. 输出 `ReasoningChain`（`generation/trace.py`；schema
   `configs/schema/reasoning_chain_v1.json`）→ 写入 `AuditStore`；
   SSE 变体 `POST /v1/query/stream`（`agent/loop_stream.py` + `api/service_stream.py`，
   LangGraph `stream(updates)` 真增量）。
8. `POST /v1/feedback` 把用户反馈挂到 chain，不准确的入 `knowledge/review/queue.py`。

横切（ENT-01…08）：auth 中间件绑定日志 contextvars（request_id/query_id/tenant_id/user_id
贯穿 JSON 日志）并 attach 入向 W3C traceparent；`tenant_id` 自 principal 透传 stores /
三路检索 / agent loop（租户作用域绕过检索缓存）；安全事件（鉴权失败/限流/上传/复核决议）
写 `observability/audit_events.py`；trace span 可选桥接 OTel（`otel_bridge.py`）。

## 4. API 面（实测自 `api/routes/`）

| 端点 | 用途 |
|---|---|
| `POST /v1/query` · `POST /v1/query/stream` | 问答（同步 / SSE） |
| `POST /v1/docs`（operator+） · `GET /v1/ingest-tasks/{task_id}` | 文档接入（应用层限额 5MB/20/白名单，ENT-06）与任务查询（`IngestTaskStore` 落盘，ENT-05） |
| `GET /v1/review-queue`（自租户） · `POST /v1/review-queue/{item_id}/decision`（operator+） | 人工复核 |
| `GET /v1/audit/queries/{query_id}` | 审计链回查（AC-3，自租户） |
| `POST /v1/feedback` | 反馈闭环（FR-OP-03） |
| `GET /v1/metrics`（admin） · `GET /v1/graph/entities` | 观测 / 图实体 |
| `GET /v1/traces/{query_id}` · `GET /v1/budget/snapshot` · `GET /v1/audit-events`（均 admin） | 排障闭环（ENT-02）：trace / 预算快照 / 安全事件 |
| `GET /healthz` · `GET /metrics-prom` · `GET /web` | 健康检查（含熔断器/复核积压/后端标识）/ Prometheus 抓取（ENT-08）/ 试用 UI —— 均免鉴权 |

CLI：`agr-ingest / build-graph / index / run-cases / run-baseline / eval / gen-cases /
pilot-triples / badcase / query / api`（`pyproject.toml [project.scripts]`，亦可
`python -m agentic_graphrag <cmd>`）。

## 5. 配置体系

`config.py` 合并 `configs/default.yaml`（`AppConfig`，可调参数；ENT-05 起含 `tenants:`
per-tenant 限额与 `retention:` 保留期，模型在 `config_enterprise.py`）与 `.env`
（`Settings`，pydantic-settings，密钥/端点）；env 覆盖 YAML；路径经 `resolve_path()`
锚定仓库根。**例外**：API 运行时开关（`AGR_ALLOW_LLM`、`AGR_USE_LIVE_STORES`、
`AGR_REQUIRE_AUTH`、`AGR_API_KEYS`（三段式 `tenant:key:role`）、
`AGR_RATE_LIMIT_QPS/CONCURRENT`、`AGR_TRUST_X_USER_ID`、`AGR_API_HOST/PORT/RELOAD`，
以及 ENT 新增 `AGR_LOG_LEVEL/FILE`、`AGR_REDACTION_ENABLED/PATTERNS`、
`AGR_OTEL_ENABLED/ENDPOINT/SAMPLE_RATE`）在调用点直读
`os.environ` —— 有意为之（测试 monkeypatch 友好、进程内可翻转），见 §7 建议 P-A5。

## 6. 静态架构审计结论（2026-07-23，未运行代码）

以 grep / 逐文件阅读核实；本环境无 `.venv`，ruff / pytest / 指标脚本未运行（见 §8）。

| # | 检查 | 结论 |
|---|---|---|
| 1 | `neo4j` / `qdrant` 导入泄漏到 `stores/` 之外 | **无** |
| 2 | 逆向分层导入（下层 import `api/`；`retrieval/stores/knowledge/llm` import `agent/`） | **无**；唯一例外是 `generation/offline_heuristics/mentions.py:8` 在函数内惰性导入 `agent.entities` —— 离线启发式专用，规避环依赖，可接受，勿改成模块级导入 |
| 3 | 文件行数（≤300） | 全部合规；`stores/neo4j_store.py` **恰好 300 行**（顶格），`config.py` 294 行（接近顶格） |
| 4 | `os.environ` 直读散布 | 集中在 `api/`（`app.py`、`auth.py`、`service.py`）共 9 处；truthy 判断习语 `in {"1","true","yes"}` 重复 **4 份**（`auth.py:55,60`、`service.py:52`、`app.py:168`） |
| 5 | 内联魔法默认值 | `auth.py:130-131` 限流默认 `"20"`/`"10"`；`app.py:161-162` host/port 默认内联 |
| 6 | 密钥硬编码 | 未发现（密钥走 `.env` / `Settings`） |

> **2026-07-25 增补：** 上表为 2026-07-23 快照。此后 ENT 变更：`neo4j_store.py` 已拆分
> （269 行，查询构造拆出 `neo4j_queries.py`，P-A3 解除）；全部 src 文件 `wc -l` 复核 ≤300
> （最大 `config.py` 298）；`os.environ` 直读点随 ENT 模块（logging_setup / redaction /
> otel_bridge / app lifespan）增加，仍集中于运行时开关语义，P-A5 结论不变。

## 7. 优化建议跟踪（P-A3 已解决；其余仅记录、未实施）

| ID | 建议 | 要点 |
|---|---|---|
| P-A1 | 归并 truthy env 习语：新建 ~15 行的 `api/env_flags.py` 提供 `env_flag(name)`（调用时读 env，保 monkeypatch 语义），替换 §6-4 的 4 处重复 | **必须**保留 `require_auth_enabled` / `trust_x_user_id_enabled` 函数（`tests/unit/test_security_budget_cache_config.py` 直接导入）；`service.py` 可 `import env_flag as _env_flag` 保内部名。不放进 `config.py`（已 294 行，加即破 300 上限） |
| P-A2 | `auth.py` 限流默认值、`app.py` host/port 提取具名常量（`_DEFAULT_RATE_LIMIT_QPS` 等） | 注意 ruff line-length 100，长行需折行 |
| P-A3 | ~~`stores/neo4j_store.py` 已顶格 300 行：下次任何改动前先拆分~~ — **已解决（ENT-06，2026-07-25）**：查询构造拆出 `neo4j_queries.py`，主文件现 269 行 | 先例 `neo4j_codec.py` / `neo4j_queries.py` 均按 读写/查询构造 维度拆分 |
| P-A4 | `config.py` 294 行：同上，预留拆分方案（如 `config_paths.py`） | 无需立即动 |
| P-A5 | 长期：评估把 `AGR_*` 收进 pydantic-settings `Settings` | 代价是失去调用时读取语义（测试与进程内翻转依赖它）；若收编需连带改造测试，收益有限，**低优先** |

## 8. 验证清单（应用上述任何建议或恢复环境后执行）

- [ ] `uv venv .venv && uv pip install -e ".[dev]"`（当前环境 `.venv` 缺失，全部门禁未跑）
- [ ] `ruff check src tests scripts` + `ruff format --check src tests scripts`
- [ ] `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`
- [ ] `python scripts/check_code_metrics.py`（文件/函数/嵌套/参数/圈复杂度）
- [ ] P-A1 实施后重点回归 `tests/unit/test_security_budget_cache_config.py`（导入面不变）
- [ ] P-A1/P-A2 实施后确认 `agr-api` 行为不变：`AGR_REQUIRE_AUTH` / `AGR_RATE_LIMIT_*` /
      `AGR_API_RELOAD` 语义与默认值逐一比对（默认 QPS 20、并发 10、port 8000）
- [ ] 本文档 §4 端点表与 `api/routes/` 保持同步（新增路由时更新）
