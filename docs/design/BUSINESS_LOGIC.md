# 业务逻辑图与完整性审查（BUSINESS LOGIC）

**初次审查：** 2026-07-26（纯静态审查）
**本次更新：** 2026-10-16 —— BL-01…BL-14 全部落地，**已实际运行**：`pytest tests/unit`（含新增 `test_bl_write_path.py` / `test_bl_reasoning_gates.py` 共 23 例）、`scripts/check_code_metrics.py`、`ruff`、离线评测 `agr-run-cases --no-llm`。
**定位：** ① 全系统业务逻辑图（节点 / 边 / 判定点）；② 业务逻辑完整性缺口及其落地状态。
**权威边界：** 强制规则以 [`plan/engineering/rules.md`](../../plan/engineering/rules.md) 为准；债务与延期以 [`IMPORTANT.md`](../reviews/IMPORTANT.md) 为准；模块地图以 [`ARCHITECTURE.md`](../ARCHITECTURE.md) 为准。

---

## 0. 总体结论

| 领域 | 业务逻辑完整性 | 说明 |
|------|----------------|------|
| 查询链路（问 → 答） | **完整** | triage / fast-path / agentic / guardrail / 兜底 answer 全部有出口 |
| 护栏与预算 | **完整** | 「计数 → 判定 → 降级出口」三段齐全；hop 现只计**依赖深度**，广度单独预算（BL-12） |
| 检索链路 | **完整** | 三路检索 + RRF + 租户维度缓存键；历史事实降权（BL-14） |
| 输出契约 / 引用门禁 | **完整** | 四级规则门禁（引用 → 绑定 → 词面+对象锚定 → 关系一致）+ 在线 LLM 蕴含判定（BL-02 / BL-13） |
| 知识写入链路（文档 → 图谱） | **完整** | 上传 → 任务 → worker → 抽取（LLM / 离线规则）→ 校验 → 消解 → 增量入图 → 冲突复核（BL-01） |
| 人工复核闭环 | **完整** | 决策由 `ReviewExecutor` 作用于图谱，结果写回条目，批次结清后任务 `review → done`（BL-03） |
| 多租户隔离 | **完整** | SSE 透传 `tenant_id`；写路径按租户打标、冲突与删除均限定在本租户（BL-04 / BL-01） |
| 摄取任务状态机 | **完整** | `REVIEW` 有生产者与消费者；`EXTRACTING` 崩溃可重投（BL-05 / BL-06 / BL-11） |
| 计划（DAG）执行语义 | **完整** | 就绪且互不依赖的节点并发检索、共享同一 hop（BL-12） |
| 图谱时间维度 | **完整** | 关系带 `valid_from/valid_to`；时效冲突按时间裁决，旧事实闭区间保留（BL-14） |

**一句话结论：** 读路径与写路径均已闭环 —— 产品叙事「上传 → 抽取 → 校验 → 复核 → 入图 → 可被多跳检索」在服务进程内有完整通路，复核决策是控制面而非台账。

---

## 1. 全局业务逻辑图

```mermaid
flowchart TB
    subgraph READ["读路径"]
        direction TB
        Q["POST /v1/query · /v1/query/stream"] --> AUTH["AuthRateLimitMiddleware + RBAC"]
        AUTH --> SVC["QueryService"]
        SVC --> CACHE{"答案缓存命中?<br/>键含租户 + 索引版本"}
        CACHE -->|"是"| RESP["ReasoningChain"]
        CACHE -->|"否"| LOOP["agent: triage → fast path / LangGraph<br/>（并发分支 BL-12）"]
        LOOP --> RETR["retrieval: 向量 · 图 Beam · BM25 → RRF<br/>历史事实降权 BL-14"]
        LOOP --> GEN["generation: 规则门禁 + 蕴含判定 BL-13"]
        GEN --> RESP --> PERSIST["审计落盘 + 预算结算"]
    end

    subgraph WRITE["写路径"]
        direction TB
        UP["POST /v1/docs"] --> SAVE["DocStore.save"] --> TASK["IngestTaskStore: queued"]
        TASK --> WORKER["IngestWorker<br/>API 内联（AGR_INGEST_INLINE=1）或独立进程"]
        WORKER --> VIDX["chunk → embed → Vector / BM25"]
        WORKER --> GI["GraphIngestor<br/>LLM 抽取 | 离线规则抽取"]
        GI --> GATE["schema + 置信度门禁"] --> RES["实体名对齐已有图谱"]
        RES --> INC["IncrementalUpdater<br/>基数 · 时间 · 置信度 · 租户"]
        INC --> KG[("GraphStore")]
        INC -->|"需人工"| RQ["ReviewQueue: conflict"]
        INC --> COMMIT["on_commit: 缓存失效 + 实体词表刷新"]
        CLI["CLI agr-build-graph"] --> KG
    end

    subgraph REVIEW["人工复核"]
        direction TB
        RQ --> DEC["POST /v1/review-queue/{id}/decision"]
        DEC --> EXE["ReviewExecutor"]
        EXE -->|"写入 / 退役 / 合并 / 回滚"| KG
        EXE --> SETTLE["批次无待决冲突 → 任务 review → done"]
    end

    KG --> RETR
    VIDX --> RETR
```

---

## 2. 查询链路逻辑图（读路径）

