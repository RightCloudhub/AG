# 基于代码的项目评审报告  
## AgenticGraphRAG（图谱增强多跳推理问答）

**评审范围**：`src/agentic_graphrag/**`、`tests/unit/**`、默认配置加载与 seed 数据驱动的离线执行路径  
**评审日期**：2026-07-20  
**口径**：实现即事实；未实现 = 不存在；注释/命名不作能力证明  
**说明**：本报告只依据源码与可运行行为，不引用 PRD/立项/评估文档。

---

## 一、执行摘要

代码层面，这是一个**真实可运行的 Agent 闭环 POC**，不是空壳：LangGraph 接线了 Planner → Executor → Critic → Answer；图检索有 beam/路径与爆炸防护参数；三路检索有统一 `Candidate` 契约；推理链有结构化模型与引用 ID 门禁；抽取有 Schema 门禁与 journal/quarantine；评测有 baseline、scoring、badcase；单测 **125 passed**。

但核心能力在**离线确定性路径**上高度耦合「合成公司关系域 + 英文模式匹配 + 规则答案器」：

| 组件 | 离线真实行为 |
|---|---|
| Planner | 正则/关键词模板拆子问题（CEO/parent/supplier 等） |
| Critic | 有图证据即 `sufficient`；有剩余 DAG 节点则只推进子问题 |
| Answer | `offline_heuristics` 按边类型与问题关键词抽答案 |
| 评测金标 | 由边模板生成（`gold_templates`）+ 同构图 |

因此：**“多跳在 seed 图上能答对”已被代码证明；“企业制度/合同场景可用 / live LLM 可靠 / 可上生产”未被代码证明。**

**现场复现问题（代码缺陷）**：多跳 placeholder 物化时，Critic 的 `partial_answer` 取的是**整条边描述字符串**，导致第二跳子问题变成：

`Who is the CEO of Apex Holdings -[PARENT_OF]-> BrightLink Logistics (Company)?`

最终仍答出 `Elena Varga`，依赖的是**规则答案器兜底**，不是干净的子问题链推理。这说明 offline 准确率**会高估**“规划-检索-反思”质量。

**综合判定**：**技术骨架值得继续投入；当前代码成熟度仅支持受控 POC / 研发验证，不支持企业生产立项。**  
**代码评审总分：61.0 / 100**

---

## 二、总体评分（代码口径）

| 评估维度 | 权重 | 得分(0–5) | 加权 | 代码依据要点 |
|---|---:|---:|---:|---|
| 1. 业务价值与场景匹配 | 15 | 2.5 | 7.5 | 实现域=公司/人员/产品/事件关系；无制度/合同/工单对象模型 |
| 2. 问题定义与技术路线 | 10 | 4.0 | 8.0 | 架构分层清晰，针对多跳而非聊天 bot |
| 3. 知识图谱建设能力 | 15 | 2.5 | 7.5 | Schema 门禁+抽取管线有；无消歧/对齐/增量/审核 UX |
| 4. 多跳推理与复杂问答 | 15 | 3.5 | 10.5 | beam/路径可跑；placeholder 物化脏；条件/否定等无专门逻辑 |
| 5. Agent 决策能力 | 10 | 3.5 | 7.0 | 闭环真实；offline Critic 过浅；无分诊 |
| 6. 检索与证据整合 | 10 | 3.0 | 6.0 | 三路可调；Executor 用 concat，RRF 未接入主路径 |
| 7. 可审计推理链与可信输出 | 10 | 3.5 | 7.0 | 链结构完整；引用只校验 ID 存在；无持久化审计 API |
| 8. 企业安全/权限/合规 | 5 | 0.5 | 0.5 | API 无鉴权；检索无 ACL；无脱敏 |
| 9. 工程化、性能与成本 | 5 | 3.0 | 3.0 | 抽象/测试好；query_timeout 未真正熔断 |
| 10. 风险、成熟度与落地 | 5 | 3.5 | 3.5 | 护栏与评测有；仍是 Conditional-Go 级 POC |
| **合计** | **100** | — | **61.0** | — |

