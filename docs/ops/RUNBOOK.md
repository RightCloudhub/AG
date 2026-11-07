# 运维手册

## 服务启动

环境准备（解释器 + 锁定依赖 + `.env`）见 [README 快速开始](../../README.md) 或直接运行
`./scripts/setup.sh`；环境体检运行 `python scripts/doctor.py`。

知识库双模、版本发布与恢复见 [图谱平台运行手册](GRAPH_PLATFORM.md)；
七角色模型目录、鉴权与测试发布见 [模型配置手册](MODEL_PROFILES.md)。

```bash
source .venv/bin/activate
# 离线默认（seed + MockLLM）
agr-api
# 或
uvicorn agentic_graphrag.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Web 工作台：<http://localhost:8000/web>

启动期（lifespan）自动执行：`setup_logging()`（ENT-01）→ 可选 `setup_otel()`（ENT-08，
`AGR_OTEL_ENABLED=1` 时）→ **解析运行 Profile 并按能力矩阵装配**（`bootstrap/`）→ **凭据 fail-fast 校验**（ENT-06b）：
`live` Profile（或旧开关 `AGR_USE_LIVE_STORES=1`）要求 `NEO4J_URI/USER/PASSWORD` + `QDRANT_URL/COLLECTION` 全非空，
`hybrid` 同样走该校验（`api/app.py` 的 `_missing_store_credentials`），因此也要求这五项全非空。
真实模型调用需显式设置 `AGR_ALLOW_LLM=1`。传统 strong/light 配置要求 `LLM_API_KEY`
非空且非占位符，缺失时拒绝启动；本地无鉴权服务需显式设置 `LLM_AUTH_REQUIRED=false`。
启用七角色目录时，按各 provider 的 `auth_required` / `secret_ref` 校验凭据，
不统一要求全局 `LLM_API_KEY`。

**不再有隐式回退**：Neo4j 或 Qdrant 不可达时启动失败（`ProfileError`），不会悄悄换成内存图或内存向量。
需要“Neo4j + 内存向量”时请显式设置 `AGR_PROFILE=hybrid`。

### 运行 Profile

| `AGR_PROFILE` | 图 | 向量 | 缓存 | seed 图 | 说明 |
|---|---|---|---|---|---|
| `offline`（默认） | 内存 | 内存 | 开 | 加载 | 本地 / CI |
| `live` | Neo4j | Qdrant | 开 | 不加载 | 生产；旧开关 `AGR_USE_LIVE_STORES=1` 等价 |
| `hybrid` | Neo4j | 内存 | 开 | 不加载 | 显式混合部署 |
| `eval` | 内存 | 内存 | 关 | 加载 | `agr-run-cases` 默认使用；不读取本地 BM25/向量索引快照 |

`AGR_ALLOW_LLM=1` 在所有 Profile 下均可启用真实 LLM，凭据按上述配置方式校验；
`eval` 下真实 LLM 的响应可从 `data/cache/llm` 磁盘缓存回放。

## 环境变量

> `LLM_*` / `NEO4J_*` / `QDRANT_*` 走 `.env`（pydantic-settings）；多数 **`AGR_*` 为进程环境变量
> （代码内 `os.environ` 直读），写进 `.env` 不生效**，需在 shell / service unit 中 export。
> 例外：`AGR_RERANK` / `AGR_CRITIC_MODE` 走 `Settings`（`.env` 生效），Web 应用的 `AGR_WEB_*` /
> `AGR_DATABASE_URL` / `AGR_DEMO_PASSWORD` 由 `WebSettings` 从 `.env` 读取。

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` | 传统 strong/light LLM 配置（`.env`）；七角色目录也可用 `secret_ref` 引用 `LLM_API_KEY` |
| `LLM_AUTH_REQUIRED` | 传统 LLM 是否需要鉴权，默认 `true`；本地无鉴权服务显式设 `false`（`.env`） |
| `NEO4J_*` / `QDRANT_*` | 外部存储（`.env`；live 模式启动期强校验） |
| `AGR_PROFILE` | 运行 Profile：`offline`（默认）/ `live` / `hybrid` / `eval`，非法值拒绝启动 |
| `AGR_ALLOW_LLM=1` | 显式启用真实 LLM；传统配置或各 provider 分别校验鉴权要求 |
| `AGR_USE_LIVE_STORES=1` | 旧开关，等价于 `AGR_PROFILE=live`（未设置 `AGR_PROFILE` 时生效） |
| `AGR_INGEST_INLINE=1` | API 进程内启动 ingest worker：上传 → 索引 → 抽取入图 → 冲突复核（离线模式下上传入图的唯一方式）；`AGR_INGEST_POLL_SECONDS` 调轮询间隔（默认 2） |
| `AGR_REQUIRE_AUTH=1` | 强制 API Key（生产必开；未设时匿名归入 `default` 租户） |
| `AGR_API_KEYS=tenant:key:role,...` | 租户密钥表；role ∈ `admin`/`operator`/`reader`（ENT-04）；兼容旧两段式 `tenant:key`（默认 reader） |
| `AGR_RATE_LIMIT_QPS` / `AGR_RATE_LIMIT_CONCURRENT` | 全局默认限流（20 / 10）；per-tenant 覆盖见下方 `tenants:` 配置 |
| `AGR_TRUST_X_USER_ID=1` | 显式信任客户端 `X-User-Id`（默认绑定 key 摘要） |
| `AGR_LOG_LEVEL` | 日志级别（默认 INFO）（ENT-01） |
| `AGR_LOG_FILE` | JSON 日志落盘路径（RotatingFileHandler；未设仅 stderr） |
| `AGR_REDACTION_ENABLED=1` | PII 脱敏管道（默认关）（ENT-06） |
| `AGR_REDACTION_PATTERNS` | 脱敏模式选择，逗号分隔或 `all`（内置：email / phone_cn / phone_intl / id_cn） |
| `AGR_OTEL_ENABLED=1` | 启用 OTel OTLP 导出（需 `./scripts/setup.sh --extras dev,otel`）（ENT-08） |
| `AGR_OTEL_ENDPOINT` / `AGR_OTEL_SAMPLE_RATE` | OTLP collector 地址 / 采样率（默认 0.1） |
| `AGR_API_HOST` / `AGR_API_PORT` / `AGR_API_RELOAD` | `agr-api` 服务参数（默认 0.0.0.0 / 8000） |
| `AGR_RERANK` | 实验开关（V2 O-5）：`none`（默认）/ `lexical` / `http`；`http` 需在 `retrieval.rerank.endpoint` 配置 TEI / Infinity 的 `/rerank`。此项走 pydantic-settings，`.env` 中也生效 |
| `AGR_CRITIC_MODE` | 实验开关（V2 O-7）：`llm`（默认）/ `tiered` / `offline`。同样可写在 `.env` |

