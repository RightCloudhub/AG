# 架构说明（ARCHITECTURE）

> 描述**当前代码已实现**的模块边界与运行路径，每个模块配一张架构图。目标设计见 [`design/ARCHITECTURE_DESIGN.md`](./design/ARCHITECTURE_DESIGN.md)，业务规则见 [`design/BUSINESS_LOGIC.md`](./design/BUSINESS_LOGIC.md)。

- [0. 总览](#0-总览)
- [1. 接入层 api / cli / web](#1-接入层-api--cli--web)
- [2. Agent 编排 agent](#2-agent-编排-agent)
- [3. 检索 retrieval](#3-检索-retrieval)
- [4. 知识图谱 knowledge](#4-知识图谱-knowledge)
- [5. 存储 stores](#5-存储-stores)
- [6. LLM 网关 llm](#6-llm-网关-llm)
- [7. 答案生成 generation](#7-答案生成-generation)
- [8. 可观测与治理 observability](#8-可观测与治理-observability)
- [9. 评测 eval](#9-评测-eval)
- [10. 查询全链路时序](#10-查询全链路时序)
- [11. 配置与运行模式](#11-配置与运行模式)

---

## 0. 总览

```mermaid
flowchart TD
    subgraph ENTRY["接入层"]
        WEB["web/ Vue 3 工作台"]
        API["api/ FastAPI /v1/*"]
        CLI["cli/ agr-*"]
    end
    subgraph APP["应用层"]
        SVC["api/service QueryService"]
        AGENT["agent/ 分诊 · Fast Path · LangGraph 循环"]
        GEN["generation/ 答案 · 引用 · ReasoningChain"]
    end
    subgraph DOMAIN["领域层"]
        RET["retrieval/ 向量 · 图 Beam · BM25 · RRF"]
        KG["knowledge/ 接入 · 抽取 · 校验 · 消歧 · 增量 · 复核"]
    end
    subgraph INFRA["基础设施"]
        STORES["stores/ Protocol + factory"]
        LLM["llm/ Provider · 预算 · 熔断"]
        OBS["observability/ 日志 · trace · 指标 · 审计"]
    end
    WEB --> API --> SVC --> AGENT
    CLI --> AGENT
    CLI --> KG
    AGENT --> RET --> STORES
    AGENT --> GEN
    KG --> STORES
    AGENT -.-> LLM
    KG -.-> LLM
    GEN -.-> LLM
    OBS -.-> API
    OBS -.-> AGENT
    STORES --> BACK[("InMemory / File / Neo4j / Qdrant")]
```

依赖方向自上而下：上层只依赖下层的 **Protocol**（`stores/interfaces.py`、`llm/interfaces.py`），具体后端只在 `stores/factory.py` 组合根中装配。`langgraph` 依赖限定在 `agent/` 内。

| 目录 | 职责 | 关键文件 |
|---|---|---|
| `api/` | HTTP 接入、鉴权限流、RBAC、SSE、应用服务 | `app.py` `auth.py` `rbac.py` `service*.py` `routes/` |
| `cli/` | `agr-*` 命令行入口 | `ingest.py` `index_cmd.py` `graph_cmd.py` `cases_run.py` |
| `agent/` | 分诊、Fast Path、Planner/Executor/Critic 状态机、护栏、记忆 | `loop.py` `loop_runtime.py` `triage.py` `planner.py` `executor.py` `critic.py` `guardrails.py` |
| `retrieval/` | 三路检索、图 Beam、RRF 融合、缓存 | `graph.py` `graph_beam.py` `fusion.py` `cache.py` `contracts.py` |
| `knowledge/` | 文档接入、三元组抽取、Schema 校验、实体消歧、增量更新、复核队列 | `extract_pipeline.py` `schema_check.py` `resolution.py` `incremental.py` `review/queue.py` |
| `stores/` | 存储协议与实现 | `interfaces.py` `factory.py` `memory_graph.py` `neo4j_store.py` `vector_store.py` |
| `llm/` | Provider 抽象、结构化输出、三级预算、熔断 | `provider.py` `structured.py` `budget*.py` `circuit.py` |
| `generation/` | 答案生成、引用绑定、置信度、推理链、审计存储 | `answer.py` `citations.py` `trace.py` `audit_store.py` |
| `observability/` | JSON 日志、trace、Prometheus、安全审计、PII 脱敏、OTel | `logging_setup.py` `metrics.py` `audit_events.py` `redaction.py` |
| `eval/` | 金标生成、评分、基线、坏例归因 | `gold_gen.py` `scoring.py` `baseline_rag.py` `badcase.py` |

---

## 1. 接入层 api / cli / web

```mermaid
flowchart LR
    C["客户端 / Web 工作台"] --> MW["AuthRateLimitMiddleware<br/>API Key → Principal(tenant, user, role)<br/>QPS · 并发限流"]
    MW --> R{"routes/"}
    R --> RQ["query.py<br/>/v1/query · /v1/query/stream"]
    R --> RK["knowledge*.py<br/>/v1/docs · /v1/ingest-tasks · /v1/graph/entities"]
    R --> RW["workspace*.py<br/>/v1/me · /v1/review-queue · /v1/feedback"]
    R --> RA["admin.py<br/>/v1/metrics · /v1/traces · /v1/budget · /v1/audit-events"]
    RQ --> SVC["QueryService<br/>答案缓存 · 三级预算 · 审计 · 复核"]
    RK --> TASK["IngestTaskStore"]
    RW --> SVC
    RA --> OBS["observability/"]
    SVC --> ENV["envelope.py 统一响应 {data, meta, error}"]
    SVC --> SSE["sse.py 增量事件流"]
```

- **鉴权与租户**：`auth.py` 解析 `AGR_API_KEYS=tenant:key:role`，`rbac.py` 提供 admin / operator / reader 三角色路由守卫。
- **QueryService**（`service.py` + `service_query.py` / `service_stream.py`）：查答案缓存 → 预留预算 → 调用 Agent → 落审计 → 结算预算；流式请求经 `loop_stream.py` 逐 hop 推送事件。
- **上传**：`POST /v1/docs` 创建任务（UTF-8 md/txt，≤5 MiB/文件，≤20/批）；worker（`AGR_INGEST_INLINE=1` 时在 API 进程内运行）完成分块索引并抽取三元组入图，冲突进入复核队列，决策由复核执行器作用于图谱（见 §4.4）。
- **web/**：Vue 3 ESM 零构建，`api.js` 调用上述接口，视图位于 `static/js/views/`（chat / knowledge / review / graph / ops）。

---

## 2. Agent 编排 agent

### 2.1 入口与分诊

```mermaid
flowchart TD
    Q["问题"] --> CC{"chitchat.py<br/>问候 / 能力询问?"}
    CC -->|是| DIRECT["直接回答，不检索"]
    CC -->|否| TR{"triage.py<br/>规则前端 + 可选轻量 LLM"}
    TR -->|简单单实体事实| FP["fast_path.py<br/>单轮检索 + 生成"]
    TR -->|多跳 / 复杂| AG["LangGraph 循环"]
    FP --> ESC{"loop_policy.py<br/>证据不足?"}
    ESC -->|是，升级一次| AG
    ESC -->|否| OUT["ReasoningChain"]
    AG --> OUT
```

### 2.2 LangGraph 状态机

```mermaid
stateDiagram-v2
    [*] --> planner
    planner --> executor: 子问题 DAG<br/>cap_plan_breadth
    executor --> critic: 执行就绪子问题
    executor --> answer: 护栏触发 / 计划耗尽
    critic --> executor: next_hop / rewrite
    critic --> answer: sufficient / give_up
    answer --> [*]
```

| 节点 | 实现 | 说明 |
|---|---|---|
| planner | `planner.py` `plan_dag.py` `planner_patterns.py` | 问题 → 带依赖的子问题 DAG，`{from:sqN}` 占位由上游结论物化；广度受 `max_sub_questions` 限制，被裁剪项写入 metadata |
| executor | `executor.py` `executor_plan.py` `executor_dispatch.py` `parallel_branches.py` | 为子问题选择工具（graph_neighbors / graph_path / graph_subgraph / vector_search / fulltext_search），并行执行后 RRF 融合；就绪且互不依赖的 DAG 节点并发检索、共享同一 hop |
| critic | `critic.py` `critic_offline*.py` `critic_guard.py` | 子问题级 + 全局两级判断：`sufficient` / `next_hop` / `rewrite` / `give_up` |
| answer | `generation/answer.py` | 引用绑定生成答案，失败重试一次后走保守回答 |

### 2.3 运行时支撑

```mermaid
flowchart LR
    RT["AgentRuntime<br/>loop_runtime.py"] --> MEM["MemoryState<br/>证据去重 · 排除假设 · 路径防环"]
    RT --> GR["Guardrails<br/>hop · 广度 · LLM 调用 · token · 超时"]
    RT --> CP["Checkpointer<br/>内存 / SQLite"]
    RT --> REC["loop_recover.py<br/>GraphRecursionError 恢复"]
    RT --> STR["loop_stream*.py<br/>stream(updates) → SSE 事件"]
    GR --> BUD["llm/BudgetTracker"]
    RT --> REG["tools/registry.py<br/>外部工具注册（预留）"]
```

hop 计数的是**依赖深度**：被并发预取的兄弟节点不再消耗 hop，广度由 `max_sub_questions` 单独限制。默认护栏（`configs/default.yaml`）：`max_hops=4`、`max_sub_questions=4`、`max_llm_calls=16`、`max_tokens_per_query=50000`、`query_timeout_seconds=45`、`recursion_limit=15`。

---

## 3. 检索 retrieval

```mermaid
flowchart TD
    SQ["子问题 + 实体提示"] --> PAR{"并行 parallel=true"}
    PAR --> V["VectorRetriever<br/>vector.py · top_k=10"]
    PAR --> G["GraphRetriever<br/>graph.py"]
    PAR --> F["FulltextRetriever<br/>fulltext.py · BM25"]
    G --> BEAM["BeamExpander graph_beam.py<br/>逐层扩展 · beam_width=20<br/>高度数节点过滤 >30"]
    BEAM --> REL["graph_relation.py<br/>关系意图推断 · 相关度阈值 0.12"]
    BEAM --> PATH["graph_paths.py<br/>路径打分 → Candidate"]
    V --> FUS["fusion.py<br/>RRF k=60 + Reranker 协议"]
    PATH --> FUS
    F --> FUS
    FUS --> CACHE["cache.py RetrievalCache<br/>key = 租户 + 索引版本 + 查询"]
    CACHE --> CAND["Candidate[]<br/>contracts.py 统一候选契约"]
```

- `contracts.py` 定义 `Candidate` / `Citation` / `CandidateSource`，三路结果统一为同一结构，`rrf_fuse` 按排名融合。
- `IndexVersion` 在图、向量、全文更新后递增，使缓存自然失效；缓存键包含 `tenant_id`，保证租户隔离。

---

## 4. 知识图谱 knowledge

### 4.1 领域 Schema（`configs/schema/domain_v0.yaml`）

```mermaid
erDiagram
    Person ||--o{ Company : "CEO_OF / WORKS_AT / WORKED_AT / FOUNDED"
    Company ||--o{ Company : "PARENT_OF / SUBSIDIARY_OF / ACQUIRED / PARTNER_OF / COMPETES_WITH / SUPPLIES"
    Company ||--o{ Product : "PRODUCES / SUPPLIES_FOR"
    Company ||--o{ Event : "PARTICIPATED_IN"
    Person ||--o{ Event : "PARTICIPATED_IN"
    Company ||--o{ Location : "LOCATED_IN"
    Person ||--o{ Location : "LOCATED_IN"
    Event ||--o{ Location : "LOCATED_IN"
```

### 4.2 构建管线（CLI 离线建图）

```mermaid
flowchart LR
    DOC["data/raw/*.md"] --> ING["ingest.py<br/>加载 + chunk<br/>1200 字 / 重叠 150"]
    ING --> EXT["extract_pipeline.py<br/>逐块 LLM 抽取"]
    EXT -->|journal 断点续跑| EXT
    EXT -->|多次失败| QUA[("quarantine")]
    EXT --> GATE["schema_check.py<br/>类型校验 + 置信度 ≥0.5"]
    GATE -->|拒绝| REJ[("rejected_triples")]
    GATE --> RES["resolution.py<br/>规则归一 → 相似度候选 → LLM 判定"]
    RES -->|不确定| RQ["review/queue.py<br/>人工复核"]
    RES --> INC["incremental.py<br/>冲突检测"]
    INC --> GS[("GraphStore")]
    ING --> IDX["index_cmd<br/>embedding + BM25"]
    IDX --> VS[("Vector / Fulltext")]
```

### 4.3 增量冲突决策（`incremental_conflicts.py`，只在本租户范围内比较）

```mermaid
flowchart TD
    T["新三元组（已打租户标）"] --> F{"同一条边?"}
    F -->|是| U["置信度显著提升才更新"]
    F -->|否| K{"单值侧有其他值?<br/>schema functional_on"}
    K -->|否 / 多值关系| W["直接写入"]
    K -->|是| TM{"双方都带日期?"}
    TM -->|是| TS["SUCCEED / HISTORICAL<br/>旧事实设 valid_to"]
    TM -->|否| D{"new ≥ old + margin?"}
    D -->|是| AU["AUTO_UPDATE<br/>写新边，退役旧边"]
    D -->|"new > old"| RV["REVIEW → 复核队列"]
    D -->|否| KO["KEEP_OLD"]
```

### 4.4 运行期写入路径（上传 → 图谱 → 复核）

```mermaid
flowchart LR
    UP["POST /v1/docs"] --> TS["ingest_tasks.py<br/>queued + spotcheck 复核项"]
    TS --> WK["ingest_worker.py<br/>API 内联（AGR_INGEST_INLINE=1）<br/>或 python -m ...ingest_worker"]
    WK --> IX[("Vector + BM25")]
    WK --> GI["graph_ingest.py<br/>LLM 抽取 | extract_rules.py 离线抽取"]
    GI --> UPD["IncrementalUpdater<br/>本租户 · 基数 · 时间 · 置信度"]
    UPD --> GS[("GraphStore")]
    UPD -->|"需人工"| RQ["ReviewQueue"]
    RQ --> DEC["/v1/review-queue/{id}/decision"]
    DEC --> EXE["review/executor.py<br/>写入 · 退役 · 合并 · 回滚"]
    EXE --> GS
    EXE --> DONE["批次结清 → 任务 review → done"]
    UPD --> CM["on_graph_commit<br/>缓存失效 + 实体词表刷新"]
    EXE --> CM
```

> 离线模式下图谱与向量在进程内存，应使用 API 内联 worker；在线模式（Neo4j/Qdrant）独立 worker 与 API 共享存储。

### 4.5 时间维度（`knowledge/temporal.py`）

关系可带 `valid_from` / `valid_to`（ISO 年 / 年-月 / 日期）。同一条边重复出现时较新日期优先；单值关系出现新值且双方都带日期时，按时间裁决（`SUCCEED` / `HISTORICAL`），旧事实写入 `valid_to` 而非删除。检索时已结束的事实降权（问题含「以前 / 曾 / previously」时不降），证据文本标注区间，如 `[2010–2016]`。

---

## 5. 存储 stores

```mermaid
classDiagram
    class GraphStore {
        <<Protocol>>
        upsert_entities()
        upsert_relations()
        get_entity_by_name()
        neighbors()
        paths()
        counts()
    }
    class VectorStore {
        <<Protocol>>
        ensure_collection()
        upsert()
        search()
    }
    class FulltextStore {
        <<Protocol>>
        index()
        search()
    }
    class DocStore {
        <<Protocol>>
        save()
        get()
        list_ids()
    }
    GraphStore <|.. InMemoryGraphStore
    GraphStore <|.. Neo4jGraphStore
    VectorStore <|.. InMemoryVectorStore
    VectorStore <|.. QdrantVectorStore
    FulltextStore <|.. BM25FulltextStore
    DocStore <|.. InMemoryDocStore
    DocStore <|.. FileDocStore
    class StoreBundle {
        graph
        vector
        fulltext
        docs
    }
    StoreBundle o-- GraphStore
    StoreBundle o-- VectorStore
    StoreBundle o-- FulltextStore
    StoreBundle o-- DocStore
```

`factory.py` 提供 `create_offline_bundle()`（内存图 + 进程内向量 + BM25 + 文件 DocStore）与 `create_live_bundle()`（Neo4j + Qdrant，Qdrant 初始化失败回退内存向量）。`neo4j_codec.py` / `neo4j_queries.py` 负责序列化与 Cypher 生成；`scheduling_protocols.py` 定义限流、预算、审计存储协议。所有读写都带 `tenant_id` 过滤。

---

## 6. LLM 网关 llm

```mermaid
flowchart LR
    CALLER["planner / critic / answer / extract / triage"] --> ST["structured.py<br/>JSON 结构化输出 + 校验重试"]
    ST --> BT{"BudgetTracker<br/>单查询预算"}
    BT -->|超限| BE["BudgetExceeded"]
    BT --> BP["budget_policy.py<br/>租户 / 用户 / 查询 三级窗口"]
    BP --> CB{"CircuitBreaker<br/>连续 2 次失败 → OPEN<br/>20s 后 HALF_OPEN"}
    CB -->|CLOSED| PR["provider.py LLMProvider"]
    PR --> STRONG["Tier.STRONG<br/>gpt-4.1"]
    PR --> LIGHT["Tier.LIGHT<br/>gpt-4.1-mini"]
    PR --> MOCK["MockLLMProvider<br/>离线默认"]
```

---

## 7. 答案生成 generation

```mermaid
flowchart TD
    EV["融合证据 Candidate[]"] --> MODE{"allow_llm?"}
    MODE -->|是| LLMA["answer.py<br/>LLM 生成 claims + evidence_ids"]
    MODE -->|否| OFF["offline_answer.py<br/>offline_heuristics/ 确定性规则"]
    LLMA --> CIT["citations.py + claim_support.py<br/>引用 · 绑定 · 词面+锚定<br/>relation_consistency.py 关系一致"]
    CIT -->|通过| NLI["entailment.py<br/>LIGHT 档蕴含判定"]
    NLI -->|不蕴含| RETRY
    CIT -->|失败| RETRY["按失败原因重生成一次"]
    RETRY --> CIT
    CIT -->|仍失败| SAFE["保守回答 / 拒答"]
    NLI -->|蕴含 / 判定器弃权| CONF["confidence.py 置信度分级"]
    OFF --> CONF
    SAFE --> CONF
    CONF --> CHAIN["trace.py ReasoningChain<br/>steps · claims · cost · metadata"]
    CHAIN --> AUD[("audit_store.py")]
```

`ReasoningChain` 契约见 [`configs/schema/reasoning_chain_v1.json`](../configs/schema/reasoning_chain_v1.json)。`offline_heuristics/` 只服务演示语料，保证 CI 可复现，不是生产路径。

---

## 8. 可观测与治理 observability

```mermaid
flowchart LR
    REQ["请求上下文<br/>request_id · tenant · user · query_id"] --> LOG["logging_setup.py<br/>structlog JSON 日志"]
    REQ --> TR["trace.py<br/>逐 hop trace"]
    REQ --> MET["metrics.py<br/>/v1/metrics · /metrics-prom"]
    SEC["鉴权失败 · 预算超限 · 越权"] --> AE["audit_events.py<br/>安全事件流"]
    LOG --> RED["redaction.py PII 脱敏"]
    TR --> OT["otel_bridge.py<br/>OTLP + W3C traceparent（可选）"]
    AE --> RET["retention.py 保留期清理"]
```

---

## 9. 评测 eval

```mermaid
flowchart LR
    SEED["seed / pilot 三元组"] --> GG["gold_gen.py + gold_templates/<br/>2hop · 3hop · open · no_answer · guardrail"]
    GG --> DS[("evals/datasets/*.jsonl")]
    DS --> RUN["cases_run.py<br/>agr-run-cases"]
    DS --> BASE["baseline_rag.py<br/>纯向量 RAG 对照"]
    RUN --> SC["scoring.py + metrics*.py<br/>containment · 别名 · 证据命中"]
    BASE --> SC
    SC --> REP["report.py → reports/"]
    SC --> BAD["badcase.py 坏例归因"]
```

---

## 10. 查询全链路时序

```mermaid
sequenceDiagram
    autonumber
    participant U as Client
    participant M as AuthMiddleware
    participant S as QueryService
    participant A as Agent
    participant R as Retrieval
    participant G as Generation
    participant D as AuditStore
    U->>M: POST /v1/query
    M->>S: Principal(tenant, user, role)
    S->>S: 答案缓存命中? / 预留三级预算
    S->>A: run_query(question)
    A->>A: chitchat → triage
    loop 每个 hop（≤ max_hops）
        A->>R: 子问题 → 三路检索
        R-->>A: RRF 融合候选
        A->>A: critic 判断
    end
    A->>G: 证据 → 答案
    G-->>A: ReasoningChain
    A-->>S: chain
    S->>D: 持久化推理链
    S->>S: 结算预算 · 记录指标
    S-->>U: envelope{data: chain}
```

---

## 11. 配置与运行模式

| 能力 | 离线（默认） | 在线（显式开启） |
|---|---|---|
| 图 | `InMemoryGraphStore` + `data/processed/seed_triples.jsonl` | Neo4j（`AGR_USE_LIVE_STORES=1`） |
| 向量 | 进程内 + 已持久化 embedding | Qdrant |
| 全文 / 文档 | BM25 + 文件 DocStore | 同左 |
| LLM | `MockLLMProvider` + 离线规则 | `AGR_ALLOW_LLM=1` + `LLM_API_KEY` |

两个开关相互独立。应用参数在 `configs/default.yaml`，由 `config.py` / `config_enterprise.py` 解析；密钥与端点走 `.env`。部署与变量清单见 [`ops/RUNBOOK.md`](./ops/RUNBOOK.md)，Neo4j 外部运行时见 [`ops/EXTERNAL_RUNTIMES.md`](./ops/EXTERNAL_RUNTIMES.md)。