```mermaid
flowchart TB
    START(["question"]) --> CHIT{"闲聊/能力问询?"}
    CHIT -->|"是"| CHITANS["直接返回 chain"]
    CHIT -->|"否"| TRIAGEEN{"enable_triage 且非 force_agentic?"}
    TRIAGEEN -->|"否"| AGENTIC
    TRIAGEEN -->|"是"| TRIAGE["triage()"]
    TRIAGE -->|"FAST_PATH"| FAST["run_fast_path"]
    TRIAGE -->|"AGENTIC"| AGENTIC
    FAST --> ESC{"证据弱?"}
    ESC -->|"否"| DONE
    ESC -->|"是，升级一次"| AGENTIC

    subgraph AGENTIC["LangGraph StateGraph"]
        direction TB
        P["planner<br/>DAG + 广度预算"] --> E["executor"]
        E --> PF{"当前节点已被并发预取?"}
        PF -->|"是：同 hop，不计深度"| USE["取用预取结果"]
        PF -->|"否：hop+1"| GRP["当前节点 + 就绪兄弟节点<br/>线程池并发检索"]
        USE --> RAE{"route_after_executor"}
        GRP --> RAE
        RAE -->|"done / 护栏"| A["answer"]
        RAE -->|"否则"| C["critic"]
        C -->|"继续"| E
        C -->|"完成 / 护栏"| A
    end

    A --> GATES{"规则门禁<br/>引用 · 绑定 · 词面+锚定 · 关系一致"}
    GATES -->|"通过且在线"| NLI{"LLM 蕴含判定"}
    GATES -->|"失败"| REGEN["按原因定向重生成一次"]
    NLI -->|"entailed / 判定器弃权"| DONE(["ReasoningChain"])
    NLI -->|"否"| REGEN
    REGEN -->|"仍失败"| FB["honest_fallback"] --> DONE
    CHITANS --> DONE
```

### 2.1 判定点

| 判定点 | 位置 | 分支 |
|--------|------|------|
| 闲聊短路 | `agent/chitchat.py` | 命中 → 跳过检索 |
| 路由 | `agent/loop.py` `_run_with_triage` | FAST_PATH / AGENTIC |
| Fast Path 升级 | `agent/loop_policy.py` | 证据弱 → agentic |
| 并发分支 | `agent/parallel_branches.py` `retrieve_for_node` / `loop_runtime._start_node` | 预取命中 → 同 hop；否则 hop+1 并预取就绪兄弟 |
| executor / critic 后路由 | `loop_runtime.route_after_*` | 护栏 / hop 上限 → answer |
| 递归兜底 | `agent/loop_recover.py` | `GraphRecursionError` → checkpoint 重建 |
| 规则门禁 | `generation/citations.validate_answered_claims` + `claim_support.claim_supported` | 失败 → 定向重生成 |
| 蕴含判定 | `generation/entailment.judge_claims` | 非 entailed → 定向重生成；判定器异常 → 弃权并记录 |

### 2.2 护栏：计数 → 判定 → 出口

| 护栏 | 计数点 | 触发出口 |
|------|--------|----------|
| max_hops（依赖深度） | `Guardrails.on_hop_start`（并发预取节点走 `check_branch`，不加 hop） | executor 置 `done` → answer |
| max_sub_questions（广度） | `plan_dag.cap_plan_breadth` / 动态插入前检查 | 超出部分记入 `metadata`，不静默丢弃 |
| 超时 / LLM 调用 / token | `Guardrails._check` | 同上 |
| recursion_limit | 编译期下限 `2*hops+5` | `loop_recover` 重建部分答案 |
| 租户预算 | `service_query` 预留 / 结算 | 429 `BUDGET_EXCEEDED` |

---

## 3. 知识写入链路逻辑图（写路径）

```mermaid
flowchart TB
    A1["POST /v1/docs"] --> A2["DocStore.save"] --> A3["IngestTaskStore: queued<br/>+ SPOTCHECK 复核项（含 doc_ids）"]
    A3 --> A4["IngestWorker 认领: extracting（租约 + 心跳）"]
    A4 --> A5["chunk → embed → Vector / BM25"]
    A4 --> A6["GraphIngestor.ingest_chunks"]
    A6 --> A7{"有 LLM?"}
    A7 -->|"是"| A8["extract_from_chunk（失败回退规则）"]
    A7 -->|"否"| A9["extract_rules_from_chunk"]
    A8 --> A10["schema + 置信度门禁"]
    A9 --> A10
    A10 --> A11["名称对齐已有实体"] --> A12["IncrementalUpdater（本租户索引）"]
    A12 --> A13{"冲突类型"}
    A13 -->|"无冲突 / 多值关系"| W["写入"]
    A13 -->|"双方带日期"| T["SUCCEED / HISTORICAL<br/>旧事实设 valid_to，不删除"]
    A13 -->|"置信度显著更高"| AU["AUTO_UPDATE：写新退旧"]
    A13 -->|"略高"| RV["REVIEW → ReviewQueue(conflict, batch=task)"]
    A13 -->|"更低"| KO["KEEP_OLD（计数守恒）"]
    W --> OUT{"task_outcome"}
    T --> OUT
    AU --> OUT
    RV --> OUT
    KO --> OUT
    OUT -->|"有错误"| F["failed"]
    OUT -->|"有待复核冲突"| R["review"]
    OUT -->|"否则"| D["done（含写入事实数）"]
```

### 3.1 摄取任务状态机

```mermaid
stateDiagram-v2
    [*] --> queued: create(docs 非空)
    [*] --> empty: create(docs 为空)
    queued --> extracting: worker 认领
    queued --> failed
    extracting --> queued: 租约过期重投
    extracting --> done: 无错误、无待复核冲突
    extracting --> review: 存在待复核冲突
    extracting --> failed: 任一文档或图谱阶段出错
    review --> done: 批次最后一个冲突被决策（ReviewExecutor）
    review --> failed
    done --> [*]
    failed --> [*]
    empty --> [*]
```