**Per-tenant 限额（ENT-05）**：`configs/default.yaml` 的 `tenants:` 段，per-tenant 覆盖
`qps` / `concurrent` / `max_llm_calls` / `max_tokens` / `max_cost_units`，构造
`RateLimiter` / `MultiLevelBudget` 时读入；未列出的租户用全局默认值。

## 角色与端点权限（ENT-04）

| 端点 | 最低角色 |
|------|----------|
| `POST /v1/query` · `/v1/query/stream` · `/v1/feedback` · `GET /v1/audit/queries/{id}`（自租户） · `GET /v1/graph/entities` | reader |
| `POST /v1/docs` · `GET /v1/ingest-tasks` · `GET /v1/ingest-tasks/{id}` · `GET /v1/review-queue` · `POST /v1/review-queue/{id}/decision` | admin / operator |
| `GET /v1/metrics` · `GET /v1/traces/{id}` · `GET /v1/budget/snapshot` · `GET /v1/audit-events` | admin |
| `GET /v1/me` | 当前身份策略允许时可用；启用强制鉴权后需有效 API Key |
| `GET /healthz` · `GET /metrics-prom` · `/web` · `/docs` · `/openapi.json` | 公开（免鉴权） |

角色不足返回 403 `FORBIDDEN`。Key 过期由 `KeyInfo.expires_at` 支持；
`api/rbac.py` 的 `KeyRegistry` 支持 YAML 注册表（过期 / 备注），但**仓库不含 `configs/api_keys.yaml`，
也没有代码加载它**；API 鉴权当前只读 `AGR_API_KEYS` 环境变量。

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
| `INVALID_INPUT` | 400/404/413/415/422 | 参数越界、未知 ID、上传超限、类型不支持、无法解码 | 415 检查签名及 MinerU 配置；422 检查编码/正文；404 对"不存在/跨租户"同形（防探测） |
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

