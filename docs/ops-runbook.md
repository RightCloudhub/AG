# 运维手册（P4-REL-04）

## 服务启动

```bash
source .venv/bin/activate
# 离线默认（seed + MockLLM）
agr-api
# 生产向：live stores + live LLM
# AGR_LIVE_STORES=1 AGR_ALLOW_LLM=1 agr-api
# 或
uvicorn agentic_graphrag.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

试用界面：<http://localhost:8000/web>  
健康检查：`GET /healthz` 返回 `graph_backend` / `vector_backend` / `allow_llm`（避免「假 live」）。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` | 聊天 LLM |
| `AGR_ALLOW_LLM=1` | API 启用真实 LLM |
| `AGR_LIVE_STORES=1` | API 使用 Neo4j + Qdrant（`create_live_bundle`） |
| `AGR_LIVE_FAIL_CLOSED=1` | live 图不可达时失败，不静默 memory |
| `AGR_LIVE_GRAPH_FALLBACK=1` | 允许 Neo4j 失败回落 memory graph |
| `AGR_REQUIRE_AUTH=1` | 强制 API Key |
| `AGR_API_KEYS=tenant:key,...` | 租户密钥 |
| `AGR_CANARY_TENANTS=t1,t2` | 灰度租户 allowlist（metrics/principal.canary） |
| `AGR_DEGRADE_TO_FAST_PATH=1` | 降级偏好分诊 Fast Path |
| `AGR_RATE_LIMIT_QPS` | 租户 QPS（默认 20） |
| `AGR_RATE_LIMIT_CONCURRENT` | 并发查询上限 |
| `NEO4J_*` / `QDRANT_*` | 外部存储 |

## 常见故障

### 1. 查询全失败 / 500

- 查 `/healthz`
- 确认 seed 三元组存在：`data/processed/seed_triples.jsonl`
- 日志中 `type` 字段在 API error details（不向终端用户暴露栈）

### 2. 成本熔断（429 BUDGET_EXCEEDED）

- 单查询：`max_hops` / `MAX_TOKENS_PER_QUERY` / `MAX_LLM_CALLS`
- 租户/用户：`MultiLevelBudget` 日窗口；调大 limits 或等窗口重置
- 指标：`GET /v1/metrics` 的 `budget_trips`

### 3. 图谱回滚

```bash
# 清空后重灌 seed
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm --memory-graph
# Neo4j：
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm
```

增量冲突入审核队列：`data/processed/review_queue.jsonl`，API：`/v1/review-queue`。

### 4. 缓存清理

图谱/索引更新后调用 `RetrievalCache.on_index_update()`（代码侧）或重启进程（版本键失效）。  
磁盘：`data/cache/`。

### 5. 审计回查

```bash
curl -s localhost:8000/v1/audit/queries/<query_id>
```

链路落库：`data/processed/audit_chains.jsonl`。

## 监控告警建议（P4-REL-03）

| 信号 | 阈值建议 |
|------|----------|
| 延迟 P95 | Agentic > 8s / Fast Path > 3s |
| 错误率 | 5xx > 1% |
| 日成本 | 按租户预算 80% 告警 |
| 熔断次数 | `budget_trips` 突增 |

指标：

- `GET /v1/metrics` — JSON（含 `latency_by_route`、`recent_error_rate`）
- `GET /v1/metrics/prometheus` — Prometheus 文本
- 示例规则：[`docs/alerts.example.yml`](./alerts.example.yml)

压测脚手架：

```bash
PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py --n 30
PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py --target http://127.0.0.1:8000 --concurrency 5 --n 50
```

## 验收门禁（工程）

```bash
./scripts/acceptance_gate.sh
./scripts/acceptance_gate.sh --with-llm --with-neo4j --with-qdrant --with-load
# → reports/ACCEPTANCE_STATUS.json
```

审计抽样（AC-3 / P4-AC-02）：

```bash
PYTHONPATH=src .venv/bin/python scripts/sample_audit.py --n 20
```

## 灰度（P4-OPS-01）

1. 内部租户：`AGR_CANARY_TENANTS=internal` + 对应 API key  
2. 试点 5–10 用户：扩 allowlist  
3. 试点全组：去掉 canary 限制或全员 key  
4. 收集反馈 ≥2 周（流程项，非代码）

## 安全检查清单（P4-REL-02）

- [ ] 无硬编码密钥；生产 `AGR_REQUIRE_AUTH=1`
- [ ] Cypher 参数化（Neo4j 适配器）
- [ ] 错误 envelope 不泄露内部路径
- [ ] 上传文档大小限制（生产应在反向代理加）
- [ ] 租户隔离：审计/反馈 AuthZ + cache tenant 前缀已落地；图库按 tenant 分库/标签仍为运维项
- [ ] LLM 连续失败 circuit（provider 内建）+ 预算/限流 429
