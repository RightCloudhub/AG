# 阶段二：MVP 构建（4-6周）

**目标**：完整四层架构落地，全部 P0 需求实现；建成 ≥200 条评测集并输出与 Baseline 的量化对比报告。
**前提**：G1 **Conditional-Go** 的过渡条件关闭（或评审豁免）——见 [g1-to-g2-transition.md](./g1-to-g2-transition.md)：  
C1 正式试点语料（P1-GOV-01）· C2 实时 LLM 重跑 · C3 Neo4j 回归。  
POC 代码按工程规范重构（TDD、80% 覆盖率自此强制执行，见 [testing-strategy.md](../engineering/testing-strategy.md)）。

### 入场检查（W0，进入 W1 前）

```bash
./scripts/g1_to_g2_gate.sh              # 汇总 C1/C2/C3 → reports/G1_to_G2_status.json
./scripts/g1_to_g2_gate.sh --with-llm   # 含 C2 自动部分
```

- [ ] `P2-ENTRY-01` 过渡门禁通过或书面豁免已归档

## 周计划

| 周 | 重点 |
|---|---|
| W1-W2 | 架构骨架搭建（服务化、接口抽象、统一响应封装）；POC 逻辑迁入；评测集标注启动 |
| W3-W4 | Planner 树状分解、Critic 迭代优化、答案引用绑定；Baseline 管线实现 |
| W5 | 评测集完成，首轮全量评测，badcase 归因 |
| W6 | 针对性优化一轮，第二轮评测，G2 评审材料 |

## 任务清单

### 架构与服务化（ARCH）
- [x] `P2-ARCH-01` 按 [repo-structure.md](../engineering/repo-structure.md) 建立正式仓库：分层模块、依赖注入、配置管理（密钥走环境变量）— `stores/factory` DI · `config.py`+env · `cli/` 分包 · layer dirs (`agent|retrieval|knowledge|generation|api|eval|llm|stores`)
- [x] `P2-ARCH-02` 存储抽象接口（Repository 模式）：GraphStore / VectorStore / DocStore / LLMClient，factory 组合根（NFR-10）— `stores/interfaces.py` · `stores/factory.py` · `llm/interfaces.py`
- [x] `P2-ARCH-03` `POST /v1/query` API + 统一响应 envelope + 输入 schema 校验（FR-API-01/04，NFR-07）— `api/app.py` · `agr-api`
- [x] `P2-ARCH-04` CI 流水线：lint + 单测 + 覆盖率门禁（≥80%）— `.github/workflows/ci.yml` · `pyproject.toml` coverage

### 图谱工作流（KG）
- [x] `P2-KG-01` 抽取管线工程化：任务化、失败重试、来源元数据落库（FR-KG-01/02 完整版）— `run_extract_pipeline` journal/retry/quarantine/provenance
- [x] `P2-KG-02` Schema 校验强制化：不合规三元组拒绝入图并记录（FR-KG-03）— `knowledge/schema_check.gate_triples` · reject log
- [x] `P2-KG-03` 置信度阈值过滤入图，阈值可配置（FR-KG-06 部分）— `KnowledgeConfig.extract_confidence_threshold` · gate
- [ ] `P2-KG-04` 图谱规模扩展至试点领域全量语料

### 检索工作流（RT）
- [x] `P2-RT-01` 图检索增强：子图遍历 + 相关性剪枝 + Top-K 路径采样（FR-RT-02 完整版，防路径爆炸）— `retrieval/graph.py` beam + relation cues + config caps
- [x] `P2-RT-02` 三路检索接口统一：候选项携带来源类型、分数、引用— `retrieval/contracts.py` (`vector_chunk` / `graph_path` / `graph_neighbor` / `fulltext_chunk`)

### Agent 工作流（AG）
- [x] `P2-AG-01` Planner 升级：支持树状/图状子问题依赖，子问题可依赖前序结果动态生成（FR-AG-02 完整版）— `agent/planner.py` DAG + `{from:sqN}` materialize
- [x] `P2-AG-02` Critic 升级：区分"子问题未答全"与"原问题未答全"，支持子问题改写（FR-AG-04 完整版）— `CriticScope` + rewrite/exclude
- [x] `P2-AG-03` Memory 升级：已排除假设记录；跨子问题证据共享（FR-AG-05 完整版）；状态承载迁至 LangGraph typed state + checkpointer，去重/排除假设语义逻辑保持自研— `MemorySnapshot` / `AgentState.memory_snapshot`
- [x] `P2-AG-04` 护栏参数化：跳数/调用次数/token 预算/超时/recursion 均从配置读取，触顶兜底带已探索路径摘要（FR-AG-06/07）— `GuardrailConfig.from_app_config`
- [x] `P2-AG-05` 答案关键论断绑定引用；无引用断言在生成层拦截（FR-AN-01，AC-7 基础）— `generation/citations.py` · split `offline_answer.py`
- [x] `P2-AG-06` 推理链 JSON Schema 定稿（子问题→工具→节点/边/片段→中间结论→答案）（FR-AN-02）— `configs/schema/reasoning_chain_v1.json`

### 评测工作流（EV）
- [x] `P2-EV-01` 评测集 **codeable substrate**（schema + 分层校验 + 从 seed 三元组确定性生成器）— `eval/cases.py` · `eval/gold_gen.py`；≥200 条人工/扩充集仍待填满（见 EV-02）
- [ ] `P2-EV-02` 金标标注：答案 + 支持证据（节点/边/文档片段），标注规范文档化；扩至 ≥200 条
- [x] `P2-EV-03` Baseline 实现：纯向量 RAG 管线（同 LLM、同语料，保证公平对比）— `eval/baseline_rag.py` · `agr-run-baseline`
- [x] `P2-EV-04` 一键评测脚本：Accuracy / 证据 Recall / 延迟 / 成本，输出对比报告（FR-OP-04）— `eval/report.py` · `agr-eval`（对照已有 run 产物；全量执行 deferred）
- [ ] `P2-EV-05` 首轮全量评测 + badcase 归因分类（检索失败/分解失败/生成失败/图谱缺失）
- [ ] `P2-EV-06` 针对 badcase 优化一轮，二轮评测，产出 G2 评审材料

## 交付物

1. 服务化的完整四层架构（API 可调用）
2. ≥200 条标注评测集 + 标注规范
3. Baseline vs Agentic 量化对比报告（两轮）
4. G2 评审结论

## 出口标准

见 [roadmap.md](../roadmap.md) G2 门禁。核心：**P0 全部实现，Accuracy +≥15pp、Recall ≥75%（趋势达标）**。

## 本阶段明确不做

- 复杂度分诊、流式响应、缓存并行化（阶段三）
- 实体消歧人工审核界面、增量更新（阶段三）
- 问答 Web 界面（阶段四）
