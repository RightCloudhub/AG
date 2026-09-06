# 运维手册（P4-REL-04；ENT-01…08 增补 2026-07-25）

## 服务启动

```bash
source .venv/bin/activate
# 离线默认（seed + MockLLM）
agr-api
# 或
uvicorn agentic_graphrag.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

试用界面：<http://localhost:8000/web>

启动期（lifespan）自动执行：`setup_logging()`（ENT-01）→ 可选 `setup_otel()`（ENT-08，
`AGR_OTEL_ENABLED=1` 时）→ **凭据 fail-fast 校验**（ENT-06b）：
`AGR_USE_LIVE_STORES=1` 要求 `NEO4J_URI/USER/PASSWORD` + `QDRANT_URL/COLLECTION` 全非空；
`AGR_ALLOW_LLM=1` 要求 `LLM_API_KEY` 非空且非占位符 —— 缺失直接 `RuntimeError` 拒绝启动。

## 环境变量

> `LLM_*` / `NEO4J_*` / `QDRANT_*` 走 `.env`（pydantic-settings）；**`AGR_*` 为进程环境变量
> （代码内 `os.environ` 直读），写进 `.env` 不生效**，需在 shell / service unit 中 export。

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` | 聊天 LLM（`.env`） |
| `NEO4J_*` / `QDRANT_*` | 外部存储（`.env`；live 模式启动期强校验） |
| `AGR_ALLOW_LLM=1` | API 启用真实 LLM |
| `AGR_USE_LIVE_STORES=1` | API 使用 Neo4j / Qdrant |
| `AGR_REQUIRE_AUTH=1` | 强制 API Key（生产必开；未设时匿名归入 `default` 租户） |
| `AGR_API_KEYS=tenant:key:role,...` | 租户密钥表；role ∈ `admin`/`operator`/`reader`（ENT-04）；兼容旧两段式 `tenant:key`（默认 reader） |
| `AGR_RATE_LIMIT_QPS` / `AGR_RATE_LIMIT_CONCURRENT` | 全局默认限流（20 / 10）；per-tenant 覆盖见下方 `tenants:` 配置 |
| `AGR_TRUST_X_USER_ID=1` | 显式信任客户端 `X-User-Id`（默认绑定 key 摘要） |
| `AGR_LOG_LEVEL` | 日志级别（默认 INFO）（ENT-01） |
| `AGR_LOG_FILE` | JSON 日志落盘路径（RotatingFileHandler；未设仅 stdout） |
| `AGR_REDACTION_ENABLED=1` | PII 脱敏管道（默认关）（ENT-06） |
| `AGR_REDACTION_PATTERNS` | 脱敏模式选择，逗号分隔或 `all`（内置：email / phone_cn / phone_intl / id_cn） |
| `AGR_OTEL_ENABLED=1` | 启用 OTel OTLP 导出（需 `pip install -e ".[otel]"`）（ENT-08） |
| `AGR_OTEL_ENDPOINT` / `AGR_OTEL_SAMPLE_RATE` | OTLP collector 地址 / 采样率（默认 0.1） |
| `AGR_API_HOST` / `AGR_API_PORT` / `AGR_API_RELOAD` | `agr-api` 服务参数（默认 0.0.0.0 / 8000） |

**Per-tenant 限额（ENT-05）**：`configs/default.yaml` 的 `tenants:` 段，per-tenant 覆盖
`qps` / `concurrent` / `max_llm_calls` / `max_tokens` / `max_cost_units`，构造
`RateLimiter` / `MultiLevelBudget` 时读入；未列出的租户用全局默认值。

## 角色与端点权限（ENT-04）

| 端点 | 最低角色 |
|------|----------|
| `POST /v1/query` · `/v1/query/stream` · `/v1/feedback` · `GET /v1/audit/queries/{id}`（自租户） · `GET /v1/review-queue`（自租户） · `GET /v1/ingest-tasks/{id}` · `GET /v1/graph/entities` · `GET /v1/me`（身份回显，匿名视作 reader） | reader |
| `POST /v1/docs` · `POST /v1/review-queue/{id}/decision` · `GET /v1/ingest-tasks`（任务列表，P5-UI-02） | operator |
| `GET /v1/metrics` · `GET /v1/traces/{id}` · `GET /v1/budget/snapshot` · `GET /v1/audit-events` | admin |
| `GET /healthz` · `GET /metrics-prom` · `/web` · `/docs` · `/openapi.json` | 公开（免鉴权） |

角色不足返回 403 `FORBIDDEN`。Key 过期由 `KeyInfo.expires_at` 支持；
`configs/api_keys.yaml` 注册表（过期/备注）目前是**独立组件未接入中间件**——生效路径是
`AGR_API_KEYS` env（见 ENTERPRISE_READINESS §3.5 待办）。

