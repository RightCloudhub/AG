# 架构优化 V2：当前实现与后续设计

O-1…O-8 已交付；剩余架构方向见 §5，真实模型效果与部署验收仍需独立验证。
原始外部评估、逐项核对、旧基线与第一批 A/B 结果见
[CHANGELOG.md](../../CHANGELOG.md#arch-v2-original-review)。
关联文档：[REFACTOR_PLAN.md](REFACTOR_PLAN.md) · [ARCHITECTURE.md](../ARCHITECTURE.md)。
多智能体编排的目标与进度见 [ARCH_OPTIMIZATION_V3.md](ARCH_OPTIMIZATION_V3.md)。

## 0. 当前结论

评测统计、金标审计、共享 BM25、可靠事件投递、可插拔重排、图到文档溯源、
分层 critic 与熔断已具备实现。离线默认路径保持确定性；真实模型的准确率、费用和
P95 需要同图、同题、同配置的配对验证。

## 1. 当前离线门禁

CI 下限为 POC 30/31（agentic 与 triage）、G2 196/200、probe 52/54 与 19/20；
金标审计 `scripts/audit_gold.py --max-flagged 0` 阻塞合并。
历史基线不能替代当前门禁或 live 效果证据。

## 2. 当前能力边界

- `offline` / `eval` 使用进程内 BM25；`live` / `hybrid` 使用共享文件 BM25，API 和
  worker 必须共享索引目录；跨主机服务化仍属后续工作。
- 图证据携带 `source_chunk_ids`；生成阶段的原文利用与效果需单独评估。
- 重排支持 `none` / `lexical` / `http`，默认 `none`；HTTP 适配器及部署说明见
  [RUNBOOK](../ops/RUNBOOK.md) 与 [rerank_eval.md](../../reports/rerank_eval.md)。
- 分层 critic、专用 NLI 与缓存策略的收益以 live 配对评估为准。

## 3. 当前优先级

已完成项维护其正确性与回归门禁；后续项按 §5 的验收标准立项。
真实检索和模型效果、跨进程/多租户一致性及组件级评测是剩余验证重点。

## 4. 当前实现

O-1…O-8 已实现并有离线单测；重排默认关闭，分层 critic 等可选路径仍需 live 配对验收。

### O-1（E-1）评测统计与配对对比

- `eval/stats.py`：
  - `wilson_interval(k, n)`；
  - `mcnemar(a_correct, b_correct)`：精确二项检验，返回 `b01 / b10 / p_value`；
  - `breakdown(rows, cases, key)`：按 `category`、`metadata.template` 分组统计准确率。
- `scripts/eval_compare.py`：输入两个运行报告 JSONL 和用例文件，输出整体准确率和 CI、分组准确率、配对 McNemar 结果、P50/P95 延迟、LLM 调用均值。
- **合并规则**（写入 `rules.md`）：架构 A/B 必须报告配对 p 值；p < 0.05 且准确率、成本、延迟三者都没有明显退化，才能合并。

### O-2（E-2）金标审计

- `eval/gold_audit.py` 和 `scripts/audit_gold.py`。对每个用例，基于三元组做确定性检查：
  1. `gold_path` 的每条边在图中存在（允许互逆：PARENT_OF ↔ SUBSIDIARY_OF，COMPETES_WITH 视为对称）。不存在则报 `path_edge_missing`；只有反向存在则报 `path_direction`。
  2. 从锚点沿 `relation_chain` 遍历，得到全部合法答案集合。若集合大小大于 1 而金标只给了 1 个，报 `ambiguous_gold`。
  3. 金标答案不在合法答案集合中，报 `gold_unreachable`。
- 输出 JSON 报告，并按问题类型计数。**不自动修改数据集**；修正走人工复核。CI 使用 `--max-flagged 0` 阻塞合并。

### O-3（S-1）全文检索：修复租户泄漏 + 跨进程共享

- `BM25FulltextStore.save/load` 写入和读取 `tenant_id`。读到旧格式（没有该字段）时保持 `""`，与当前行为一致，并记录一条警告。
- `SharedFileBM25Store(path)`：
  - `index()` 之后原子写入（临时文件 + `os.replace`）；
  - `search()` 之前检查文件的 `mtime_ns`，有变化就重新加载。
  - 这样 API 和 worker 只要挂同一个卷，就能看到彼此的写入。
- 工厂：Profile 为 `live` / `hybrid` 时使用 `SharedFileBM25Store(indexes_dir/bm25.json)`，`offline` / `eval` 仍用进程内实现。能力矩阵把 fulltext 标为 `shared-file`。
- **说明**：这是在现有部署形态下的正确性修复。多副本或跨主机部署时，应改用服务端实现（Qdrant 稀疏向量或 OpenSearch），列为 S-2，见 §5。

### O-4（O-1）事件可靠投递

- `InProcessEventBus.subscribe(event_type, handler, *, critical=False, retries=0, on_failure=None)`：
  - 普通订阅者：行为不变；
  - critical 订阅者：失败后按 `retries` 重试；仍失败则调用 `on_failure(event, exc)`，计数加一（`handler_failures[name]`），并通过 `observability.metrics` 记 `agr_event_handler_failures_total{event,handler}`。
- `write_path`：`invalidate` 注册为 critical，`retries=2`，`on_failure` 设为**清空检索缓存和答案缓存**（fail-safe：宁可缓存全部失效，也不返回旧答案）。

### O-5（R-1）两阶段召回 + 可插拔重排

- 配置 `retrieval.rerank`：
  ```yaml
  rerank:
    kind: none        # none | lexical | http
    candidate_pool: 30
    top_n: 10
    endpoint: ""      # http: TEI / Infinity 兼容的 POST /rerank
    timeout_seconds: 2.0
  ```
- `retrieval/rerankers.py`：
  - `LexicalReranker`：离线可用。按“问题 token 与候选内容的重叠 + 图候选中命中实体加分”重排，同分时保持 RRF 顺序。
  - `HttpCrossEncoderReranker`：调用 `{"query", "texts"}`，读取返回的 `[{"index", "score"}]`。超时或出错时退回输入顺序，并在候选 metadata 中标 `rerank_fallback`。
  - `build_reranker(cfg)` 工厂函数。
- `app/wiring.py` 把 reranker 注入 `Executor`（构造参数已存在：`agent/executor.py:79/107`）。`kind != none` 时，各路召回的 top_k 提升到 `candidate_pool`。
- 默认 `none`，行为不变。

### O-6（K-1）边到 chunk 溯源透出

- `retrieval/graph.py` 的邻居候选、`graph_paths.py` 的路径候选：从 `RelationRecord.sources` 里取 `doc_id`、`chunk_id`，加入 `Citation`，并写入 `structured["source_chunk_ids"]`。
- 生成阶段的引用检查和 NLI 可以凭这些 id 回查原文（后续接入，列为 K-2）。

### O-7（A-1）分层 critic

- `guardrails.critic_mode: llm | tiered | offline`，默认 `llm`，与现状一致。
- `tiered`：先运行 `offline_critique`。若结果是“确定性结论”，就不调 LLM；否则再调 LIGHT 模型。确定性结论指：
  - `ANSWER` 或 `NEXT_HOP`，且能抽取出实体结论；
  - 或者证据为空，直接给出 NEXT_HOP / GIVE_UP。
- 记录 `critic_tier` 到 trace 和 metadata，以便统计 LLM 调用节省比例。用单测中的计数 LLM 验证。

### O-8（L-1）熔断滑窗 + 档位降级

- **熔断器按路由拆分**：`chat:strong`、`chat:light`、`embed` 各用一个熔断器（`CircuitBreakerSet`），这是降级的前提。
- `CircuitBreaker` 新增 `window_seconds` 和 `min_calls`：在时间窗口内，失败率达到 `failure_rate` 且调用数不少于 `min_calls` 时熔断；原有“连续 N 次失败”的规则保留，作为兜底。
- `OpenAICompatibleProvider`：STRONG 档熔断时，若配置 `llm.fallback_to_light: true`，改用 LIGHT 模型完成调用，并在响应 metadata 中标 `degraded_from=strong`。默认关闭。
- 熔断器按端点进程级共享，半开只放一个探测，见 V3 §3。

---

## 5. 后续批次（只做设计，不在本批实现）

| ID | 项 | 设计要点 | 验收 |
|---|---|---|---|
| E-3 | 评测扩容到 ≥300 | 先完成 E-2 审计和修正；新增模板覆盖 inverse / intersection / qualifier / missing_relation / false_premise（见《评测集扩充提示词》） | 每个子类型 ≥30 条，heldout 不参与调优 |
| E-4 | 分模块评测 | 路由 acc、检索 recall@k / MRR（以 gold_evidence 为准）、抽取 P/R、消歧 pairwise F1、NLI acc、置信度 ECE | 每项有基线文件 |
| P-1 | QueryFrame 规划 | 见上一份报告；改进复杂关系链规划 | 以当前基线做配对验证 |
| R-2 | PPR 图检索 | 应用层 PPR，以问题实体为种子；Neo4j 可选 GDS 实现 | 3hop recall ↑，P95 +<100ms |
| R-3 | 查询自适应融合 | `weighted_rrf(weights_by_qtype)` | 各分类不退化 |
| R-4 | 父子分块 | 子块 350 字参与检索，返回父块，引用落到子块 | 证据精度 ↑ |
| R-5 | 语义缓存 | embedding 相似度 ≥0.95 且实体集合相等才命中 | 误命中率 <0.5% |
| S-2 | 全文服务化 | `QdrantSparseFulltextStore`（BM25 / SPLADE） | 多副本一致 |
| S-3 | Outbox + 分路版本 | `IndexVersion` 改为 `(tenant, channel)` 两级版本；写侧用 outbox | 失败可以重放 |
| K-2 | 溯源接入生成 | 图证据带回原文 span，交给 NLI | 引用准确率 ↑ |
| K-3 | 文档级抽取 | 先做实体和指代表，再逐块抽关系 | 实体碎片 ↓ |
| K-4 | blocking 消歧 | 先按名称和类型分桶，桶内聚类，边界样本才交给 LLM | LLM 调用 ↓ |
| G-1 | 专用 NLI | 用 cross-encoder 批量判定，可与重排共用服务 | 延迟 ↓，准确率不降 |
| G-2 | 置信度校准 | isotonic 校准，需要 E-3 的样本量 | ECE ↓ |
| L-2 | 原生 json_schema 输出 | 在 `structured.py` 中按 provider 能力选择 | 重试率 ↓ |
| X-1 | trace_id 贯通 | ReasoningChain、日志、OTel span 共用同一个 id | 可以端到端跳转 |
| X-2 | 租户级 IndexVersion | 与 S-3 合并 | 跨租户不再互相失效 |

---

## 6. 验证方法

- Ruff、format、mypy、代码指标、单测覆盖率 ≥80% 和 §1 离线门禁必须通过。
- 检索、规划、critic 或生成改动使用 `scripts/eval_compare.py` 比较基线与候选，
  报告逐题配对 McNemar、准确率、费用与 P95；真实 LLM 结果单独记录。
- 实验开关为 `AGR_RERANK=none|lexical|http`、`AGR_CRITIC_MODE=llm|tiered|offline`。

## 7. 验证记录入口

第一批提交、旧 CI 下限与 A/B 数字已移至
[CHANGELOG](../../CHANGELOG.md#arch-v2-original-review)。
当前重排验收与剩余限制见 [rerank_eval.md](../../reports/rerank_eval.md)，
其余未完成项见 [IMPORTANT.md](../reviews/IMPORTANT.md) §6。