### 3.2 复核队列状态机与执行

```mermaid
stateDiagram-v2
    [*] --> pending: enqueue()
    pending --> approved: decide(approve)
    pending --> rejected: decide(reject)
    pending --> skipped: decide(skip)
    approved --> executed: ReviewExecutor.execute
    rejected --> executed: ReviewExecutor.execute
    skipped --> executed: 无图谱效果
    executed --> [*]
    note right of executed
      conflict 批准：写新事实、退役旧边
      extraction 批准/驳回：写入/移除该事实
      resolution 批准：实体合并
      spotcheck 驳回：回滚该上传产生的事实
      结果写入 item.execution，重放幂等
    end note
```

| 条目类型 | 批准 | 驳回 |
|---|---|---|
| conflict | 写入 incoming，退役 superseded（限本租户） | 保留现有事实 |
| extraction | 写入提议事实 | 若已入图则移除 |
| resolution | `apply_entity_merge(keep, drop)` | 两实体保留 |
| spotcheck | 接受上传 | 回滚「来源全部为该批文档」的关系 |
| feedback | 无图谱效果 | 无图谱效果 |

---

## 4. 验证项与落地测试

| # | 验证项 | 状态 | 自动化测试 |
|---|--------|------|-----------|
| V-01 | 上传后图谱实体增加 | ✅ | `test_bl_write_path::test_api_upload_reaches_graph_via_inline_worker` |
| V-02/03 | 中文 claim 不再必然失败 | ✅ | `test_citations.py`（BL-02 既有用例） |
| V-04 | 复核批准后图谱变化 | ✅ | `test_bl_write_path::test_conflict_goes_to_review_and_approval_takes_effect` |
| V-05/06 | 流式租户隔离 / 缓存 | ✅ | `test_audit_tenant_cache_isolation.py`、`test_live_sse_stream.py` |
| V-07 | worker 命令消费队列 | ✅ | `test_bl_write_path::test_cli_worker_is_wired_to_queue_and_graph` |
| V-08 | 中断恢复 | ✅ | `test_enterprise_completion.py`（stale requeue） |
| V-09/10 | 上传校验 | ✅ | `test_enterprise_readiness.py` |
| V-11…13 | 越权与存在性泄漏 | ✅ | `test_console_api.py`、`test_graph_entities_api.py` |
| V-14 | 批次计数守恒 | ✅ | `test_review_fixes.py` + `conflicts_temporal` 新计数 |
| V-15 | 非流式检索缓存生效 | ✅ | 缓存键含租户（BL-04） |
| V-16 | 超量子问题不再静默丢弃 | ✅ | `test_plan_dynamic_sq_insert.py` |
| V-17 | 并行分支延迟 Σ → max | ✅ | `test_bl_reasoning_gates::test_branches_run_concurrently`、`test_parallel_sibling_shares_the_hop` |
| V-18 | 关系错配被拦截（运行期与评测同口径） | ✅ | `test_bl_reasoning_gates::test_gate_rejects_claim_citing_edge_with_another_relation`、`test_eval_fabrication_uses_same_relation_rule` |
| V-19 | 时效冲突按时间裁决 | ✅ | `test_bl_write_path::test_dated_successor_closes_previous_ceo_instead_of_deleting`、`test_older_fact_is_stored_as_history` |

**运行结果（2026-10-16）：** 单测 309 通过（`test_web_claude_ui.py` 7 例为前端静态文件缺失导致的既有失败，与本文件无关）；覆盖率 84%（门禁 80%）；代码硬指标全部通过（同时修复了 3 处既有超标）；离线 POC 评测 21/31，与改动前逐例一致。

---

## 5. 落地状态（2026-10-16）

> `[x]` 已完成并有测试；括号内为主要实现位置。

| 缺口 | 状态 | 落地内容 |
|---|---|---|
| BL-01 上传→图谱无通路 | `[x]` | 新增 `knowledge/graph_ingest.py`（抽取 → 门禁 → 名称对齐 → 增量入图）与离线规则抽取器 `knowledge/extract_rules.py`（中英文句式，含日期）；`IngestWorker` 增加图谱阶段与 `task_outcome`；API 内联 worker `api/service_ingest.py`（`AGR_INGEST_INLINE=1`），独立 worker 移至 `knowledge/ingest_worker_cli.py` 并接入图谱；写入后 `on_graph_commit` 使缓存失效并刷新实体词表 |
| BL-02 中文 claim 必然失败 | `[x]` | CJK 二元组切词 + 按失败原因定向修复提示（既有） |
| BL-03 复核决策无执行器 | `[x]` | 新增 `knowledge/review/executor.py`：五类条目的批准/驳回语义、按租户删除、幂等重放、结果写回 `item.execution`、批次结清后任务 `review → done`；`IncrementalUpdater` 的待复核冲突直接进入 `ReviewQueue`；决策路由调用执行器 |
| BL-04 SSE 未传租户 | `[x]` | 既有修复；写路径同样按租户打标（`graph_builder.stamp_tenant`） |
| BL-05 / BL-06 | `[x]` | 既有修复；CLI worker 另接入图谱阶段 |
| BL-07 schema 门禁 | `[x]` | 既有修复；运行期写路径与复核执行器均经门禁 |
| BL-08 / BL-09 / BL-10 | `[x]` | 既有修复；复核执行与图谱阶段失败均显式上报，不静默 |
| BL-11 空分支 / 计数 | `[x]` | `IngestStatus.REVIEW` 现有生产者（`task_outcome`）与消费者（`_settle_task`）；新增 `conflicts_temporal` 计数保持守恒 |
| BL-12 广度/深度混同 | `[x]` | `agent/parallel_branches.py`：就绪且互不依赖的节点线程池并发检索，结果预取入状态，取用时不增 hop（`Guardrails.check_branch`）；`guardrails.parallel_branches` / `max_parallel_branches` 可配；并发组记入 `metadata.parallel_branches` |
| BL-13 门禁只到词面 | `[x]` | `generation/relation_consistency.py`：中英文关系线索 vs 边关系类型（含兼容/蕴含映射），运行期门禁与评测 `fabrication_rate` 同口径；`generation/entailment.py`：在线路径 LIGHT 档批量蕴含判定（`llm.entailment_check`），非 entailed 触发定向重生成，判定器异常则弃权并记录 |
| BL-14 图谱无时间维度 | `[x]` | `knowledge/temporal.py`：`valid_from/valid_to`；同边合并以较新日期优先；`incremental_conflicts` 新增 `SUCCEED / HISTORICAL`，旧事实设 `valid_to` 而非删除；检索对已结束事实降权（问「以前/曾」时不降）并在证据文本标注区间；schema 声明 `functional_on` 基数，多值关系不再误判冲突；抽取提示要求输出日期 |