## 全链路排障（ENT-01/02）

一次失败请求的四点回查：**网关/响应 `request_id` → JSON 日志 → 审计链 → trace**。

1. 从失败响应 envelope `meta.request_id`（或 `X-Request-Id`）拿到 request_id。
2. 按 request_id 查 JSON 日志（`AGR_LOG_FILE`）。日志字段字典：

   | 字段 | 含义 |
   |------|------|
   | `ts` / `level` / `logger` / `message` | ISO 时间（UTC）/ 级别 / 记录器 / 内容（5xx 含栈） |
   | `request_id` | 请求 ID（中间件注入，contextvar 贯穿） |
   | `query_id` | 推理链 ID（进入查询流程后填充） |
   | `tenant_id` / `user_id` | 鉴权得到的租户 / 用户身份 |

3. 日志里的 `query_id` → 审计链回查（chain metadata 亦记录 `request_id`）：

   ```bash
   curl -s localhost:8000/v1/audit/queries/<query_id>          # 落盘：data/processed/audit_chains.jsonl
   ```

4. trace 明细（span 序列，admin key）：

   ```bash
   curl -s -H 'Authorization: Bearer <admin-key>' localhost:8000/v1/traces/<query_id>
   ```

`/healthz` 检查项：`graph`/`vector`/`graph_ping`、`graph_backend`/`vector_backend`、
`allow_llm`、`llm_circuit`（熔断器状态）、`review_queue_pending`（复核积压）。

## 错误码对照表

| 错误码 | HTTP | 常见根因 | 处置 |
|--------|------|----------|------|
| `INVALID_INPUT` | 400/404/413/422 | 参数越界、未知 ID、上传超限 | 看 details；404 对"不存在/跨租户"同形（防探测） |
| `UNAUTHORIZED` | 401 | 缺 key / key 无效或过期 | 核对 `AGR_API_KEYS`；查 `/v1/audit-events?action=auth_failure` |
| `FORBIDDEN` | 403 | 角色不足（ENT-04） | 换更高角色 key 或调整 key 的 role 段 |
| `RATE_LIMITED` | 429 | 租户 QPS / 并发超限 | 查 `tenants:` 覆盖；`/v1/audit-events?action=rate_limited` |
| `BUDGET_EXCEEDED` | 429 | 单查询护栏或租户/用户日预算触顶 | `/v1/budget/snapshot`（admin）看用量；调 `tenants:` 限额或等日窗口重置 |
| `TIMEOUT_PARTIAL` | 200(部分) | 护栏超时截断 | 看 chain 的 partial 摘要；必要时调 `timeout_seconds` |
| `SERVICE_UNAVAILABLE` | 503 | 服务未初始化 / 组件未配置 | 查启动日志（含 fail-fast 凭据报错） |
| `INTERNAL_ERROR` | 500 | 未处理异常 | 按 request_id 查日志（含栈），走四点回查 |

## 常见故障

### 1. 查询全失败 / 500

- 查 `/healthz`；按 request_id 查 JSON 日志（响应不含栈，栈只在服务端日志）
- 确认 seed 三元组存在：`data/processed/seed_triples.jsonl`

### 2. 成本熔断（429 BUDGET_EXCEEDED）

- 单查询：`max_hops` / `MAX_TOKENS_PER_QUERY` / `MAX_LLM_CALLS`
- 租户/用户：`MultiLevelBudget` 日窗口 — `GET /v1/budget/snapshot`（admin）看实时用量；
  per-tenant 限额调 `configs/default.yaml` `tenants:` 段
- 指标：`/v1/metrics` 的 `budget_trips` 或 Prometheus `agr_budget_trips_total`

### 3. 图谱回滚

```bash
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm --memory-graph  # 进程内
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm                 # Neo4j
```

增量冲突入审核队列：`data/processed/review_queue.jsonl`，API：`/v1/review-queue`。

### 4. 缓存清理

图谱/索引更新后调用 `RetrievalCache.on_index_update()`（代码侧）或重启进程（版本键失效）。
磁盘：`data/cache/`。注意：**检索缓存键已含 tenant_id**（`retrieval_key(query, tools, tenant_id=…)`），带租户的检索正常读写缓存，隔离由键本身保证（BL-04）。`RetrievalCache.persist_cache_stats()` 写 `data/cache/cache_stats.json`（仅计数，非 embedding 向量；无读回路径，仅供人工查看）。

### 5. 审计回查（推理链 + 安全事件）

```bash
curl -s localhost:8000/v1/audit/queries/<query_id>       # 推理链（自租户）
curl -s -H 'Authorization: Bearer <admin-key>' \
  'localhost:8000/v1/audit-events?since=<epoch>&tenant_id=acme&action=auth_failure&limit=200'
```

