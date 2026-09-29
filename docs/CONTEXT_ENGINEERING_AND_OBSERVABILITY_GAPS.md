# 上下文工程与可观测性差距

**评估日期：** 2026-09-23  
**对象：** AgenticGraphRAG 当前代码与仓库运行文档  
**目的：** 基于现有实现识别上下文构造和运行观测中的产品化盲区，并给出改进顺序；不是说当前系统没有记忆、日志或指标。

## 摘要

项目已有按查询共享的 Agent Memory、子问题 DAG、证据引用、LLM token 预算、结构化日志、query metrics、内部 trace、审计链及可选 OTel/Prometheus。主要短板是：

1. **上下文工程：** 已存下来的上下文不等于已被有效管理；当前压缩主要依赖条目数和字符截断，缺少统一的 token 预算分配、相关性/多样性选择、来源新鲜度策略和上下文快照。
2. **可观测性：** 已有 query 级观测，但追踪颗粒度偏粗、状态多为进程内存、LLM/检索/摄取各阶段信号不完整；生产 Collector、告警和跨副本聚合仍需验证。
3. **数据安全：** 查询文本预览会进入 OTel span 属性；脱敏默认关闭，现有脱敏接线主要面向日志和审计，OTel 属性需有独立的数据最小化策略。

## 1. 上下文工程

### 已具备的基础

- [`agent/memory.py`](../src/agentic_graphrag/agent/memory.py) 维护单次 Agent 查询内的证据、已探索路径/子问题、排除假设和结论，并可快照到 LangGraph state/checkpointer。
- Planner 接收问题与 `memory_summary`；Executor 接收子问题和实体提示；Critic 与 Answer 根据证据构造不同 prompt。答案 claim 需绑定 evidence id。
- 调用预算、跳数、子问题宽度、超时等硬限制已存在；上下文窗口不是完全无上限。

### 主要不足

| 缺口 | 当前实现与影响 | 建议 |
|---|---|---|
| **上下文选择较粗** | `MemoryState.summary()` 默认取插入顺序前 10 条证据，每条只保留前 120 个字符；它不是按当前子问题相关度、证据权威性、时效或来源多样性挑选。Planner 因而可能看不到较晚但更关键的证据。见 [`memory.py`](../src/agentic_graphrag/agent/memory.py)。 | 加一层统一 `ContextAssembler`：按任务类型筛选证据，做相关性与来源去重/多样性排序，并说明每条证据为何入选或被丢弃。保留稳定证据 ID 与来源链。 |
| **字符裁剪不等于 token 预算** | Planner memory summary 有条数/字符上限；Critic 限证据条数与字符数；Answer 每条证据裁到 500 字符但会遍历传入 evidence 列表。当前尚无按模型上下文窗口、任务阶段和预留输出 token 统一计算的预算分配。见 [`critic.py`](../src/agentic_graphrag/agent/critic.py)、[`answer.py`](../src/agentic_graphrag/generation/answer.py)。 | 按 Planner/Executor/Critic/Answer 分别配置 context token budget；先预留 system 指令和输出空间，再按重要性截取；统计“可用/实际/丢弃”token。超限时应确定性压缩或缩小候选，而非任意截断。 |
| **记忆摘要偏目录，不是状态综合** | `memory_summary` 主要列计数、前若干证据片段、结论和排除项；没有明确的“已确认事实、未解决问题、冲突、缺失证据、下一步计划”摘要结构。Planner 的输入也没有完整的 corpus profile 或证据覆盖说明。见 [`memory.py`](../src/agentic_graphrag/agent/memory.py)、[`planner.py`](../src/agentic_graphrag/agent/planner.py)。 | 将 memory summary 变成带 schema 的短状态：已确认结论及证据 ID、相互冲突的证据、已完成/未完成子问题、缺口和剩余预算；让 planner 只引用 ID，不重复复制长文本。 |
| **来源时间和版本上下文不足** | 生成 prompt 使用候选的 id/type/score/content；现有上下文选择没有统一的文档版本、更新时间、有效期和权威等级规则。遇到冲突事实或过期文档时，模型不一定能按业务规则优先。 | 为候选统一携带 `doc_id/version/updated_at/valid_time/source_span/tenant` 等来源属性；按问题决定“最新优先”还是“历史时点查询”，冲突显式呈现并触发澄清/拒答。 |
| **上下文安全边界不够显式** | Evidence 与用户问题作为普通 prompt 字段传入。系统要求 grounded answer，但 prompt 模板没有统一的检索材料不可信边界和注入内容处理策略。 | 把检索内容明确标为不可信数据，规定只抽取事实、不执行文档中的指令；加入文档提示注入、恶意引用、跨租户内容混入的离线回归集。 |
| **跨步骤可复现性不足** | ReasoningChain 保存证据目录和部分运行 metadata，但没有统一的 context snapshot 契约，记录每次模型调用实际使用的证据 ID、裁剪原因、token 估算、prompt/schema 版本和检索配置版本。 | 保存轻量可复现快照（内容可脱敏或哈希，仅保留必要 evidence ID/排序/截断标志/版本），可回答“模型当时看到了什么”，避免为调试永久存储完整敏感 prompt。 |