### 5.1 仍需知悉的边界（非缺口）

| 项 | 说明 |
|---|---|
| 内联 worker 默认关闭 | 需 `AGR_INGEST_INLINE=1`；离线模式下独立 worker 与 API 不共享内存图谱，故离线请用内联方式，在线（Neo4j/Qdrant）两者皆可 |
| 规则抽取器 | 精确优先，只覆盖常见公司关系句式；在线模式优先用 LLM 抽取，失败逐块回退规则 |
| 蕴含判定成本 | 每次作答额外 1 次 LIGHT 调用；`MockLLMProvider` 与离线路径不调用 |
| 时间裁决前提 | 仅当冲突双方都带日期时按时间裁决，否则沿用置信度规则 |
| 并发范围 | 并发的是检索；critic 仍按节点顺序评估，避免改变判定语义 |

---

## 6. 原始缺口记录（2026-07-26 审查）

> 以下为初次审查的原文记录，行号对应当时版本，保留用于追溯；现状以 §0 与 §5 为准。

### 6.1 完整性缺口清单

严重度：🔴 关键（业务承诺无法兑现 / 数据越权）｜🟠 高｜🟡 中｜⚪ 低

**验证状态：** BL-01 / BL-02 / BL-03 / BL-04 已经过**独立对抗式复核**（复核者以「默认该结论错误」为立场，专门搜寻可推翻的调用点、默认值、中间件与并行模块），四条全部 **CONFIRMED**；复核同时修正了 BL-01 的调用点枚举、BL-02 的触发条件与行号、BL-04 的受影响子路径。其余条目为单次静态审查结论，**未经对抗复核**，标注为待验证。

**BL-12 / BL-13 / BL-14 为第二轮补充审查所得**（来源：`ARCHITECTURE_DESIGN.md` 净室设计与实现的逐条对差，见 [`DESIGN_VS_IMPLEMENTATION.md`](./DESIGN_VS_IMPLEMENTATION.md) 的 D1 / D2 / D3），同样**未经对抗复核**。该轮的第四条发现（人工复核决策无执行器，D5）与既有 **BL-03 完全重合**，故不另立条目 —— BL-03 的记述更完整（额外指出 `IncrementalUpdater._append_review` 与 `ReviewQueue` 是两套互不连通的存储）。

#### 🔴 BL-01：不存在「上传文档 → 进入知识图谱」的运行期通路

| 项 | 内容 |
|----|------|
| 证据 | 全仓库图写入调用点共 7 处，**无一可从 API 运行期到达**：底层 `graph_builder.py:101-102`；`resolution_merge.py:23`（仅经 `EntityResolver._apply_merge`，而 `EntityResolver(` 只在两个单测中构造）；`api/service.py:134`（**服务构造期**加载 seed 三元组）；`cli/graph_cmd.py:153`、`cli/cases_run.py:52`（CLI）；`incremental.py:204`、`eval/p3_ev.py:166`（离线评测演练）。 |
| 证据 | `IngestWorker.process_doc`（`ingest_worker.py:70-86`）只做 chunk → embed → 向量/全文索引；其构造函数（`:50-59`）**根本没有 graph 参数**。`api/` 与 `agent/` 两个包内对 `bundle.graph` 的引用除 `service.py:134` 外全部是读操作（`app.py:169/188`、`service_helpers.py:73`、`routes/knowledge.py:216`）。 |
| 证据 | `api/app.py:28-51` lifespan 只做 `setup_logging` → 可选 OTel → `build_default_service()` → `_validate_live_credentials`，**不启动任何 worker 或线程**；`src/` 内启线程处仅 `ingest_worker.py:141`（API 从不调用）与 `service_agent.py:21` 的查询线程池。 |
| 代码自陈 | `knowledge_upload.py:83` 的任务消息即写明：`"Documents persisted to doc store; run extract pipeline offline or via worker"`。 |
| 失败场景 | 管理员通过 `POST /v1/docs` 上传新文档 → 文档可被向量/BM25 检索到 → 但其中的实体与关系**永远不会进入图谱** → 图检索（多跳推理，本项目的核心卖点）对新文档完全无效。 |
| 影响 | 产品叙事「知识图谱作为结构化记忆」在运行期只对 CLI 预构建的语料成立。 |

#### 🔴 BL-02：在线回答路径的引用门禁对纯中文 claim 必然失败