安全事件落盘 `data/processed/audit_events.jsonl`（append-only，按大小轮转）。已接采集点：
`auth_failure` / `rate_limited` / `doc_upload` / `review_decision`；
`budget_exceeded` / `feedback_submitted` / `config_change` 事件类型已定义、采集点待接
（见 ENTERPRISE_READINESS §3.5）。

### 6. 摄取任务停在 queued（ENT-05）

上传（`POST /v1/docs`）只落任务记录（`data/processed/ingest_tasks.jsonl`，状态机
`queued → extracting → (review) → done / failed`，另有 `empty` 终态）；
**API 进程不自动起 worker**，需单独运行：

```bash
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker --once   # 单轮
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker          # 常驻轮询（5s）
```

worker 消费任务：chunk → embed → 索引；也可在自定义部署里以 `IngestWorker` 后台线程内嵌。

## 数据保留与清理（ENT-06）

`configs/default.yaml` `retention:`（audit_chains 90 天 / audit_events 90 天 /
review_queue 30 天 / ingest_tasks 30 天）。清理脚本（先 `--dry-run`）：

```bash
PYTHONPATH=src python scripts/prune_data_files.py --dry-run
PYTHONPATH=src python scripts/prune_data_files.py
```

PII 脱敏：`AGR_REDACTION_ENABLED=1`（默认关）启用正则管道，挂在日志 formatter 与审计链
落盘前；`AGR_REDACTION_PATTERNS` 选择模式（默认 `all`）。

## 监控与告警（P4-REL-03 / ENT-08）

Prometheus 抓取 `GET /metrics-prom`（公开，文本协议）。指标：
`agr_queries_total`、`agr_latency_p50/p95/p99_ms`、`agr_route_queries_total{route}`、
`agr_errors_total{code}`、`agr_budget_trips_total`。

告警规则示例（阈值沿用 `plan/engineering/cicd-observability.md` §3.3）：

```yaml
groups:
  - name: agentic-graphrag
    rules:
      - alert: AgenticP95High
        expr: agr_latency_p95_ms > 8000
        for: 10m
        labels: {severity: high}
      - alert: ErrorRateHigh
        expr: rate(agr_errors_total[5m]) / rate(agr_queries_total[5m]) > 0.05
        for: 5m
        labels: {severity: high}
      - alert: BudgetTripsSpike
        expr: increase(agr_budget_trips_total[10m]) > 10
        labels: {severity: medium}
```

OTel：`pip install -e ".[otel]"` + `AGR_OTEL_ENABLED=1 AGR_OTEL_ENDPOINT=<collector>`；
进程内 trace span 桥接为 OTel span 并重键为公开 `query_id`；入向解析 W3C `traceparent`，
出向可用 `inject_trace_context()` 注入。无 SDK 时优雅退化为进程内 trace。
**collector / Jaeger / Tempo 实际导出与告警规则部署仍待环境验证。**

## 安全检查清单（P4-REL-02，2026-07-25 更新）

- [ ] 无硬编码密钥；生产 `AGR_REQUIRE_AUTH=1` 且 key 带角色段（reader 缺省）
- [ ] Cypher 参数化（Neo4j 适配器）
- [ ] 错误 envelope 不泄露内部路径 / 栈（栈只入服务端 JSON 日志）
- [x] 上传治理已在应用层兜底：单文件 ≤5MB（分片读取，超限立即中断）、单批 ≤20、类型白名单 md/txt（pdf 返回「暂不支持」）、非 UTF-8 拒绝、超限 413
      （反向代理限制仍建议作为外层）
- [ ] 租户隔离：应用层 `tenant_id` 已贯穿 stores / 检索 / agent / 审计 / 复核（ENT-06）；
      生产仍需物理分库或标签隔离核查 + 真实 Neo4j/Qdrant 跨租户回归（P4-REL-01 运维侧）
- [ ] 含 PII 语料上线前开 `AGR_REDACTION_ENABLED=1` 并核对模式覆盖

## 待部署环境验证（本地无法完成，勿以离线证据冒充）

- 多副本（`uvicorn --workers N`）下限流/预算为进程内状态 → 需 Redis 协调（ENT-05 后续）
- 真实 Neo4j / Qdrant 跨租户检索回归（ENT-06）
- OTLP collector 联调 + 告警规则实际部署（ENT-08）
- SQLite checkpointer（`.[sqlite-checkpoint]` extra，`make_checkpointer("sqlite")`）中断恢复演练
- 压测下日志开销复核（目标 P95 增幅 <3%，`scripts/p3_load_http.py`）