**评分说明**：加权得分 = 维度得分 / 5 × 权重。

---

## 三、代码架构事实（实现了什么）

### 1. Agent 编排（已实现）

```
planner → executor → critic ─┬→ executor（未 done）
                             └→ answer → END
```

- 入口：`agent/loop.py` → `run_agentic_query`
- 节点：`agent/loop_runtime.py`（`AgentRuntime`）
- 状态：`AgentState` + `MemoryState` 快照进 checkpointer
- 护栏：`max_hops`、`max_llm_calls`、`max_tokens`、`recursion_limit`（`guardrails.py` + `BudgetTracker`）

### 2. 检索（已实现）

- 图：`GraphRetriever` + `BeamExpander`（邻居扩展、路径、关系 cue 打分、高度数阈值）
- 向量：`VectorRetriever`
- 全文：BM25 `FulltextRetriever`
- 统一契约：`Candidate` / `Citation`
- **融合**：`concat_candidates` 在 Executor 主路径使用；`rrf_fuse` **有实现但未接入** Executor

### 3. 知识层（部分实现）

- 文档切分/接入：`knowledge/ingest.py`
- LLM 抽取 + journal/retry/quarantine：`extract_pipeline.py` / `extract_core.py`
- Schema 校验门禁：`schema_check.gate_triples`
- 入图：`graph_builder`（实体 ID = `sha1(type:name.lower())`）
- 确定性 pilot 三元组：`pilot_triples.py`（脚本宇宙硬编码，非 LLM 抽取）
- 存储：`InMemoryGraphStore` / `Neo4jGraphStore`（参数化 Cypher，防注入标识符校验）

### 4. 生成与推理链（已实现）

- `ReasoningChain`：子问题、工具调用、证据 ID、claims、status、cost
- 引用门禁：claims 必须带**存在于检索结果中的 evidence_id**；失败则 regenerate 一次，再 honest fallback
- offline 答案：`offline_heuristics.focused_extract`（领域硬编码模式）

### 5. API（最小实现）

- `POST /v1/query`、`/healthz`
- 默认 offline：seed 图 + MockLLM
- **无** auth 中间件、无 tenant、无 rate limit 实现体
- 注释写明：`force_agentic` 因无 triage 仅写 metadata

### 6. 评测（已实现）

- case schema、模板生成金标、dev/heldout 切分
- baseline 纯向量路径
- accuracy（token overlap / containment）
- badcase 四桶归因
- 单元测试覆盖主链路

---

## 四、关键发现（按严重度）

### 致命 / 高（企业落地）

#### F1. 无任何权限与安全边界

- `api/app.py` 仅挂 query 路由与校验错误处理
- 全库检索路径无 `user/role/acl` 参数，图邻居扩展**不会**按权限裁剪
- **结论**：接入真实内网语料即构成越权问答风险；**不可上生产**

#### F2. 图谱身份模型 = 字符串名哈希，无实体消歧/对齐

- `_entity_id(name, type) = sha1(f"{type}:{name.lower()}")`
- 同实异名、别名、错别字会生成不同节点；无 `resolution` 模块
- Neo4j 有 `aliases` 字段写入，但**无解析/合并逻辑**消费它
- **结论**：真实文档噪声下，多跳会沿错误边“自证正确”

#### F3. 评测闭环与答案器高度同构，数字不可外推

- 金标：`gold_templates` 从图边模板生成问题与答案
- 规划：`plan_offline` 英文关系句式模板
- 批评：offline 见 graph hit 即 sufficient
- 作答：`offline_heuristics` 按 PARENT_OF/CEO_OF/WORKED_AT 等抽答案
- **结论**：高 Accuracy 主要证明「确定性图 + 规则答」在同分布上可对齐，**不证明** Agent/LLM 多跳推理能力