| 项 | 内容 |
|----|------|
| 证据 | `generation/citations.py:142-148` `_content_tokens()` 以**空白分词**：`(text).lower().replace("-"," ").split()`。中文无空格 → 整句坍缩为一个 token（全角标点被 `isalnum()` 过滤掉，**不**起分隔作用）。`claims_lexically_supported`（`:116`，`min_overlap=1`）要求 claim 与被引证据**至少共享一个 token**。 |
| 触发条件（已精确化） | 失效点在 **claim 一侧**，与证据类型无关。判据：*claim 文本中若不含任何与证据共享的「空格分隔的拉丁/数字 token」，则必然判失败*。 |
| 实测（纯函数推演，未运行项目） | 证据 `Apex Holdings -[CEO_OF]-> Elena Varga (Person)`：<br/>· claim `Elena Varga is the CEO of Apex Holdings.` → 交集 {apex, elena, holdings, varga} → **通过**<br/>· claim `Apex Holdings的首席执行官是Elena Varga。` → 交集 {apex, varga} → **通过**（多词拉丁人名「漏」出可匹配 token）<br/>· claim `NovaTech的首席执行官是马库斯·韦伯。` → 交集 ∅ → **失败**<br/>· claim `苹果控股的首席执行官是埃琳娜·瓦尔加。` → 交集 ∅ → **失败** |
| 失败场景 | 在线路径 `generate_answer`（`answer.py:152`）→ `_apply_payload`（`:76`，`require_lexical_support` 默认 True，全仓库无任何调用点传 False）返回 None → 重生成一次（`answer.py:193`）→ 再次失败 → `honest_fallback("answer claims lacked evidence citations after regenerate")`（**`answer.py:221`**）。**纯中文问答消耗 2 次 STRONG 档 LLM 调用后退化为「无法回答」。** |
| 重试注定失败 | 重生成提示（`answer.py:100-103`）只说「每条 claim 必须带 evidence_ids」，而真实失败原因是**词面重叠**，与 id 无关 → 第二次调用是确定性浪费。 |
| 为何未被评测发现 | `answer.py:138` —— `allow_llm=False` 时直接走 `offline_answer`，**完全绕过该门禁**（`:150`、`:173` 的降级分支亦然）。`--no-llm` 的 20 例评测因此全绿。典型的离线/在线路径不对称。 |
| 三个入口均受影响 | `agent/loop_runtime.py:208`、`agent/fast_path.py:33`、`agent/loop_recover.py:83` —— 只要 `allow_llm and llm is not None` 就会过闸。 |
| 修复位置 | `generation/citations.py:144` 需改为 CJK 感知切分（CJK 连续段落须切出 unigram/bigram）。**注意：直接照搬 `stores/fulltext_store.py:11` 的 `[\w一-鿿]+` 正则无效** —— 它对 CJK 同样是「整段一个 token」。 |

#### 🔴 BL-03：人工复核决策没有执行器

| 项 | 内容 |
|----|------|
| 证据 | `ReviewQueue.decide()`（`review/queue.py:150-173`）只写 `status`/`reviewer`/`decided_at`/`decision_note`。`grep -rn "APPROVED\|approved" src/`（排除 `queue.py`）**零命中** —— 无任何代码消费已批准项。 |
| 失败场景 | 运维批准一条低置信冲突 → 图谱不变；驳回一条错误三元组 → 该三元组（若已入图）不被移除。复核队列是一本**只写的台账**，不是控制面。 |
| 关联 | `IncrementalUpdater._append_review`（`incremental.py:269`）把待复核冲突写入一个**独立的 JSONL** `review_log`，与 `ReviewQueue` 是两套互不连通的存储；`GET /v1/review-queue` 永远读不到它。 |

#### 🔴 BL-04：SSE 流式路径未传播 `tenant_id`，导致跨租户检索与缓存共享

| 项 | 内容 |
|----|------|
| 证据 | 同步路径 `loop_recover.py:36-46` 初始状态**含** `"tenant_id": tenant_id`（`:45`）；流式路径 `loop_stream.py:248-258` `_initial_state()` 只有 3 个参数，**不含 tenant_id**（调用点 `:223`）。全仓库 `agent/` 内 `"tenant_id"` 字面量仅两处命中：`loop_handlers.py:94`（读）与 `loop_recover.py:45`（写）。 |
| 传导链 | `loop_handlers.py:94` `tenant_id = ctx.state.get("tenant_id") or None` → 流式恒为 `None` → `executor.run(tenant_id=None)` → ① 三路检索**不做租户过滤**（`executor_dispatch.py:159/171`，图侧 `:106/:125/:146`）；② `executor.py:143` 读共享检索缓存、`:150` `cache_result=tenant_id is None` **写入**共享缓存。 |
| 存储层为「放行」而非「拒止」 | `fulltext_store.py:83` `if tenant_id is None or ...`；`vector_store.py:134` `if tenant_id is not None and ...`；`memory_graph.py:229-230` `if requested is None: return True`；Qdrant `vector_store.py:67-71` 仅在非 None 时加 `FieldCondition`。四个后端一致：`None` = 全量放行，泄漏方向而非 fail-closed。 |
| 失败场景 | 租户 A 用 `POST /v1/query/stream` 提问 → 检索命中租户 B 的实体/chunk 并作为证据引用；结果写入全局检索缓存 → 租户 B 后续同样子问题直接读到 A 的检索结果。 |
| 受影响子路径（已精确化） | 闲聊：不涉检索；**Fast Path 未升级：正确**（`loop_stream.py:177` 显式传 `tenant_id`）；**Agentic 三种进入方式全部受影响** —— triage 路由（`:146`）、`force_agentic`/`enable_triage=False`（`:100`）、**Fast Path 升级**（`:182`）；非流式 `POST /v1/query`：正确。 |
| 缓存键无租户 | `RetrievalCache.retrieval_key`（`cache.py:133`）只含 index_version + 归一化 query + tools，**不含 tenant_id**，其隔离完全依赖「租户作用域运行绕过缓存」这一约定 —— 该约定在流式路径被打破。答案缓存**不受影响**（`cache.py:150` 已含租户，另有 `service_helpers.py:148` 元数据兜底）。 |
| 与已发布承诺冲突 | `docs/reviews/ENTERPRISE_READINESS.md:278` 明写「检索三路、executor dispatch、fast path 与 agent loop 透传 `tenant_id`（**租户作用域运行绕过检索缓存**，隔离优先）」—— agent loop 一环在流式下不成立。 |
| 连带发现 | API 侧 `tenant_id` 恒非空（`api/auth.py:169/178/189`、`api/service.py:168/177` 均默认 `"default"`），因此 `executor.py:143` 的 `tenant_id is None` 判据意味着 **P3-PERF-04 检索缓存在所有非流式 API 查询上实际是失效的**，而在流式上「生效」恰恰是本缺陷的副作用。修复 BL-04 会同时让两侧都关闭该缓存 —— 需要一并决定缓存键是否改为含租户。 |
| 现有测试盲区 | `test_enterprise_completion.py:39-74` 只直接调 `store.search/neighbors`；`test_audit_tenant_cache_isolation.py` 只覆盖答案缓存与审计库；`test_live_sse_stream.py:26/51` 虽传 `tenant_id="t"` 但不做任何隔离断言。 |
| 修复位置 | `agent/loop_stream.py:223` —— `opts.tenant_id` 已在 `ctx.run_opts` 上（由 `resolved_run_opts` 填充，`loop.py:264`），只需把它并入初始状态，对齐 `loop_recover.py:45`；该行以上无需改签名。 |

