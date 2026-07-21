# 阶段三：工程优化（3-4周）

**目标**：性能、成本、质量治理达到上线水位 —— 效果最终达标（AC-1/2）、延迟达标（AC-4）、护栏与预算生效（AC-6）、增量更新可用（AC-5）。
**前提**：G2 通过。

## 周计划

| 周 | 重点 |
|---|---|
| W1 | 复杂度分诊 + 检索并行化 + 融合排序 |
| W2 | 缓存 + 流式响应 + 监控/预算控制 |
| W3 | 增量更新 + 实体消歧 + 质量治理闭环 |
| W4 | 全量回归评测 + 压测 + 护栏专项测试 + G3 评审 |

## 任务清单

### 性能与成本（PERF）
- [x] `P3-PERF-01` 复杂度分诊器：规则 + 轻量 LLM 判定，简单问题走 Fast Path（FR-AG-01）— `agent/triage.py` · `agent/fast_path.py` · `run_query` 入口；评测集 Accuracy 损失验证仍待 held-out
- [x] `P3-PERF-02` 三路检索并行执行（FR-RT-05）— `Executor` ThreadPoolExecutor；单路失败降级
- [x] `P3-PERF-03` RRF 融合排序，预留 Re-ranker 接口（FR-RT-04）— `retrieval/fusion.py` · Executor 默认 rrf
- [x] `P3-PERF-04` 中间结果缓存：embedding / 子问题检索 / 热点答案 + index_version 失效 — `retrieval/cache.py`
- [x] `P3-PERF-05` LLM 分级用模：Planner/Critic=STRONG，Executor/分诊=LIGHT（`Tier` 已接线）
- [x] `P3-PERF-06` SSE 流式响应 — `POST /v1/query/stream` · `api/sse.py` · **真·增量**：`agent/loop_stream.py`（LangGraph `stream(updates)`）+ `api/service_stream.py`
- [x] `P3-PERF-07` 压测脚手架 — `scripts/p3_guardrail_and_load.py` → `reports/p3_perf_guardrails.json`（offline 毫秒级；live P95 仍待生产压测）

### 成本护栏与运营（OP）
- [x] `P3-OP-01` 查询级指标采集 — `observability/metrics.py` · `GET /v1/metrics`
- [x] `P3-OP-02` 预算控制：租户/用户/单查询三级 — `llm/budget_policy.py` · `BUDGET_EXCEEDED`
- [x] `P3-OP-03` 全链路 trace — `observability/trace.py` · query_id 串联
- [x] `P3-OP-04` 护栏专项集 — `scripts/p3_guardrail_and_load.py`；wall-clock timeout 已进 `Guardrails`

### 图谱治理（KG）
- [x] `P3-KG-01` 增量更新 + 冲突检测 — `knowledge/incremental.py`（clear_first=False）
- [x] `P3-KG-02` 实体消歧 — `knowledge/resolution.py`（规则+相似度+LLM 判定钩子）
- [x] `P3-KG-03` 质量治理（最小）— `knowledge/review/queue.py` + API 审核队列（非独立 SPA）
- [x] `P3-KG-04` 知识管理 API — `POST /v1/docs` · `GET /v1/ingest-tasks/{id}` · review-queue

### 可解释性（AN）
- [x] `P3-AN-01` 推理链落库 + 审计 API — `generation/audit_store.py` · `GET /v1/audit/queries/{id}`
- [x] `P3-AN-02` 推理链可视化 — `web/` 步骤/证据展示（为 P4 复用）

### 回归与评审（EV）
- [~] `P3-EV-01` held-out offline 回归脚手架 — `scripts/p3_ev_offline.py` → `reports/g3_offline/heldout_eval.json`；**live AC-1/2 正式关闭仍开**
- [~] `P3-EV-02` 分诊 A/B（triage on vs force agentic）— 同上 `triage_ablation.json`；`run-cases --enable-triage`
- [~] `P3-EV-03` G3 材料脚手架 — `reports/g3_offline/G3_review_scaffold.{json,md}` + 增量 drill；**formal_g3_go=false**

## 交付物

1. 达标的生产候选版本（效果/延迟/成本三达标）
2. 压测报告、护栏专项测试报告、增量更新演练记录
3. 监控看板 + 预算熔断机制
4. G3 评审结论

## 出口标准

见 [roadmap.md](../roadmap.md) G3 门禁。核心：**AC-1/2/4/5/6 全部达标**。