#### F4. 子问题物化污染（已复现）

- Critic offline 把 `partial_answer` 设为第一条图证据 `content`（边描述）
- `materialize_subquestion` 把 `{from:sq1}` 替换成该字符串
- 第二跳问题脏化，工具参数依赖实体抽取从脏句中抠名字
- **结论**：推理链表面“有 2 hop”，实质可信度低于 step 数所暗示

### 中高（能力空洞）

#### F5. Critic「反思」在 offline 几乎不是反思

```text
if remaining_subquestions > 0 → SUFFICIENT（推进 DAG）
if graph_hits → SUFFICIENT（全局）
```

- 不校验证据是否回答子问题语义
- live 路径依赖 LLM structured 输出，**代码侧无充分性一致性校验**

#### F6. 无复杂度分诊，一律 Agentic

- `loop` 固定 agentic 入口
- `service.py`：`force_agentic` 仅 metadata
- 简单事实问也会走多工具 + 多跳预算路径 → 成本/延迟结构差

#### F7. RRF 与冲突裁决未进主路径

- `rrf_fuse` 存在
- Executor 固定 `concat_candidates`
- 无“图路径与向量段落冲突 → partial/并列”的裁决代码

#### F8. 引用门禁只验 ID，不验内容蕴含

- `validate_answered_claims`：有 evidence_id 且 id 在候选集即可
- **不能阻止**「引用了无关证据却编造结论」
- offline 还会把答案文本强绑到 top 证据 ID（`bind_claims_to_evidence`）

#### F9. `query_timeout_seconds` 未真正熔断

- 配置写入 `GuardrailConfig`，`status_text` 会打印
- agent 循环**无** wall-clock 超时检查；仅 hop/token/llm_calls
- API 的 `timeout_ms` 只改配置字段，不中断执行

### 中（工程债）

#### F10. 增量更新 / 版本 / 废止：未实现

- `load_triples_into_graph(..., clear_first=True)` 默认清空重灌
- 无废止时间、制度版本、冲突双版本保留逻辑

#### F11. 推理链无生产审计闭环

- 链在内存/响应 JSON；checkpointer 默认可 memory
- 无按 `query_id` 的审计查询 API、无业务可视化、无强制落库

#### F12. 领域硬编码渗透 offline 路径

- `offline_heuristics` 含 Apex/Helix/Meridian/Orion/Elena 等专名分支
- Planner 模式几乎全是英文公司关系句式
- **换企业中文制度语料，offline 路径基本失效**

---

## 五、分模块代码评价

### 5.1 知识图谱

| 能力 | 代码状态 | 评价 |
|---|---|---|
| Schema 定义与校验 | 有 | 类型/关系约束可拒入图 |
| LLM 抽取管线 | 有 | retry/journal/quarantine 工程完整 |
| 确定性 seed/pilot | 有 | 评测友好，生产不真实 |
| 实体消歧/对齐 | **无** | 致命缺口 |
| 冲突检测/审核队列 | **无**（仅 reject log） | 质量闭环未完成 |
| 增量更新 | **无** | clear_first 批处理心态 |
| 制度/合同/组织本体 | **无** | 仅 Company/Person/Product/Event/Location 系 |

**判断**：图谱作为“可查询结构”有雏形；作为“企业结构化记忆”未达标。

### 5.2 多跳检索

| 能力 | 代码状态 | 评价 |
|---|---|---|
| 多跳邻居 BFS/beam | 有 | 参数化剪枝，工程意识正确 |
| 路径查询 | 有 | store.paths + beam 回退 |
| 关系 cue 过滤 | 有 | 词表英文关系中心 |
| 路径爆炸防护 | 有 | layer limit / beam / high degree |
| 跨文档语义 | 弱 | 依赖 chunk 向量/BM25，与图协同浅 |

**判断**：在**已正确入图**的关系数据上，路径召回是系统最硬的能力之一。