#### 🟠 BL-05：文档化的 worker 命令不消费任务队列

| 项 | 内容 |
|----|------|
| 证据 | `CLAUDE.md` 记载 `python -m agentic_graphrag.knowledge.ingest_worker --once  # consume queued upload tasks`，但 `ingest_worker.py:181-189` 的 `__main__` 构造 `IngestWorker(...)` **未传 `task_store=`** → `run_once()`（`:110`）走 legacy 分支遍历 `doc_store.list_ids()`。 |
| 失败场景 | 上传后运行文档化命令 → 任务状态永远停在 `queued`，`GET /v1/ingest-tasks/{id}` 永远返回 queued。且该命令另起进程调 `create_offline_bundle()`，若 doc store 为 `InMemoryDocStore`（`stores/doc_store.py:11`）则与 API 进程不共享数据，worker 看不到任何文档。 |

#### 🟠 BL-06：`extracting` 态崩溃后任务永久卡死

| 项 | 内容 |
|----|------|
| 证据 | `ingest_worker.py:124` 先 `transition(QUEUED→EXTRACTING)` 再处理；`IngestTaskStore.pending()`（`ingest_tasks.py:90`）**只返回 QUEUED**；`_ALLOWED_TRANSITIONS`（`:24-28`）无 `EXTRACTING→QUEUED` 重投路径，也无租约/心跳/超时字段。 |
| 失败场景 | worker 在处理中被 kill → 任务永久停留 `extracting`，无任何机制重投或告警。 |

#### 🟠 BL-07：schema 校验门禁在写入函数中是「可选参数」而非「门」

| 项 | 内容 |
|----|------|
| 证据 | `schema_check.py:3` 声明不变量「非符合 schema 的三元组被拒绝并记录，**绝不进入图**」。但 `graph_builder.load_triples_into_graph`（`:74-97`）的 `schema` 参数默认 `None`，为 `None` 时**完全跳过** `gate_triples`。 |
| 各调用点 | `cli/graph_cmd.py:153` ✅（在 `_gate_and_report` 中已提前 gate）；`api/service.py:134` ❌ 未 gate（seed 三元组，风险可接受但不变量不成立）；`incremental.py:204` ❌ **显式** `schema=None`。 |
| 叠加缺陷 | `IncrementalUpdater` 两处构造点（`eval/p3_ev.py:167`、`tests/unit/test_phase3_kg_and_budget.py:43`）**都未传 `schema=`** → `_gate()`（`incremental.py:210`）直接原样返回，增量路径既无 schema 校验也无置信度阈值。 |
| 次级 | `graph_builder.py:92` —— 即使传了 schema，`confidence_threshold=None` 时 `thr=0.0`，置信度过滤退化为空操作。 |

#### 🟡 BL-08：上传校验可被绕过 + PDF 声称支持但无解析

| 项 | 内容 |
|----|------|
| 证据 | `knowledge_upload.py:23` `if ext and ext not in ALLOWED_EXTENSIONS:` —— **无扩展名文件 `ext` 为空 → 短路跳过白名单**。 |
| 证据 | `:48` 大小校验发生在 `await file.read()`（`:47`）**之后** → 5MB 限制无法阻止超大文件先进内存。 |
| 证据 | `ALLOWED_EXTENSIONS` 含 `pdf`（`:16`），但 `:54` 一律 `content.decode("utf-8", errors="replace")`，**无 PDF 文本抽取** → PDF 被存为乱码并进入分块与索引。 |

#### 🟡 BL-09：跨租户越权与存在性泄漏（API 层）

