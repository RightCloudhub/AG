# 阶段一：POC 验证（3-4周）

**目标**：用最小代价验证"图谱 + Agent 多跳循环"技术路线可行，产出 G1 门禁所需证据。
**原则**：一切从简 —— 单一领域、小图谱、无界面、脚本驱动；只验证核心闭环，不做工程化。

## 周计划

| 周 | 重点 |
|---|---|
| W1 | 选型落地 + 语料准备 + Schema 定义；评测 case 设计启动 |
| W2 | 抽取管线跑通，构建小图谱（百~千级实体）；三路检索工具封装 |
| W3 | Agent 循环（Planner→Executor→Critic→Memory）打通，端到端跑 case |
| W4 | 20 个多跳 case 全量跑测 + 成本/延迟采集 + G1 评审材料 |

## 任务清单

### 前置决策（W1，阻塞项）
- [~] `P1-GOV-01` 确定首个试点领域与语料范围（≥100 篇文档），获得数据授权 — 负责人：产品 — **POC 暂用 interim 公司关系语料（6 篇 + seed 三元组）；G1→G2 过渡 C1，见 [g1-to-g2-transition.md](./g1-to-g2-transition.md)、`data/pilot/`**
- [x] `P1-GOV-02` 图数据库选型决策（Neo4j）→ 写入 tech-stack.md ADR-001 已采纳 — 负责人：架构
- [x] `P1-GOV-03` LLM 选型：OpenAI 兼容双档位 + BudgetTracker；POC 预算待业务确认 — 负责人：架构

### 图谱工作流（KG）
- [x] `P1-KG-01` 定义领域 Schema V0：5 类实体、14 类关系（`configs/schema/domain_v0.yaml`）
- [x] `P1-KG-02` 文档接入脚本：Markdown/TXT/HTML → 清洗分段（`knowledge/ingest.py` + CLI）
- [x] `P1-KG-03` LLM 三元组抽取 Prompt + 结构化输出校验，带置信度与来源引用（`knowledge/extraction.py`）
- [x] `P1-KG-04` 三元组入图脚本（`knowledge/graph_builder.py` + seed 离线路径）
- [x] `P1-KG-05` 抽取质量抽检：seed baseline 23 条 schema 合法三元组（`reports/triple_spotcheck*`），正确率 100%（seed 口径）；正式 LLM 抽检待试点语料后补

### 检索工作流（RT）
- [x] `P1-RT-01` 文档 chunk 化 + embedding 入向量库（Qdrant / InMemory）（`retrieval/vector.py`）
- [x] `P1-RT-02` 图检索工具：k 跳邻居、路径查询（`retrieval/graph.py` + Neo4j）
- [x] `P1-RT-03` BM25 全文检索（`stores/fulltext_store.py` + `retrieval/fulltext.py`）

### Agent 工作流（AG）
- [x] `P1-AG-00` LangGraph 骨架：StateGraph 节点/条件边跑通（`agent/loop.py`）
- [x] `P1-AG-01` Planner：问题 → 子问题序列（链式；`agent/planner.py`）
- [x] `P1-AG-02` Executor：子问题 → 选择检索工具 → 执行（`agent/executor.py`）
- [x] `P1-AG-03` Critic：证据充分性判定 + 下一跳（`agent/critic.py`）
- [x] `P1-AG-04` Memory：已探索路径与证据去重（`agent/memory.py`）
- [x] `P1-AG-05` 护栏：最大跳数 + LLM 调用/token 预算 + 诚实兜底（`agent/guardrails.py`）
- [x] `P1-AG-06` 答案生成：证据 → 答案 + JSON 推理链（`generation/`）

### 验证与评审
- [x] `P1-EV-01` 设计 20 个多跳 case（`evals/datasets/poc_cases.jsonl`）
- [x] `P1-EV-02` 端到端跑测脚本骨架（`cli.run_cases_main`；需 Neo4j 时跑全量）
- [x] `P1-EV-03` G1 评审材料：`reports/G1_review.md`（Conditional-Go；20/20、成本延迟、风险、环境声明）
- [x] `P1-EV-04` **G1→G2 C2**：实时 LLM 重跑 — 自动化通过（`scripts/llm_live_rerun.sh` · `reports/llm_live_rerun.json`）；**live 质量 caveat**（网关 403 / 准确率低）见 closeout
- [x] `P1-EV-05` **G1→G2 C3**：Neo4j 回归 — `pass_partial`（`scripts/neo4j_regression.sh` · `reports/neo4j_regression.json`，offline 14/20）

## 交付物

1. 可运行的 POC 代码仓库（脚本形态即可，按 [repo-structure.md](../engineering/repo-structure.md) 布局）
2. 单一领域小图谱 + 向量索引
3. 20 case 跑测报告（通过率、失败归因、成本/延迟）
4. G1 评审结论

## 出口标准

见 [roadmap.md](../roadmap.md) G1 门禁。核心：**≥60% case 正确且路径合理，成本延迟无量级失控**。

## 本阶段明确不做

- 不做界面、不做 API 服务化（脚本调用即可）
- 不做实体消歧、增量更新（图谱可全量重建）
- 不做融合排序（三路结果简单拼接）、不做复杂度分诊
- 不追求测试覆盖率（POC 代码允许后续重写，但核心数据结构定义要留存）