### 建议上下文流水线

```text
身份/租户与会话状态
  → 意图及任务计划
  → 受权限约束的多路检索
  → 证据规范化、去重、冲突/时效标注
  → 按阶段 token 预算选择/压缩
  → 带来源 ID 的提示词组装
  → claim-evidence 校验
  → 保存脱敏 context snapshot 与决策原因
```

Context state、持久化 memory、模型提示输入应作为不同对象管理；LangGraph 官方的 state/node/checkpoint 设计说明可作为状态边界参考，但项目仍应保持自己的检索和存储协议。见[官方 LangGraph 指南](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)。

## 2. 可观测性

### 已具备的基础

- [`observability/trace.py`](../src/agentic_graphrag/observability/trace.py) 提供 query 关联的 span 结构；管理员 API 可按 query id 回查 trace。
- [`observability/metrics.py`](../src/agentic_graphrag/observability/metrics.py) 汇总 query 数、P50/P95/P99、route、错误、hop 和预算触发；指标含 query token/cost 等字段。
- 结构化日志、安全事件审计、PII 脱敏开关、Prometheus exposition 和可选 OTel/W3C trace context 已有代码与运维说明。

### 主要不足

| 缺口 | 当前实现与影响 | 建议 |
|---|---|---|
| **Trace 粒度停留在请求级** | 当前业务代码创建的 OTel span 主要是 `run_query` / `stream_query` 两个外层 span；Planner、每次 LLM 调用、向量/BM25/图检索、RRF、Answer/citation gate、缓存及摄取步骤没有成体系的父子 span。ReasoningChain 是业务推理记录，不等于可跨服务关联的分布式 trace。 | 建立统一 trace contract：API → triage → planner → tool/retriever → fusion/rerank → LLM → citation validation；摄取则覆盖 parse/chunk/extract/embed/index/review。记录耗时、状态、重试和有限的计数/版本属性。 |
| **Trace/metrics 以内存进程为边界** | Trace registry 默认最多保留 2,000 条；metrics 的近期 event 列表最多 5,000 条，但 `_latency`/`_hops` 样本列表会持续 append，长期运行时既有内存增长风险，汇总 percentile 也覆盖进程启动以来所有样本。服务重启会丢失，多个 worker 的 admin 查询和本地汇总不天然共享。见 [`trace.py`](../src/agentic_graphrag/observability/trace.py)、[`metrics.py`](../src/agentic_graphrag/observability/metrics.py)。 | 生产环境以外部 OTLP/Prometheus 后端作为长期查询与跨副本汇总面；进程内 registry 只保留开发/快速排障用途。若保留本地 percentile，使用有界 reservoir/histogram；明确保留期、采样、丢弃行为。 |
| **LLM 与检索阶段指标不足** | Query metric 有总 token/cost/call 数，但缺少按模型/操作/阶段拆分的首 token/总延迟、输入输出 token、重试、限流、错误类型、embedding 用量、cache hit 和各检索通道耗时/命中数。 | 补齐低基数 stage/model 指标和 span 属性；把检索质量指标（候选数、融合后证据数、引用支持/拒答反馈）与延迟/成本并列看，便于判断慢在哪里、质量为何下降。 |
| **摄取过程几乎不在统一遥测内** | 当前 query metrics 不覆盖解析失败率、队列等待、重试/隔离区、抽取置信度、embedding/index 时长、索引新鲜度和人工复核积压变化。 | 为 ingest worker 定义任务级指标和分布式 trace；关联 `task_id/doc_id/version`，但避免把原文或敏感元数据作为高基数标签。 |
| **内容脱敏与 OTel 脱节** | Query span 在 [`service_agent.py`](../src/agentic_graphrag/api/service_agent.py) / [`service_stream.py`](../src/agentic_graphrag/api/service_stream.py) 将问题前 120 字符写入 `question` 属性；Redactor 默认关闭且当前脱敏接线主要面向日志/审计，未挂入 `otel_span` 属性处理。原始问题可能进入远端 Collector。 | 默认仅发 allowlist 属性、分类标签和内容哈希；将 OTel attribute 纳入同一脱敏/拒绝策略。确需内容采样时，必须显式开关、权限隔离、短保留期和脱敏审查。 |
| **生产告警与 Collector 尚未闭环** | 仓库运维文档记录 OTLP Collector/Jaeger/Tempo 实际导出及告警规则部署仍待环境验证；有告警 YAML 示例不代表线上已告警。见 [`ops-runbook.md`](./ops-runbook.md)、[`ENTERPRISE_READINESS.md`](./ENTERPRISE_READINESS.md)。 | 用目标部署环境做端到端演练：从 API 请求到 Collector 到查询 UI；再验证断连、队列阻塞、LLM 失败、P95 超标、预算耗尽等告警是否触发、去重、带 runbook 并能恢复。 |
| **GenAI 遥测字段缺少统一版本约定** | 自定义 `agr.*` 属性和 query metrics 可用于本项目，但跨 provider/后端比较及可视化需要统一模型调用名称、token、错误与 operation 语义；官方 GenAI conventions 仓库当前仍将 Schema URL 标为 TODO。 | 评估采用 OpenTelemetry GenAI semantic conventions；落地时锁定具体 revision/schema，核对属性稳定性，控制属性基数与敏感数据，而不是无版本地照搬。见[官方 GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)。 |