### 3.5 worker 写入的文档 API 检索不到（全文）

- `offline` Profile 下全文索引是进程内的，独立 worker 的写入对 API 不可见（worker 启动时会打印 `Fulltext store ... is process-local` 警告）。
- `live` / `hybrid` 使用共享快照 `data/indexes/bm25.json`：确认 API 和 worker 挂的是同一个卷、同一个 `paths.indexes_dir`。快照损坏时日志会出现 `fulltext snapshot reload failed`，进程继续使用上一次的正常状态。
- 注意：`hybrid` 的向量库是内存实现，worker 写入的向量对 API 同样不可见；需要共享向量时请用 `live`（Qdrant）。

### 4. 缓存清理

图谱/索引更新后调用 `RetrievalCache.on_index_update()`（代码侧）或重启进程（版本键失效）。
磁盘：`data/cache/`。注意：**检索缓存键已含 tenant_id**（`retrieval_key(query, tools, tenant_id=…)`），带租户的检索正常读写缓存，隔离由键本身保证（BL-04）。`RetrievalCache.persist_cache_stats()` 写 `data/cache/cache_stats.json`（仅计数，非 embedding 向量；无读回路径，仅供人工查看）。

缓存只接收健康结果：融合和 rerank 完成后，只要本次查询共享的 `ChannelHealth` 已记录任何非 OK 状态，就跳过该次及后续子查询的检索缓存写入，包括 embedding / reranker 回退。答案的 `degraded_channels` 非空时同样不写缓存，不要求触发 `low_confidence`。故障期间重复查询会重试后端，恢复后可重新获取完整证据；需要限制重试压力时应使用熔断或退避。检索及答案缓存均为进程内存缓存，部署修复时重启进程即可清除旧版本可能留下的降级结果。

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
默认不启动处理器。本地离线体验可在启动 API 时显式开启同进程 worker：

```bash
AGR_PROFILE=offline AGR_ALLOW_LLM=0 AGR_INGEST_INLINE=1 agr-api
```

