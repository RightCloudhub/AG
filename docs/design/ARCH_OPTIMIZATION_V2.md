# 架构优化 V2：核对结论与精细化设计

> 状态：**第一批（O-1 ~ O-8）已在 `refactor/arch-v2` 上实现并通过验证**（验证记录见 §7），其余项见 §5。
> 基线：`8942ddb`（Batch 1 + CI 门禁）。
> 输入：外部《架构优化评估》（结论摘要 + §1–§10）。
> 方法：逐条对照代码（标注 `file:line`），并在离线 Profile 下实测 `poc_cases`（31 题）和 `g2_all`（200 题）。
> 关联文档：[REFACTOR_PLAN.md](REFACTOR_PLAN.md) · [ARCHITECTURE.md](../ARCHITECTURE.md) · 前一份《架构对效果的影响评估》。

---

## 0. 结论先行

1. **评估的大方向成立，但有 6 处与代码不符**，会直接影响优先级排序（§2 用 ✗ / ◐ 标出）：
   - 度数大于 30 的节点**没有被丢弃**，只是改走关系类型硬过滤，过滤后没有结果会再无过滤重试（`retrieval/graph_beam.py:70-86`）。所以“枢纽实体被系统性丢失”不成立。
   - NLI **不是每个 claim 调一次**，而是把所有 claim 拼成一次 LIGHT 调用（`generation/entailment.py:83-94`）。
   - 缓存键**不是原文**，已经做了小写化和空白归一（`retrieval/cache.py:27-28`）。
   - 边到 chunk 的溯源**已经存在**：`RelationRecord.sources[].chunk_id` 在构图时写入（`knowledge/graph_builder.py:60-71`），Neo4j 也会持久化（`stores/neo4j_codec.py:26-31`）。缺的是**检索阶段没有把它带出来**。
   - Fast→Deep 升级时**会继承 Fast 已拿到的证据**（`agent/fast_path.py:51`），并不是“白白多花一轮”。
   - IndexVersion 比评估说的**更粗**：它是全局单计数器，不分租户（`core/versioning.py:14-27`）。任意租户写入都会让所有租户的缓存失效。
2. **“效果瓶颈在检索排序”只说对了一半。** 实测 g2 的 33 个失败中，只有 6 个的标准答案出现在证据里。但另外 27 个失败的主因是**规划把 3 跳压成了 1 跳**（`ceo_parent_producer` 模板 0/14，全部只生成了 1 个子问题），以及**金标本身的缺陷**（金标审计结果：13 道题在图里有多个合法答案，金标只给了一个；16 道题的 gold_path 把互逆关系写成了反向标签）。重排器有价值，但它不能修复规划和金标问题。
3. **两个 P0 架构风险被证实，而且比评估描述的更严重：**
   - **BM25 不跨进程**：独立 worker 写进的是自己进程内的 BM25（`knowledge/ingest_worker.py:176-179`），从不落盘；API 只在启动时读一次 `bm25.json`（`stores/factory.py:137-138, 170-174`）。
   - **BM25 租户泄漏**：`save()` 不写 `tenant_id`（`stores/fulltext_store.py:96-108`），重新加载后 `tenant_id=""`；而 `search()` 把 `""` 视为对所有租户可见（`:83`）。**重启之后，所有租户都能检索到彼此的 chunk。**
   - **缓存失效静默失败**：`InProcessEventBus.publish` 吞掉所有异常（`core/events.py:96-100`），`invalidate` 失败时 IndexVersion 不会递增，用户会一直拿到旧答案，也没有指标或告警。
4. **评测集确实太小，而且有标注缺陷。** 31 题的 95% Wilson 区间是 [50.1%, 81.4%]，宽 31pp；200 题是 [77.7%, 88.0%]，宽 10pp。另外 g2 只用了 7 个模板；open 类 30 题全部是 1 跳的路径解释题；审计标记了 29 道题（13 道金标有歧义、16 道互逆标签写反，例如 `Northwind Analytics PARENT_OF Apex Holdings`）；poc 的 gold_path 是手写的非规范格式，31 题中有 18 题无法被机器校验。**扩容之前必须先做金标审计**，否则规模越大，噪声越大。