### 5.3 Agent

| 组件 | 实现质量 | 风险 |
|---|---|---|
| Planner offline | 模板覆盖窄，但 DAG/depends_on/placeholder 设计对 | 开放问法退化成单跳 passthrough |
| Planner live | structured JSON | 依赖模型；失败回退 offline |
| Executor | 工具选择启发式合理；停用词实体过滤有 | LLM 工具选择异常吞掉后回退 |
| Critic offline | 过松 | 假阳性 sufficient |
| Memory | 去重/排除假设/路径记录 | 子串相似度去重可能误伤 |
| Guardrails | hop/token/calls 有效 | timeout 无效 |

**判断**：**编排骨架 > 决策智能**。当前更像“可配置的多工具循环 + 规则脑”，不是可靠的自主推理体。

### 5.4 可审计性

**有**：

- 每步 sub_question、tool、hits、critic_action
- claim ↔ evidence_id
- `answered|partial|no_answer` + honest_fallback 中文拒答文案
- cost 字段（calls/tokens/latency）

**缺**：

- 证据内容与 claim 文本一致性
- 原文 span 强制（图证据常只有 entity/relation id）
- 持久化与按 query 回放 API（checkpointer 不等价审计库）
- 业务可读路径（边字符串直接暴露给用户）

**判断**：**机器可追踪 > 合规可审计**。

### 5.5 相对纯向量 RAG

代码内有对等 baseline：`eval/baseline_rag.py`（单轮向量，无图无多跳）。  
在 seed 公司关系多跳题上，agentic+图路径**机制上**优于单轮向量（向量拼不出 PARENT→CEO 链）。  
但优势成立条件：

1. 边已正确在图中；  
2. 问题落在 offline 模板/启发式覆盖内；  
3. 评测同分布。

**代码不能支持**“企业知识全集全面优于向量 RAG”的结论。

---

## 六、企业场景适配（纯代码）

| 维度 | 代码结论 |
|---|---|
| 私域知识治理 | 弱：有接入/校验，无分级分类 |
| 权限控制 | **缺失** |
| 审计追踪 | 链结构有，落库/鉴权审计无 |
| 知识更新/废止 | **缺失** |
| 复杂组织/职责 | 无组织本体 |
| 多源冲突 | 无裁决 |
| 敏感数据 | 无脱敏 |
| 中文企业问法 | offline 路径基本未覆盖 |

**总评**：代码是 **「关系多跳技术底座 POC」**，不是 **「企业内部知识问答产品」**。

---

## 七、风险矩阵（代码推导）

| 风险 | 概率 | 影响 | 等级 | 代码触发点 |
|---|---|---|---|---|
| 权限泄露 | 上真实数据后高 | 极高 | **高** | 无 ACL |
| 图谱噪声被路径正当化 | 高 | 高 | **高** | 无消歧+路径检索 |
| 评测过拟合 offline | 已发生 | 高 | **高** | 模板金标+规则答案器同构 |
| Critic 假充分 | 中高 | 高 | **高** | offline 见边即过 |
| 成本失控 | 中 | 中高 | **中高** | 无分诊；timeout 未生效 |
| 子问题物化错误 | 已复现 | 中 | **中** | partial_answer=边文本 |
| 幻觉（live） | 中 | 高 | **中高** | 引用只验 ID |
| 运维/增量腐烂 | 中高 | 高 | **高** | clear 重灌、无版本 |

---

## 八、测试与可运行性

- **单测**：125 passed（约 2s）——工程健康度好  
- **有意义的多跳测**：`test_multihop_graph_evidence.py` 验证图证据进入链  
- **缺口**：无权限测试、无 live LLM 一致性测试、无真实语料回归、无超时熔断测试、无消融测试（去图/去 Agent）

---

## 九、落地建议（基于代码现状）

### 建议决议

**有条件继续研发（POC） / 不建议生产立项或采购交割。**

### 必须先修的代码债（按优先级）

