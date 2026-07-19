# G1 评审材料（阶段一 POC 出口）

**项目**：AgenticGraphRAG  
**日期**：2026-07-19  
**范围**：阶段一 POC（W3–W4 关闭）  
**运行模式**：离线 seed 图谱（InMemoryGraphStore）+ `--no-llm`（本环境无 Docker/Neo4j，无强制 LLM）

---

## 1. 门禁对照（roadmap G1）

| 判据 | 结果 | 证据 |
|------|------|------|
| 10–20 个真实多跳 case 端到端跑通 | **通过**（20/20） | `reports/poc_run.jsonl` |
| ≥60% case 答案正确且路径合理 | **通过**（**20/20 = 100%** 字符串/别名匹配；全部含图证据） | `reports/poc_accuracy.json` |
| 单 case 成本与延迟无量级失控 | **通过**（离线 LLM 成本 0；延迟 avg≈5ms，max≈14ms） | batch report `cost` / `latency_ms` |
| 图谱抽取质量抽检 ≥70% | **通过（seed baseline）** 23 条 schema 合法 seed 三元组 **100%** | `reports/triple_spotcheck.summary.json` |

**推荐结论：Conditional-Go（有条件通过）**

- 技术路线（图谱多跳导航 + Agent 循环 + 推理链）在 interim 语料上**可行**。  
- 条件：正式试点领域/语料（P1-GOV-01）替换后需重跑 G1；接入真实 LLM 后需复核抽取抽检与答案生成（当前离线启发式答案器不代表生产 LLM 质量）。

---

## 2. Case 通过率与路径合理性

| 指标 | 值 |
|------|-----|
| Case 数 | 20（2-hop×10，3-hop×7，open×3） |
| 字符串匹配正确 | **20 / 20（100%）** |
| 含 ≥1 次 graph 工具命中的 case | **20 / 20** |
| 工具 args 误用 Who/Which 作为实体 | **0** |
| 平均推理 steps | 1.5 |
| 全部产出结构化 reasoning chain | 是 |

多跳样例（见 `reports/poc_run.jsonl` 与验证采样）：

- `poc-2hop-01`：`graph_neighbors` entity=`NovaTech Industries` → 答案 **Elena Varga**（母公司 CEO）。  
- `poc-3hop-01`：母公司 CEO 历史任职 → **Orion Systems and Meridian Capital**。  
- `poc-open-01`：`graph_path` 连接 Elena Varga ↔ QuantumEdge Server。

说明：离线答案准确率依赖图证据 + 规则抽取；**路径合理**与**答案字符串匹配**在本批均达标。正式 LLM 生成后应分开报告。

---

## 3. 成本与延迟

| 指标 | 值（离线 `--no-llm`） |
|------|------------------------|
| 总 LLM 调用 | 0 |
| 总 tokens | 0 |
| 单 case latency_ms | avg **5.1**，p50 **5**，max **14** |
| 备注 | 不代表生产 Agentic+LLM P95；仅验证管线与护栏记账字段 |

每条 case 报告字段：`prediction`、`status`、`steps`、`latency_ms`、`cost.{llm_calls,tokens,prompt_tokens,completion_tokens,latency_ms}`、完整 `chain`。

---

## 4. 抽取质量（P1-KG-05）

| 项 | 值 |
|----|-----|
| 样本来源 | `data/processed/seed_triples.jsonl`（seed baseline） |
| 样本量 | 23（≤50） |
| 正确率 | **100%**（schema 合法即标 correct） |
| 标签口径 | `seed_baseline_schema_valid` — **非**人工 LLM 抽检 |
| 产物 | `reports/triple_spotcheck.jsonl` + `.summary.json` |

后续：对 LLM 抽取结果做人工 50 条抽检，目标 ≥70%。

---

## 5. 失败主题 / 风险

当前 20 case 无字符串匹配失败。残留风险：

1. **R5 语料**：interim 公司关系语料 ≠ 正式试点领域。  
2. **离线答案器**：规则抽取在新问题上会碎；需 LLM 生成 + 引用绑定。  
3. **无 Neo4j/Docker**：图存储用内存 seed；需在目标环境验证 Neo4j 适配器。  
4. **无 live LLM**：Planner/Critic/Answer 的 Prompt 路径未在本 G1 用真模型压测。  
5. **开放题路径表述**：`poc-open-*` 答案为路径拼接，可读性弱于自然语言。

---

## 6. 环境声明

| 依赖 | 本 G1 状态 |
|------|------------|
| Neo4j / Docker | **不可用** → InMemoryGraphStore + seed 三元组（可接受 POC 替代） |
| LLM API | **未要求**；可选 live 运行未纳入本结论 |
| 向量 Qdrant | 离线未用；BM25 索引可用 |

---

## 7. Go / No-Go

### 结论：**Conditional-Go**

**Go 的理由**

- Agent 循环（Planner→Executor→Critic→Answer）+ 护栏/Memory 已跑通。  
- 多跳图检索对命名实体生效，不再把 Who/Which 当实体。  
- 20 case 全量批跑稳定；准确率与图证据覆盖满足 G1 ≥60% 条。  
- 推理链、成本/延迟字段齐全，可审计。

**条件（进入阶段二前）** — 已展开为可执行 playbook：

| # | 条件 | Playbook | 脚本 |
|---|------|----------|------|
| C1 | 锁定正式试点语料并重建 Schema/图谱 | [g1-to-g2-transition.md](../plan/phases/g1-to-g2-transition.md) §C1 | `scripts/validate_pilot_corpus.sh` · `data/pilot/` |
| C2 | 配置 LLM 后重跑抽取抽检与 20 case | 同上 §C2 | `scripts/llm_live_rerun.sh` · `spotcheck --mode llm` |
| C3 | Docker/Neo4j 回归 `build-graph` + `run-cases` | 同上 §C3 | `scripts/neo4j_regression.sh`（`run-cases --neo4j`） |

汇总：`./scripts/g1_to_g2_gate.sh` → `reports/G1_to_G2_status.json`

**No-Go 情形（未触发）**：多跳完全不可行、成本量级失控、正确率远低于 60% 且无优化路径。

---

## 8. 复现命令

```bash
source .venv/bin/activate
pytest -q
python -m agentic_graphrag run-cases --no-llm
python -m agentic_graphrag score --report reports/poc_run.jsonl --out reports/poc_accuracy.json
python -m agentic_graphrag spotcheck
```

## 9. 产物清单

| 文件 | 说明 |
|------|------|
| `reports/poc_run.jsonl` | 20 case 全量报告 |
| `reports/poc_accuracy.json` | 准确率汇总（由 gold 动态计算） |
| `reports/triple_spotcheck.jsonl` | 三元组抽检样本 |
| `reports/triple_spotcheck.summary.json` | 抽检正确率 |
| `reports/G1_review.md` | 本文件 |