| 项 | 内容 |
|----|------|
| 复核决策无租户校验 | `routes/knowledge.py:145` `svc.review_queue.decide(item_id, ...)` 不传租户；`ReviewQueue.decide`（`queue.py:150`）也不校验 —— 与 `list()`（`:126`，有 `tenant_id` 过滤）不对称。租户 A 的 operator 知道 item_id 即可决策租户 B 的条目。 |
| 图实体接口无租户过滤 | `routes/knowledge.py:208` `GET /v1/graph/entities` 既无 `require_role`，`_list_entity_records`（`:234`）调用 `lister(limit=, offset=)` **不传 tenant_id**，而 `memory_graph.list_entities`（`memory_graph.py:155`）是支持 `tenant_id` 的 —— 能力存在但未使用。 |
| 反向存在性泄漏 | `routes/knowledge.py:188-190`：他租户的 query_id → 404；**不存在**的 query_id → 200。与 `:177` 注释「404 for both missing and cross-tenant (no existence leak)」相反，可用作存在性探测。 |

#### 🟡 BL-10：静默吞异常导致「成功」语义失真

| 位置 | 行为 | 后果 |
|------|------|------|
| `knowledge_upload.py:68-71` | `docs.save()` 失败 `pass` | 文档未落盘，任务仍报 `queued` 并列出该 doc；注释称「任务仍记录失败尝试」，但实际**未记录任何失败信息** |
| `service_query.py:278-282` | `audit_store.save()` 失败 `pass` | 合规审计链可静默缺失 |
| `ingest_worker.py:88-96` | 逐条 embed 失败仅 warning | 全部 embedding 失败时 `result.error` 仍为 None → 任务标记 `done`，实际 0 条入索引 |
| `incremental.py:255` | `delete_relation` 失败 `pass` | AUTO_UPDATE 后旧边残留，形成双事实 |

#### ⚪ BL-11：声明但无生产者的分支 / 计数不闭合

| 项 | 证据 |
|----|------|
| `IngestStatus.REVIEW` 无生产者 | `ingest_tasks.py:18` 声明，`_ALLOWED_TRANSITIONS` 允许 `EXTRACTING→REVIEW`，但全仓库无任何 `transition(..., REVIEW)` 调用 |
| `ConflictAction.SKIP` 无生产者 | `incremental.py:28` 声明；`_decide()`（`:178`）只返回 AUTO_UPDATE / REVIEW / KEEP_OLD |
| `KEEP_OLD` 不计数 | `_collect_writes`（`:217`）只处理 AUTO_UPDATE 与 REVIEW；KEEP_OLD 既不写库也不计入 `BatchResult` 任何字段 → 批次数字不守恒 |
| 值冲突只取第一条 | `incremental.py:148` `old = value_conflicts[0]` —— 存在多条冲突边时其余被静默忽略，`_retire_conflicting_edge` 无法兑现「不留双事实」 |
| `accepted` 计数可虚高 | `incremental.py:205` `stats.get("relations", 0) or len(to_write)` —— 底层返回 0 时回退为「计划写入数」，0 写入被报成成功 |
| `persist_embeddings` 名不符实 | `cache.py:227-233` 写入的是 `{"size","hits","misses"}` 统计，**不是 embedding**，且无任何读回路径 |
| 流式缺 `request_id` | `service_stream.py:195` 调 `_finalize_chain` 未传 `request_id`（同步路径 `service_query.py:66` 传了）→ 流式审计记录 `request_id` 恒为空 |
| 流式 triage 帧不保证 | `loop_stream.py:106` 注释「clients always see a triage frame first」，但 `:107` 仅在 `force_agentic` 时发帧；`enable_triage=False` 且非 force 时无 triage 帧 |
| `_TASKS` 无界增长 | `routes/knowledge.py:22` 模块级全局 dict，只增不删，无淘汰与保留策略 |
| 复核决策非幂等 | `queue.py:150` 重复提交会覆盖 `status`/`reviewer`/`decided_at`，无终态保护 |
| 加载健壮性不对称 | `queue.py:89` `json.loads` 无 try/except（`ingest_tasks.py:116` 有）→ 单行损坏即导致 `QueryService` 初始化抛异常 |
| 上传复核置信度硬编码 | `routes/knowledge.py:68` 每次上传都以 `confidence=0.5` 入队 SPOTCHECK，非业务规则而是占位值 |

---

#### 补充审查（第二轮：设计对差）

> 以下三条来自净室设计文档与实现的逐条对差，**未经对抗式复核**。均已给出 `file:line` 证据，但落地前应按 §5 的 V-16…V-19 实测确认。

#### 🟠 BL-12：Planner 产出 DAG，Executor 按线性序执行；hop 预算与子问题数被混同

