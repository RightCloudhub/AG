# CI/CD 与可观测性

**覆盖需求**：NFR-08、FR-OP-01/02、P2-ARCH-04、P3-OP-*

## 1. CI 流水线（自 MVP 起）

```
PR 提交 → lint/format → 类型检查 → 单元测试 → 集成测试(容器) → 覆盖率门禁(≥80%)
       → [Prompt/配置变更时] 评测集 dev 回归 → 人工评审 → 合入
```

- 提交信息遵循 conventional commits（feat/fix/refactor/docs/test/chore/perf/ci）。
- 集成测试用 testcontainers 拉起 Neo4j/向量库/ES，CI 内自包含。
- **评测回归触发规则**：`configs/prompts/`、`configs/default.yaml`（分诊/护栏参数）、`agent/`、`retrieval/fusion.py` 变更，以及核心依赖升级（langgraph 等，版本锁定见 ADR-005/R11）时，必须附 dev 集评测报告链接；指标回退 >2pp 阻塞合入。
- 夜间任务：dev 集全量评测 + 趋势图，防隐性回退。

## 2. CD 与环境

| 环境 | 用途 | 部署方式 |
|---|---|---|
| dev | 日常联调 | 合入主干自动部署 |
| staging | 评测与压测、增量更新演练 | 手动触发，数据为试点语料副本 |
| prod | 试点灰度（阶段四起） | 手动审批 + 灰度发布 |

- 发布物：容器镜像（版本=git tag）；配置与 Prompt 随镜像版本固化，运行时仅密钥来自环境。
- 灰度：按租户/用户名单放量（P4-OPS-01）；回滚 = 切回上一镜像 + 图谱快照恢复预案（phase-4 回滚预案）。

## 3. 可观测性

> **实现状态（2026-07-25，ENT-01/02/08 落地）：** 本节原为设计稿；除标注外均已有工程实现。
> 状态权威与剩余待办见 [`docs/ENTERPRISE_READINESS.md`](../../docs/ENTERPRISE_READINESS.md) §3.5。

### 3.1 全链路 Trace（NFR-08）— 🟢 工程实现（collector 联调待部署验证）
- OpenTelemetry：`query_id` 为根 span，子 span 覆盖 分诊→每跳（Planner/Executor/Critic）→每次工具调用→每次 LLM 调用。
- LLM span 属性：模型档位、token in/out、成本、重试次数。
- 检索 span 属性：工具类型、候选数、剪枝丢弃数。
- **实现：** 进程内 tracer（`observability/trace.py`）+ 可选 OTel 桥接（`otel_bridge.py`，`otel` extra + `AGR_OTEL_*`），trace 重键为公开 `query_id`，入向 W3C attach / 出向 inject；span 集合以现有 trace 埋点为准，上列属性明细未逐项对齐（差距随部署联调收口）。

### 3.2 指标（FR-OP-01）— 🟡 部分实现

| 类别 | 指标 |
|---|---|
| 查询 | QPS、延迟 P50/P95（分 fast_path/agentic）、错误率（分错误码） |
| Agent | 跳数分布、循环触顶率、兜底率（no_answer/partial 占比）、Fast Path 升级率 |
| 成本 | 单查询 token/成本分布、日成本累计、熔断触发次数 |
| 检索 | 各路召回耗时、缓存命中率、图查询慢查询数 |
| 知识 | 抽取任务积压、审核队列长度与时效、入图三元组数/日 |
| 反馈 | 负反馈率（试点起） |

> **实现：** `MetricsRegistry`（查询计数、P50/95/99、路由分布、错误码计数、budget_trips）+
> `GET /v1/metrics`（admin）+ `GET /metrics-prom` Prometheus 文本（ENT-08）。**未覆盖：**
> 检索各路耗时/缓存命中率、知识类指标（抽取积压/审核时效仅 healthz `review_queue_pending`）、
> per-tenant 聚合（ENT-04 待办）。

### 3.3 告警（P4-REL-03）— 🟡 规则示例已入 runbook，部署落地待办

| 告警 | 阈值（初始） | 级别 |
|---|---|---|
| Agentic P95 延迟 | > 8s 持续 10min | 高 |
| 错误率 | > 5% 持续 5min | 高 |
| 日成本 | > 预算 80% / 100% | 中 / 高 |
| 熔断触发 | 单用户高频触发 | 中（疑似滥用或分诊失效） |
| 兜底率 | 突增 >2 倍基线 | 中（疑似图谱/检索故障） |
| 审核队列 | 积压 > 阈值 | 低 |

> **实现：** 前四项对应的 Prometheus rules YAML 示例已入 `docs/ops-runbook.md`（2026-07-25）；
> 兜底率/审核队列告警需先补对应指标外送。实际告警部署与验证归部署侧（G4 门禁第 3 条）。

### 3.4 日志 — 🟢 工程实现（ENT-01/06）
- 结构化 JSON 日志，全部携带 `query_id`/`tenant_id`；服务端记录详细错误上下文，面向用户的错误消息保持友好且不泄露内部细节。
- 推理链落库（FR-AN-04）即审计日志，保留策略与合规要求对齐（评审确认保留期）。
- **实现：** `observability/logging_setup.py`（JSONFormatter + contextvars：request_id/query_id/tenant_id/user_id；`AGR_LOG_LEVEL/FILE` + 轮转）；PII 脱敏钩子 `redaction.py`（默认关）；保留期入 `configs/default.yaml` `retention:`（90/90/30/30 天，`scripts/prune_data_files.py` 清理）——保留期数值仍待评审确认，当前为工程默认值。