| 优先级 | 项 | 验收 |
|---|---|---|
| P0 | 权限：检索前过滤 + 证据/邻居再过滤 | 无权限文档/节点永不进 chain |
| P0 | 子问题物化：结论必须是**实体名/规范答案**，禁止边字符串 | 第二跳问题可解析出合法 entity |
| P0 | Critic offline/live：充分性需对齐 gold 子答案或结构化检查 | 有边但未答子问题 → next_hop |
| P0 | 评测与答案器解耦：heldout + live；禁止仅依赖 focused_extract 报生产数字 | live heldout 报告独立 |
| P1 | 接入 RRF 或明确冲突策略；加规则分诊 Fast Path | 简单题不进多跳 |
| P1 | 实体别名/对齐最小闭环 | 同义不同名可合并或拒入 |
| P1 | 真正执行 `query_timeout` | 超时 → no_answer + 已探索路径 |
| P1 | claim 内容与证据重叠/蕴含检查 | 仅 ID 不足 |
| P2 | 推理链落库 + query_id 查询 | 审计 API |
| P2 | 增量更新与废止字段 | 旧关系可下线 |

### POC 范围（代码可支撑）

- **适合**：股权/任职/供应/竞品类**关系链**问答（与现有 schema、beam、模板同构）  
- **不适合直接做**：制度条款适用、审批责任、工单根因、合同义务（缺对象模型与解析）

### POC 验收（可写入决议，必须 live）

1. 真实授权语料（非 `pilot_triples` 硬编码）  
2. live LLM 路径 Accuracy / 证据召回 / 拒答率  
3. 越权用例：证据集与推理链节点均不泄露  
4. 子问题链抽检：每跳结论为干净实体，而非边描述  
5. 单题成本与真实 wall-clock 超时熔断  
6. 消融：Baseline 向量 vs Agentic-无图 vs Full  

---

## 十、最终结论

### 代码证明了什么

1. Agentic 循环、图多跳检索、统一证据契约、引用 ID 门禁、Schema 入图门禁、评测脚手架**真实存在且可测**。  
2. 在 **seed 公司关系图 + offline 规则** 下，典型 2 跳题（如 parent→CEO）**可以跑通并答对**。  
3. 工程纪律较好：抽象边界、单测、参数化 Neo4j、预算计数。

### 代码否定了什么

1. **不是**企业内知识管理系统：无权限、无组织/制度本体、无更新废止。  
2. **不是**可靠的自主多跳推理：offline Critic/Answer/Planner 是规则栈；且存在物化污染。  
3. **不能**用 offline 高分代表生产能力。  
4. **不能**默认“有图谱就优于向量 RAG”——仅在已结构化关系问题上机制占优。

### 一句话

> **这是一个完成度不错的多跳 Graph+Agent 技术 POC 代码库，适合在收窄场景与硬门禁下继续验证；以当前实现直接服务企业内部知识生产问答，证据不足且风险过高。**

---

## 附：与“只看文档评估”的差异（代码校正）

| 若只看文档可能得到的印象 | 代码事实 |
|---|---|
| Critic 具备有效反思 | offline 见 graph 即 sufficient；live 无一致性校验 |
| 推理链可审计 | 有结构；ID 引用 ≠ 内容可审计；无落库 API |
| RRF 混合检索 | 函数有，主路径 concat |
| 超时可控 | 字段有，循环未 enforce |
| 分诊 Fast Path | 未实现 |
| 企业权限 | 完全缺失 |
| 高 Accuracy 反映 Agent 能力 | 高度依赖规则答案器与模板金标同构 |
| 子问题链干净 | 物化会把边文本灌进下一跳 |

---

**评审声明**：本报告仅基于仓库代码与离线可复现执行；未进行生产渗透、未接入真实企业语料、未做完整 live LLM 压测。若补齐权限、真语料 live 评测与消融结果，可申请复评并上调相关维度。