---

## 1. 实测基线（离线，`--no-llm --memory-graph`）

| 数据集 | 正确 | 准确率 | 95% Wilson CI | 备注 |
|---|---|---|---|---|
| `poc_cases` | 21/31 | 67.7% | [50.1, 81.4] | CI 门禁 |
| `g2_all` | 167/200 | 83.5% | [77.7, 88.0] | pilot 三元组 |
| ├ 2hop | 82/90 | 91.1% | | |
| ├ 3hop | 35/60 | 58.3% | [45.7, 69.9] | **主要短板** |
| ├ open | 30/30 | 100% | | 全部来自 `competes_path` 一个模板，区分度为零 |
| └ no_answer | 20/20 | 100% | | 只有 phantom 实体一种 |

g2 按模板：`ceo_parent_producer` 0/14、`competitor_of_producer` 1/5、`ceo_competitor_producer` 35/46、`ceo_of_parent` 36/39、`ceo_prior_employers` 45/46。

**解读**：失败集中在“产品 → 生产商 → …”这类链条。规划器把 `the parent company of the producer of X` 识别成 `What is the parent company of X?`，一跳都没有命中，结果是 `no evidence retrieved`。这是规划问题，加重排器解决不了。

---

## 2. 逐条核对

图例：✓ 与代码相符　◐ 部分相符或需修正　✗ 与代码不符

### 2.1 Agent

| 原文判断 | 结论 | 证据 |
|---|---|---|
| critic 每 hop 都调 LLM | ✓ | `agent/loop_runtime.py:161-186`：只要 `allow_llm`，就走 `_llm_critique`（`agent/critic.py:174-177`） |
| 离线有确定性 critic 可用 | ✓（原文没提到） | `agent/critic_offline.py`，加上 `critic_guard.force_incomplete_multihop` |
| `max_llm_calls=16`，`timeout=45s` | ✓ | `configs/default.yaml` 中的 guardrails |
| planner 一次生成 DAG，之后靠 rewrite 修补 | ✓ | `agent/planner.py` + `planner_patterns.py`；实测 3 跳被压成 1 跳 |
| 误判 Fast 后要“白白多花一轮” | ◐ | 升级时会继承 Fast 的证据（`agent/fast_path.py:51`），损失的是 Fast 那一轮的延迟，证据并没有浪费 |

### 2.2 检索

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 没有重排器，只定义了协议 | ✓ | `retrieval/fusion.py:27-38`：只有 `IdentityReranker` |
| 过滤度数大于 30 的节点 | **✗** | `graph_beam.py:113-120` 只用于判断；判定为高度数后改用 `preferred_relations` 硬过滤，结果为空则无过滤重试（`:70-86`） |
| 三路权重固定 | ✓ | `core/evidence.py:202` 是无权重的 RRF |
| 1200 字定长切分 | ◐ | 1200 字，overlap 150，会在句边界回退（`knowledge/ingest.py:52-80`），不是硬切 |
| 缓存按原文精确匹配 | **✗** | `normalize_query_key` 做了小写和空白归一，但确实没有语义缓存 |

### 2.3 知识图谱

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 逐块抽取，块间没有上下文 | ✓ | `knowledge/extract_chunk.py` |
| 边和 chunk 之间缺少溯源 | **◐** | 存储层**有**（`graph_builder.py:60-71`，`neo4j_codec.py:26-31`），检索层**没带出来**（`retrieval/graph.py:166-187` 的 Citation 不含 chunk_id） |
| Schema 固定，Schema 外的关系被拒 | ✓ | `configs/schema/domain_v0.yaml`（5 种实体、13 种关系）+ `rejected_triples.jsonl` |
| 消歧逐对判定，超线性增长 | ✓ | `knowledge/resolution.py:135-145`：同类型内 O(n²) |