## 优先实施与验收

### P0：先让每次回答可解释、可复现

- Context snapshot 记录 prompt/schema 版本、证据 ID 与排序、每阶段 token 估算、截断/排除原因、模型和检索配置版本；敏感正文默认不落 telemetry。
- 加入长上下文、冲突来源、旧版本、权限过滤、注入文本、无证据和预算耗尽测试。
- 对慢/错案例能从 query id 还原阶段时长和决策，但不能依赖保存在某一 API 进程里的内存对象。

### P1：补全执行链 Trace 和业务信号

- 所有 LLM、retriever、fusion、cache、citation gate、ingest step 都有父子 span，贯穿 `request_id/query_id/task_id/trace_id`。
- 外部后端可按服务、模型、阶段和状态聚合 P50/P95/P99、错误率、token/cost、缓存命中和 ingest 队列延迟。
- 明确高基数维度不进入 Prometheus label；tenant/user 仅用于受控日志或受限查询，不暴露为公开 label。

### P1：脱敏和生产验证

- OTel、日志、审计三条输出使用统一的数据分类与字段 allowlist；默认不发送问题全文、检索原文、prompt 和 API 密钥。
- 在真实 Collector 和告警系统上验证 traceparent 传播、采样、重启后查询、跨 worker 聚合、保留期和告警恢复。
- 将 OpenTelemetry GenAI 语义 convention 的版本/状态写入实现记录；确保 exporter 失败不影响主请求，但 exporter 失败率可被监控。

## 资料与边界

- 代码现状依据本文链接的模块与 [`ARCHITECTURE.md`](./ARCHITECTURE.md)、[`ops-runbook.md`](./ops-runbook.md)、[`ENTERPRISE_READINESS.md`](./ENTERPRISE_READINESS.md)。生产 Collector 验证状态沿用仓库当前记录，部署完成后应更新验证日期与结果。
- 外部设计参考为 LangGraph 官方 [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph) 和 OpenTelemetry 官方 [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)，查阅日期 **2026-09-23**。GenAI conventions 的版本与稳定性应在落地时复核。
- 本文所称“上下文工程不足”指上下文选择、预算、来源、状态和复现策略不够系统化，不代表项目没有 memory 或 evidence；“可观测性不足”指从代码实现到生产运营闭环尚有距离，不代表没有日志、指标或 trace 代码。