独立多租户应用使用相同开关启动 `agr-webapp`，完整命令见
[Web 使用指南](../product/WEBAPP_GUIDE.md#启动应用)。同进程处理器复用 API 的图谱、
向量和全文索引，完成分块、索引、事实抽取与冲突审核；轮询默认 2 秒，
可用 `AGR_INGEST_POLL_SECONDS` 调整。

使用共享持久化后端的部署也可单独运行 worker：

```bash
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker --once   # 单轮
PYTHONPATH=src python -m agentic_graphrag.knowledge.ingest_worker          # 常驻轮询（5s）
```

独立 worker 完成 chunk → embed → 索引 → 抽取入图（`ingest_worker_cli.py` 装配 `GraphIngestor`）；
离线模式下它的图与内存索引都是进程本地的，对 API 不可见，因此离线场景推荐上面的同进程处理器（`AGR_INGEST_INLINE=1`）。可见性限制见 §3.5。

### 6.1 文档解析与分块（BL-08）

API 与 CLI 共用解析端口。默认离线支持 `.md`、`.txt`、`.html`、`.htm`，检测文本编码
（含 GBK/GB18030）并去 BOM；HTML 保留标题/表格、移除导航与页脚。现有 Web 文件选择器
仍限定 MD/TXT；新增格式使用 `POST /v1/docs` 或 `agr-ingest --input <目录>`。

PDF、DOCX、PPTX、XLSX、PNG/JPEG/GIF/TIFF/BMP/WebP 需要部署支持相应格式的 MinerU
HTTP 服务，然后在 `configs/default.yaml` 中显式设置：

```yaml
knowledge:
  chunk_size_tokens: 512
  chunk_overlap_tokens: 64
  tokenizer: cl100k_base
  parsing:
    encoding_min_confidence: 0.8
    encoding_preference: gb18030
    mineru:
      enabled: true
      endpoint: http://127.0.0.1:8001/file_parse
      timeout_seconds: 120
```

接口使用 multipart 字段 `files`，附带真实 MIME；表单包含 `return_md=true`、
`return_images=false`、`return_content_list=false`、`parse_method=auto`。响应合约为
`{"results":{"文件名":{"md_content":"# Markdown 正文"}}}`，一次只发一个文件。
可选 `X-MinerU-Version` 响应头写入文档元数据。仓库不安装/下载 OCR 模型；离线测试
mock 此接口，部署前需用真实 PDF、Office 和扫描件验证所用服务版本的解析能力。

未配置 MinerU 或签名与扩展名不符返回 415；无法可靠解码/无正文返回 422；服务错误或
超时返回 503。每文件 5 MiB、每批 20 个的限制不变（超限 413）。chunk 预算包含
breadcrumb，大表格只按整行拆分并重复表头，单个代码块/列表项/引用块或表头加一行
无法容纳时任务会 failed，提示增大 `chunk_size_tokens`。

旧 `chunk_size_chars` / `chunk_overlap_chars` 必须迁移为 token 配置；旧键不被静默忽略。
默认词表随包分发，无首次网络下载。本地自定义 tokenizer 的 JSON 合约见
[编码器说明](../../src/agentic_graphrag/knowledge/tokenizers/README.md)。现有 doc_id 和
chunk ID 格式不变，重新摄取会按新分块规则生成内容；本变更不重建既有索引或 embedding。

## 数据保留与清理（ENT-06）

`configs/default.yaml` `retention:`（audit_chains 90 天 / audit_events 90 天 /
review_queue 30 天 / ingest_tasks 30 天）。清理脚本（先 `--dry-run`）：

```bash
PYTHONPATH=src python scripts/prune_data_files.py --dry-run
PYTHONPATH=src python scripts/prune_data_files.py
```

PII 脱敏：`AGR_REDACTION_ENABLED=1`（默认关）启用正则管道，挂在日志 formatter 与审计链
落盘前；`AGR_REDACTION_PATTERNS` 选择模式（默认 `all`）。
审计中的机器标识、来源哈希及版本字段保持原值，以便回查和删除重检；正文、引用摘录及
段落说明仍脱敏。

## 监控与告警（P4-REL-03 / ENT-08）

Prometheus 抓取 `GET /metrics-prom`（公开，文本协议）。指标：
`agr_queries_total`、`agr_latency_p50/p95/p99_ms`、`agr_route_queries_total{route}`、
`agr_errors_total{code}`、`agr_budget_trips_total`、`agr_event_handler_failures_total{handler}`（V2 O-4：缓存失效等关键订阅者在重试后仍失败的次数；出现即说明缓存已被 fail-safe 清空，需要排查）、
`agr_degradation_total{component,reason}`（O-9：被吞掉的依赖失败；`component` ∈ `vector`/`fulltext`/`graph`/`rerank`/`embedding`/`llm` 或子系统名，`reason` 是固定 slug，出现即说明有通道降级）。

查询响应会同时返回 `degraded_channels`（每个降级通道及其原因）与 `low_confidence`；SSE 在 `answer` 之前多发一个 `degraded` 事件。当 `configs/default.yaml` 的 `degradation.low_confidence_channels`（默认 `[vector, graph]`）全部为 `failed` 时，答案被标记为不可靠 —— 这类查询的 `degraded_channels` 与告警应一起看。

查询执行前会绑定本次 `tenant_id` / `query_id` / `user_id`，三个线程池为每个任务单独复制上下文；日志中的 `query_id` 与返回答案一致。SSE 在每次迭代和关闭时恢复自己的上下文，避免交错消费的查询串用身份。工具规划、triage 和 entailment 校验失败也会报告 `llm` 降级，即使答案正常生成；此类答案不写入答案缓存。

告警规则示例（阈值沿用 `plan/engineering/cicd-observability.md` §3.3）：

```yaml
groups:
  - name: agentic-graphrag
    rules:
      - alert: AgenticP95High
        expr: |
          histogram_quantile(0.95, sum by (le) (rate(agr_query_latency_ms_bucket{route="agentic"}[5m]))) > 8000
        for: 10m
        labels: {severity: high}
      - alert: FastPathP95High
        expr: |
          histogram_quantile(0.95, sum by (le) (rate(agr_query_latency_ms_bucket{route="fast_path"}[5m]))) > 3000
        for: 10m
        labels: {severity: high}
      - alert: LLMCircuitOpen
        expr: agr_llm_circuit_open == 1
        for: 5m
        labels: {severity: high}
      - alert: ErrorRateHigh
        expr: rate(agr_errors_total[5m]) / rate(agr_queries_total[5m]) > 0.05
        for: 5m
        labels: {severity: high}
      - alert: EventHandlerFailure
        expr: increase(agr_event_handler_failures_total[5m]) > 0
        labels: {severity: high}
      # O-9: 第一条抓持续降级；第二条关联同一实例窗口内的两种通道故障。
      - alert: RetrievalChannelDegrading
        expr: rate(agr_degradation_total{component=~"vector|graph|fulltext"}[10m]) > 0.05
        for: 10m
        labels: {severity: medium}
      - alert: RetrievalChannelsDown
        expr: |
          sum by (job, instance) (increase(agr_degradation_total{component="vector",reason=~".*unavailable"}[15m])) > 0
          and on (job, instance)
          sum by (job, instance) (increase(agr_degradation_total{component="graph",reason=~".*unavailable"}[15m])) > 0
        labels: {severity: high}
      - alert: BudgetTripsSpike
        expr: increase(agr_budget_trips_total[10m]) > 10
        labels: {severity: medium}
```

`RetrievalChannelsDown` 表示同一 `job` / `instance` 在过去 15 分钟内，vector 和 graph 都出现过 `unavailable` 事件；两次故障可能来自不同查询，也可能已恢复。故障增量离开窗口后告警解除，历史累计值不会令它永久触发。它不等于某次查询触发了 `low_confidence`；精确判断应读取查询响应。当前未导出 `agr_low_confidence_total`，如需按实际低置信度查询告警，应另增该计数后使用 `increase(agr_low_confidence_total[15m]) > 0`。

`RetrievalChannelDegrading` 的 `0.05/s` 是每个 `component` / `reason` 序列约每 10 分钟超过 30 次降级，并要求持续 10 分钟，适用于持续故障。低流量部署若希望少量失败即报警，可将表达式改为 `increase(agr_degradation_total{component=~"vector|graph|fulltext"}[10m]) > 0`，并按响应要求缩短或移除 `for`。这些阈值需要结合实际流量调整。

OTel：`./scripts/setup.sh --extras dev,otel` + `AGR_OTEL_ENABLED=1 AGR_OTEL_ENDPOINT=<collector>`；
进程内 trace span 桥接为 OTel span 并重键为公开 `query_id`；入向解析 W3C `traceparent`，
出向可用 `inject_trace_context()` 注入。无 SDK 时优雅退化为进程内 trace。
span 创建、进入和退出失败均不影响业务结果；业务异常保持原类型，即使 span 清理同时失败。
**collector / Jaeger / Tempo 实际导出与告警规则部署仍待环境验证。**

## 安全检查清单（P4-REL-02，2026-07-25 更新）

- [ ] 无硬编码密钥；生产 `AGR_REQUIRE_AUTH=1` 且 key 带角色段（reader 缺省）
- [ ] Cypher 参数化（Neo4j 适配器）
- [ ] 错误 envelope 不泄露内部路径 / 栈（栈只入服务端 JSON 日志）
- [x] 上传治理已在应用层兜底：单文件默认 ≤50 MiB（分片读取，超限立即中断）、单批默认 ≤100（均可配置）；扩展名 + 签名校验、编码检测、可选 MinerU（§6.1）；类型 415、解码 422、超限 413
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

## 摄取索引恢复、版本替换与容量（2026-11-03）

文档 UUID 在版本间稳定；同租户相同内容返回 `docs[].status=duplicate`，同 `source_uri`
新内容返回相同 UUID 与递增 `version`。普通上传的 source 为 `upload://<filename>`；
CLI 为绝对 `file://` URI；需要自定义 source 的客户端使用 resumable init。
替换首先清理旧版本索引，因此普通内容替换期间该文档暂时不可检索。

任务 `done` 表示全部必需阶段完成；`partial` 附每阶段 chunk 计数和脱敏错误。
`failed` 中的 401 先修复 embedding 凭据，再显式 reconcile，避免重复认证请求。
worker 在每批向量和每个图抽取后仅更新对应 chunk 行，不再逐 chunk 重写正文和全部 embedding。
已有 SQLite 文档首次打开时自动迁移到独立 chunk 表；升级前备份 `documents.sqlite3`，
升级后不要混用旧版 writer。进程中断的 `extracting` 任务在租约过期后重投；
持久 adapter 的成功阶段可直接跳过。内存 adapter 重建或清空后，reconcile 会恢复已保存的向量、
重建 fulltext 并重新抽取图，完成后才更新 checkpoint；已完成文档也会检查内存索引代次。
恢复已有向量不调用 embedding 服务。服务与 worker 必须共享 runtime
目录；live profile 同时共享 Neo4j、Qdrant 和 BM25 snapshot。SQLite/文件锁适用于同机或支持
可靠 POSIX 锁的共享卷；offline 的图和向量仍是进程本地，使用 API inline worker。

```bash
# 读取同一配置、同一 runtime 目录；租户参数必须显式提供。
python -m agentic_graphrag reconcile --tenant acme
python -m agentic_graphrag reindex --tenant acme
python -m agentic_graphrag.knowledge.ingest_worker --once
```

reconcile 同时恢复失败的版本替换/删除意图。DELETE 返回 503 `Delete pending reconciliation`
时不要当成成功：修复存储后执行 reconcile，意图完成后源文档和 chunk journal 才删除。
`DELETE /v1/documents/{UUID}` 仅 admin/operator 可用；跨租户与不存在均为 404，成功写审计。
其它文档支持的图关系会保留，但删去已删除文档的 provenance。
审核写入携带文档版本，并在文档锁内校验：来源已删除、已替换或尚在清理时，决策记录
`execution.effect=stale_document`，不修改图。旧审核优先从任务记录读取版本；无版本时仅允许
未替换的第一版文档。任务终态使用当前队列的 pending 数量；共享 JSONL 的 API/worker 会刷新
其它进程的决策，已处理的审核不会令修复后的任务再次卡在 `review`。

Open 实体消歧有多个候选时，批准请求需携带 `keep_id`，其值必须来自审核项的
`payload.candidate_ids`，且不能等于 `drop_id`。例如向
`POST /v1/review-queue/{id}/decision?kb_id={kb}` 提交
`{"decision":"approve","keep_id":"候选实体 ID"}`。未选择或选择无效候选返回 422，审核项
仍为 pending；有效目标与决策一同持久化，执行重试沿用该目标。

模型升级：先验证新模型服务，再让维护进程使用目标 `llm.embedding_model` 运行 reindex；
查询会读取当前 collection 的 embedding_model 来选择 encoder；alias 在 embedding 请求期间变化时重新编码，
避免配置更新或在途请求使用错误的向量维度。上传 worker 配置应与发布后的模型一致。Qdrant 会在租户独立的 collection
中构建新模型（允许不同维度），全部成功才切换 alias，然后删除旧空间内该租户的向量；
发布前失败时旧 alias 不变；发布后的清理失败保留新 alias，重试可完成清理。
修复后再次运行可复用已完成的 chunk。内存 adapter 使用原子 upsert。
升级会保存新的 `embedding_model`；reconcile 不自动触发模型升级，必须运行 reindex。

### 旧 filename / sha1(path) 身份迁移

1. 暂停该租户上传、worker 和待复核写入；备份 doc 目录、tasks/review JSONL、BM25、Qdrant、Neo4j。
2. 新 FileDocStore 首次打开自动将旧根目录及 `v2/*.json` 导入 `documents.sqlite3`。
   后续读取使用 SQLite；请勿再用旧版本程序写 JSON。原 JSON 留作迁移备份，不再是事实来源。
3. 运行 `python -m agentic_graphrag migrate-doc-ids --tenant acme > migration-acme.json`。
   输出 old_id → UUID 映射；迁移保留正文，不改变 parser/chunk text，重建索引前按旧 doc_id
   清理三种索引，并修正持久任务引用。清理失败保留意图；重复运行可恢复且不会再次换 UUID。
4. 核查 partial/failed、抽样检索与图来源；处理迁移前的 pending review，确认其旧文档引用
   不会被直接批准写回，然后恢复 worker/上传。保留映射用于历史审计；备份按既定保留策略清理。

### 容量与断点续传

`configs/default.yaml` 的 `indexing` 配置：默认普通文件 50 MiB、100 files/batch，
resumable 单会话 500 MiB，part 5 MiB，过期 86400 秒。`embedding_batch_size=32`、
`embedding_max_attempts=4`、`retry_base_seconds=0.5`、`retry_max_seconds=30` 可调。
同时调整反向代理的请求体上限；应用按片读取，但代理负责网络请求体的整体限制。

`POST /v1/docs` 先校验并解析整批文件，全部通过后才接受；任一文件被拒绝时整批不生效，
已有文档和索引保持不变。解析结果在接受前保留在内存中，峰值约等于整批大小；大批量导入请
调低 `indexing.max_batch_files` / `max_file_bytes` 或改用下面的 resumable 上传。

1. `POST /v1/uploads/init`，JSON：`{"filename":"report.txt","size":12345,"source_uri":"...","content_sha256":"..."}`。
   source/hash 可省略；返回 `id`、`part_bytes`、`parts`、`expires_at`。
2. `PUT /v1/uploads/{id}/parts/{number}`，原始二进制请求体，part 从 0 开始；除末片外长度
   必须等于返回的 part_bytes。可乱序重传相同内容；同 part 不同内容返回 409。
3. `GET /v1/uploads/{id}` 获取 received 列表，进程重启后仍可续传。
4. `POST /v1/uploads/{id}/complete` 校验总量、全部 parts 与可选 SHA-256，再用与直接上传相同的
   解析器解析（在会话锁之外进行），生成摄取任务；解析失败返回 415/422/503，可修正后重试。
   重复 complete 返回同一任务；跨租户任何操作均为 404。支持类型见 §6.1；MinerU 未启用时，
   PDF 等 MinerU 专属类型在 `init` 时即返回 415。

定期运行 `python -m agentic_graphrag.cli.ingest_maintenance prune-uploads --tenant acme`
清理过期会话。完成后立即删除 part 内容；任务与文档保留各自 journal。