### 2.4 存储

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 全文检索只有 BM25 一个实现 | ✓ | `stores/factory.py:93-94` |
| 多进程之间可能不共享 | **✓（已证实）** | worker 只写进程内存（`ingest_worker.py:176-179`）；API 只在启动时加载（`factory.py:137`） |
| 新发现：租户泄漏 | **✓（新）** | `fulltext_store.py:96-108` 的 save 丢弃 `tenant_id`；`:83` 把 `""` 视为全局可见 |
| 新发现：每次 `index()` 全量重建 | ✓（新） | `fulltext_store.py:63-70` 每次都重算整个语料的 IDF，写入成本 O(N) |
| 三个存储之间没有事务 | ✓ | `app/write_path.py` 分别写入 |
| 租户只靠过滤隔离 | ✓ | 各个 store 都用 `tenant_id` 过滤 |
| 新发现：hybrid Profile 用的是内存向量 | ✓（新） | `bootstrap/profile.py`：HYBRID = Neo4j + **memory** vector。worker 写入的向量对 API 同样不可见，与 BM25 是同一类问题（列入 S-2） |

### 2.5 LLM 网关

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 连续 2 次失败就熔断 20s | ✓ | `llm/circuit.py:21-22` |
| 没有降级目标 | ✓ | `llm/provider.py:150-151`：熔断打开后直接抛错 |
| 新发现：chat 的 STRONG、LIGHT 和 embedding **共用同一个熔断器** | ✓（新） | `provider.py:47`：只有一个 `self.circuit`；embedding 端点故障会把 chat 一起熔断，STRONG 故障也会连带 LIGHT 不可用，因此原文的“降级到 LIGHT”在现有结构下**无法实现** |
| 新发现：`embed_many` 逐条调用 | ✓（新） | `provider.py:133-134` |
| JSON + 校验重试 | ✓ | `llm/structured.py:39` 用的是 `json_object`，不是 `json_schema` |

### 2.6 生成

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 每个 claim 调一次 LLM 做 NLI | **✗** | 全部 claim 合并成一次调用（`entailment.py:83-94`） |
| 判定器弃权即视为通过 | ✓ | `entailment.py:72-78` |
| 置信度是规则式的 | ✓ | `generation/confidence.py` |

### 2.7 可观测

| 原文判断 | 结论 | 证据 |
|---|---|---|
| trace_id 和 ReasoningChain 不一定能互相跳转 | **◐（更严重）** | 整个 `src/` 里**没有 `trace_id` 字段**；ReasoningChain 不记录任何 trace 关联 |
| 订阅者异常只记日志 | ✓ | `core/events.py:96-100` |
| 新发现：健康检查里的熔断状态永远不会上报 | ✓（新） | `api/app.py:216-220` 读取 `svc._circuit_breaker`，但整个代码库里没有任何地方给它赋值（列入 X-1） |
| IndexVersion 按租户 | **✗（更粗）** | 全局单计数器（`core/versioning.py:14-27`） |

### 2.8 评测

| 原文判断 | 结论 | 证据 |
|---|---|---|
| 31 题时 CI 约 ±16pp | ✓ | Wilson 区间 [50.1, 81.4] |
| 需要扩容到 ≥300 条 | ◐ | 先要审计金标：g2 中 13 道题金标有歧义，16 道互逆标签写反，open 类只有 1 个模板且全是 1 跳 |
| 300 条能检出 5–8pp 差异 | ◐ | 非配对比较时，n=300 的最小可检出差异约为 11pp；**配对 McNemar** 才能到 5–8pp，前提是 A/B 跑同一批题 |
| 没有分模块评测 | ✓ | `eval/` 只有端到端评测和证据召回 |

---

## 3. 修订后的优先级