| 项 | 内容 |
|----|------|
| 证据（并行语义未兑现） | `plan_dag.py:124 ready_subquestions()` —— 返回「全部依赖已满足的待执行节点」，即并行分支 API —— 在 `planner.py:16,32` 被再导出、在 `tests/unit/test_planner_dag.py:44,51` 有单测，但**全仓库无任何运行期调用点**。Agent 循环改用 `current_index` 线性推进（`loop_runtime.py:122,162`；`loop_handlers.py:70,81,151,213`）。 |
| 证据（无并发能力） | 全仓库 `async def` 共 11 处且**全部位于 `api/`**；`agent/` 与 `retrieval/` 内无 `asyncio.gather` / `TaskGroup` / `to_thread`。 |
| 证据（hop ≡ 已执行子问题数） | `loop_runtime.py:110` —— `node_executor` 每次进入即 `guards.on_hop_start()`（`guardrails.py:128` `hop += 1`），而每次进入只处理 `idx` 指向的**一个**子问题（`:122-146`）。故 hop 计数等价于「已执行子问题数」，并非推理深度。 |
| 证据（子问题数无上限） | `planner.py:58-60` 对 LLM 返回的 `result.sub_questions` **不截断**，`normalize_plan`（`plan_dag.py:47`）亦不截断；`loop_handlers.py:249` 还会在运行期**插入**动态子问题，使总数进一步增长。 |
| 失败场景 | `configs/default.yaml` `max_hops: 4`。LLM planner 产出 6 个子问题（或产出 4 个后 critic 追加 2 个）→ 第 4 次 executor 后 `loop_handlers.py:64` `hop >= max_hops` 置 `done`（`loop_runtime.py:240` 同判据）→ 第 5、6 个子问题**永不执行且无告警**。护栏文案 `guardrails.py:134` 报 `max_hops exceeded (5/4)`，读作「推理太深」，实际触发原因是「子问题太多」。 |
| 影响 | ① 深度（多跳）与广度（子问题数）共用同一预算，两者不可独立调参，也无法从日志区分是哪一种超限；② DAG 的 `depends_on` 在运行期只影响拓扑排序**顺序**，其并行语义（同层节点可同时执行）未兑现 —— 互不依赖的分支各自消耗一个 hop，等于把广度计入深度预算；③ 延迟为 Σ(分支) 而非 max(分支)，对 AC-4（8s）不利。 |
| 备注 | 属**能力未兑现**，非死循环 —— §2「读路径闭环」结论不受影响：超预算时仍走 answer 节点产出 `ReasoningChain`。修复面小于新增功能：并行 API 与其单测已存在，缺的是运行期调用与并发载体。 |

#### 🟠 BL-13：引用门禁与 fabrication 指标只到「词面重叠」，无法判别关系是否相符

| 项 | 内容 |
|----|------|
| 门禁实为三级 | `citations.py:100-108`：① `claims_have_citations` 每条 claim 有 `evidence_ids`；② `claims_bind_to_evidence` 引用 id 必须存在于检索集合；③ `claims_lexically_supported`（`:116`，`min_overlap=1`）claim 与被引证据**至少共享 1 个内容词**。函数自陈（`:98`）：*"not a full NLI check — still better than ID-only fabrication=0"* —— 设计者已知这是代理判据。 |
| 假阳性（本条） | `min_overlap=1` 可仅靠**主语实体**满足，与关系类型无关。证据 `Apex Holdings -[FOUNDED_BY]-> 李四`、claim `Apex Holdings 的 CEO 是张三` → 交集 {apex, holdings} ≥ 1 → **通过门禁**。即：引用一条真实、已检索、但**断言了另一种关系**的证据，门禁无法拦截。 |
| 与 BL-02 互为反面 | BL-02 是同一函数的**假阴性**方向（纯中文 claim 因空白分词恒失败）。中文侧过严（必然拒绝），含拉丁 token 侧过松（仅需 1 词重叠）。**注意：修复 BL-02 的 CJK 切分会加剧本条** —— 切出 unigram/bigram 后，中文 claim 更容易凑满 1 个重叠 token 而蒙混通过。两条应一并设计，不宜先后单独修。 |
| 指标口径比门禁更弱 | `eval/metrics_evidence.py:145 fabrication_rate` 的判据 `_claims_unbound`（`:175`）**只检查 `evidence_ids` 是否非空** —— 既不校验 id 是否在检索集合内，也不做词面重叠，是三级门禁中最弱的一级。docstring（`:146`）自陈为「AC-7 proxy」，但指标名 `fabrication_rate` 读作「捏造率」。 |
| 失败场景 | 在线路径：模型引用真实 id 但误读其内容 → 门禁放行 → 输出错误答案且附带「看起来有据可依」的引用。评测路径：该行 `_claims_unbound` 返回 False → 计为「未捏造」→ `fabrication_rate` 系统性偏低。 |
| 影响 | AC-7「无据不答」的验收证据强度低于指标名义。若要真正判别关系相符需引入蕴含判定（每 claim 一次轻档 LLM 调用，或小型 NLI 模型），成本需单独评估；在此之前，至少应让指标名与实际判据一致。 |

#### 🟡 BL-14：图谱无时间维度，时效性冲突按置信度而非时间裁决

| 项 | 内容 |
|----|------|
| 证据 | 全仓库 `src/` 内 `valid_from` / `valid_to` / `valid_at` / `as_of` / `temporal` **零命中**。`graph_builder.py:39` 的 source 结构为 `{doc_id, chunk_id, span, confidence}`，实体与关系均无时间字段。 |
| 冲突按置信度裁决 | `graph_builder.py:53` `if rid not in relations or t.confidence > relations[rid].confidence:` —— 同一 `rid` 的重复关系保留**置信度更高**者。置信度来自抽取阶段，与事实新旧**无关**。 |
| 失败场景 | 语料同时含 2019 与 2025 年的 CEO 公告 → 两条 `CEO_OF` 边归一到同一 `rid` → 保留抽取置信度更高的一条（可能恰是旧闻，因其表述更规范）→ 图检索稳定返回过期答案，且 `ReasoningChain` 会为其附上**真实引用**，人工复核前不可见。 |
| 与增量路径同源 | BL-11「值冲突只取第一条」（`incremental.py:148`）与本条是同一个根因：数据模型只能表达「二选一」，无法表达「两者都对，分属不同时间区间」。`_retire_conflicting_edge` 因此只能删除旧边，历史丢失。 |
| 影响 | 对当前静态演示语料无实际损害；对任何有更新节奏的真实语料，这是**先于 BL-01 需要决策**的数据模型问题 —— 补时间字段属 schema 变更（`configs/schema/domain_v0.yaml` + 抽取提示 + 路径打分），成本显著高于补一个执行器。 |

---