| 优先级 | 项 | 理由 |
|---|---|---|
| **P0** | E-1 统计显著性与配对对比；E-2 金标审计 | 没有这两项，后面所有 A/B 结论都不可信 |
| **P0** | S-1 BM25 租户泄漏 + 跨进程共享 | 正确性和安全问题，不是调参能解决的 |
| **P0** | O-1 事件可靠投递（缓存失效） | 失败时会静默返回旧答案 |
| P1 | R-1 两阶段召回 + 可插拔重排 | 原文的 P0；离线 A/B 没有显著收益（§7），降为 P1，等 live A/B 结果再定 |
| P1 | K-1 边到 chunk 溯源透出 | 数据已经有了，只差检索层透出，成本低 |
| P1 | A-1 分层 critic | LLM 调用减少可以确定 |
| P1 | L-1 熔断滑窗 + 档位降级 | 可用性 |
| **P0** | P-1 规划修复（QueryFrame，见上一份报告） | 3 跳只有 58% 的直接原因（`ceo_parent_producer` 0/14） |
| P2 | 其余项 | 见 §5 |

---

## 4. 第一批精细化设计（本次实现）

每一项都满足：离线行为不变（poc 21/31，g2 167/200），默认关闭或向后兼容，并有单测。

### O-1（E-1）评测统计与配对对比

- **新增** `eval/stats.py`：
  - `wilson_interval(k, n)`；
  - `mcnemar(a_correct, b_correct)`：精确二项检验，返回 `b01 / b10 / p_value`；
  - `breakdown(rows, cases, key)`：按 `category`、`metadata.template` 分组统计准确率。
- **新增** `scripts/eval_compare.py`：输入两个运行报告 JSONL 和用例文件，输出整体准确率和 CI、分组准确率、配对 McNemar 结果、P50/P95 延迟、LLM 调用均值。
- **合并规则**（写入 `rules.md`）：架构 A/B 必须报告配对 p 值；p < 0.05 且准确率、成本、延迟三者都没有明显退化，才能合并。

### O-2（E-2）金标审计

- **新增** `eval/gold_audit.py` 和 `scripts/audit_gold.py`。对每个用例，基于三元组做确定性检查：
  1. `gold_path` 的每条边在图中存在（允许互逆：PARENT_OF ↔ SUBSIDIARY_OF，COMPETES_WITH 视为对称）。不存在则报 `path_edge_missing`；只有反向存在则报 `path_direction`。
  2. 从锚点沿 `relation_chain` 遍历，得到全部合法答案集合。若集合大小大于 1 而金标只给了 1 个，报 `ambiguous_gold`。
  3. 金标答案不在合法答案集合中，报 `gold_unreachable`。
- 输出 JSON 报告，并按问题类型计数。**不自动修改数据集**；修正走人工复核。CI 暂不拦截，只产出报告。

### O-3（S-1）全文检索：修复租户泄漏 + 跨进程共享

- `BM25FulltextStore.save/load` 写入和读取 `tenant_id`。读到旧格式（没有该字段）时保持 `""`，与当前行为一致，并记录一条警告。
- **新增** `SharedFileBM25Store(path)`：
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

---

## 5. 后续批次（只做设计，不在本批实现）

| ID | 项 | 设计要点 | 验收 |
|---|---|---|---|
| E-3 | 评测扩容到 ≥300 | 先完成 E-2 审计和修正；新增模板覆盖 inverse / intersection / qualifier / missing_relation / false_premise（见《评测集扩充提示词》） | 每个子类型 ≥30 条，heldout 不参与调优 |
| E-4 | 分模块评测 | 路由 acc、检索 recall@k / MRR（以 gold_evidence 为准）、抽取 P/R、消歧 pairwise F1、NLI acc、置信度 ECE | 每项有基线文件 |
| P-1 | QueryFrame 规划 | 见上一份报告；直接修复 `ceo_parent_producer` 0/14 | 3hop ≥ 80% |
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

- 每个提交都必须通过：ruff、format、`check_code_metrics`、单测覆盖率 ≥80%、离线评测 poc 21/31（agentic + triage）。
- 本批另外要求：g2_all 保持 167/200（默认配置下行为不变）。新开关打开时（`rerank.kind=lexical`、`critic_mode=tiered`），用 `scripts/eval_compare.py` 出配对报告，结果记入 §7。

## 7. 本批验证记录

所有提交都在 `refactor/arch-v2` 上，每个提交都通过了 ruff、format、code metrics 和单测（最终 385 passed，覆盖率 86.12%）。

| 提交 | 项 | 默认配置下的行为 | 验证 |
|---|---|---|---|
| `4e2bf4e` | O-1 / O-2 评测统计与金标审计 | 无变化 | g2 审计：200 题中 29 题被标记（ambiguous 13、inverse_label 16）；poc 31 题中 18 题的 gold_path 无法机器校验 |
| `da95fc3` | O-3 BM25 租户与共享 | offline / eval 不变；live / hybrid 改为 shared_file | 单测覆盖：重启后不再跨租户可见；worker 写入后 API 可见；快照损坏时保留上一次的正常状态 |
| `7ee91fa` | O-4 事件可靠投递 | 普通订阅者不变 | 单测：重试成功、重试耗尽后执行 fail-safe、fail-safe 自身异常被隔离；失效失败时缓存被清空，并计入 `event_handler_failures` |
| `844e0f3` | O-5 两阶段召回 + 重排 | `kind=none`，不变 | 见下方 A/B |
| `147846f` | O-6 边到 chunk 溯源 | 只是增加字段 | 单测：邻居候选和路径候选都带有 `source_chunk_ids` |
| `d76268d` | O-7 分层 critic | `critic_mode=llm`，不变 | 见下方 A/B |
| `b7a5c62` | O-8 熔断与降级 | 与 V1 等价（阈值 2，冷却 20s，无窗口，不降级） | 单测：路由之间相互隔离、窗口失败率触发熔断、STRONG 熔断后降级到 LIGHT |

**离线评测（默认配置）**：poc 21/31（agentic 和 triage 都是），g2 167/200（agentic 和 triage 都是），与基线完全一致。

**O-5 重排 A/B**（同一份图、同一批题，配对 McNemar）：

| 配置 | poc | g2 | p 值 | P95 延迟 |
|---|---|---|---|---|
| 无重排（基线） | 21/31 | 167/200 | — | 51 / 63 ms |
| lexical | 20/31 | 167/200 | 1.0 / 1.0 | 38 / 57 ms |
| cross-encoder（ms-marco-MiniLM-L-6-v2，CPU，HTTP） | 21/31 | 167/200（5 题变对、5 题变错） | 1.0 / 1.0 | **833 / 465 ms** |

结论：在当前的离线答案生成器（基于图启发式选答案）下，**重排对准确率没有显著影响，在 CPU 上还会让 P95 延迟增加 0.4–0.8 秒**。这与 §1 的诊断一致：失败主要来自规划和金标，而不是排序。重排的价值要等到**真实 LLM 生成**时，通过 live A/B 来确认（需要 GPU 或托管的 bge-reranker-v2-m3）。因此 R-1 保持默认关闭，**不建议把它作为 P0**。

**O-7 分层 critic**：离线运行时统计每次带证据的 critic 调用。

| 数据集 | 带证据的 critic 调用 | 确定性闸门可以直接判定的 | 可以省掉的 LLM critic 调用 |
|---|---|---|---|
| poc | 46 | 38 | **83%** |
| g2 | 346 | 309 | **89%** |

在 live 模式下，相当于每题约省 1.2 次 LLM 调用（poc）。这一项能否保持准确率不下降，需要在 live 环境下用 `AGR_CRITIC_MODE=tiered` 跑配对对比，并用 `scripts/eval_compare.py` 判定。

**实验开关**：`AGR_RERANK=none|lexical|http`，`AGR_CRITIC_MODE=llm|tiered|offline`。对比命令：

```bash
agr-run-cases --cases evals/datasets/g2_all.jsonl --no-llm --memory-graph \
  --seed-triples data/processed/pilot_triples.jsonl --run-name base
AGR_CRITIC_MODE=tiered agr-run-cases ... --run-name cand
python scripts/eval_compare.py reports/base.jsonl reports/cand.jsonl \
  --cases evals/datasets/g2_all.jsonl --fail-on-worse
```

**CI**：`eval-offline` job 新增 g2 门禁（下限 167/200）和金标审计报告（只出报告，不拦截合并）。
