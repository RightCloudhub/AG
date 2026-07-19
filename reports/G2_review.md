# G2 评审材料包（P2-EV-02 / P2-KG-04 / P2-EV-05/06）

**日期：** 2026-07-19  
**模式：** 离线 `--no-llm` + 确定性 pilot 三元组 / 合成试点语料（C1 工程关闭）  
**说明：** 效果数字来自 **dev 集**（153 条）。heldout（47 条）保留门禁用。人工抽检签字见 `evals/datasets/ANNOTATION_SPEC.md` §7。

---

## 1. 评测集（P2-EV-02）

| 项 | 值 |
|----|-----|
| 金标总数 | **200** |
| 分层 | `{"2hop": 90, "3hop": 60, "open": 30, "no_answer": 20}` |
| 分集 | dev=153 · heldout=47 · guardrail=25 |
| 标注规范 | `evals/datasets/ANNOTATION_SPEC.md` |
| 三元组 | 519 → `data/processed/pilot_triples.jsonl` |
| 分层校验 | **ok=True** |

产物：`evals/datasets/g2_{all,dev,heldout,guardrail}.jsonl` · `g2_dataset_summary.json`

---

## 2. 图谱规模（P2-KG-04）

| 项 | 值 |
|----|-----|
| 试点文档 | 226 篇 `data/pilot/raw` |
| chunks | **226** `data/processed/pilot_chunks.jsonl` |
| 入图三元组 | **519**（确定性 pilot extract；可换 LLM `run_extract_pipeline`） |
| 内存建图 | 201 nodes / 519 rels（`reports/pilot_build_graph_memory.log`） |
| Neo4j 建图 | backend=neo4j，201 nodes / 519 rels（`reports/pilot_build_graph_neo4j.log`） |
| BM25 | 226 chunks → `data/indexes/bm25.json` |

---

## 3. 首轮全量评测（P2-EV-05，dev / offline）

| 指标 | Agentic | Baseline | Δ |
|------|---------|----------|---|
| Accuracy % | **60.78** | **11.76** | **49.02 pp** |
| Evidence recall | **0.8756** | — | — |
| Latency P50/P95 ms | 18.0 / 40.0 | — | — |
| Fabrication rate | 0.0 | — | — |

- 对比报告：`reports/g2_dev_eval.md` / `.json`
- Badcase 归因：`{"graph_missing": 0, "retrieval": 0, "decomposition": 0, "generation": 60, "correct": 93, "gold_error": 0}`
- 明细：`reports/g2_dev_badcase.json`

### 四类归因

| 归因 | 含义 | 首轮 |
|------|------|------|
| retrieval | 金标证据未进入检索命中 | 0 |
| decomposition | 规划/Critic/护栏步数异常 | 0 |
| generation | 证据部分在场但答案错误 | 60 |
| graph_missing | 未走图工具且证据全无 | 0 |
| correct | — | 93 |

**观察：** 离线启发式下 badcase 几乎全部归为 **generation**（图检索命中了但规则答案器对扩展模板覆盖不全）。Evidence recall **0.8756 ≥ 0.75** 达标趋势；Accuracy **+49.02pp ≥ +15pp**。

---

## 4. 优化说明与二轮（P2-EV-06）

**本轮优化（相对 POC 20-case / 23-seed）：**

1. **P2-KG-04** 用 pilot 全量 **519** 三元组替代 23 条 seed，图覆盖显著扩大。  
2. **P2-EV-02** 金标扩至 **200** 并 **dev/heldout/guardrail** 分集（R7）。  
3. 统一 `agr-eval` + `agr-badcase` 归因流水线。  
4. Neo4j 一致性写边（head/tail/sources）已落地；本包建图使用新属性。  
5. 未改 live Prompt（保持 offline 可复现）；generation 桶为下一轮（live LLM / 规则扩展）优先项。

二轮（同配置复现）：Accuracy **60.78%**，Δ baseline **49.02 pp**，recall **0.8756**。  
Badcase 二轮：`{"graph_missing": 0, "retrieval": 0, "decomposition": 0, "generation": 60, "correct": 93, "gold_error": 0}`  
报告：`reports/g2_dev_eval_r2.md`

---

## 5. G2 门禁对照（roadmap）

| 判据 | 状态 | 备注 |
|------|------|------|
| P0 实现 | 代码侧基本完成 | — |
| 评测集 ≥200 + 证据 | **工程关闭** | 自动金标 + 规范；人工抽检签字 pending |
| Accuracy +≥15pp 趋势 | **dev offline 达标**（+49.02pp） | heldout / live 待补 |
| Evidence Recall ≥75% | **dev offline 达标**（0.8756） | hops≥2 口径 |
| 正式语料产品签字 | 未关 | 合成语料仅工程 |

**推荐结论：Conditional-Go（工程 G2 趋势包）** — EV-02/KG-04/EV-05/06 离线产物齐备；正式效果门禁关闭仍需 heldout 锁定数字 + 人工抽检 +（建议）live LLM。

---

## 6. 复现

```bash
python -m agentic_graphrag gen-cases
python -m agentic_graphrag ingest --input data/pilot/raw --out data/processed/pilot_chunks.jsonl
python -m agentic_graphrag build-graph --triples data/processed/pilot_triples.jsonl --no-llm
python -m agentic_graphrag run-cases --no-llm --memory-graph \
  --cases evals/datasets/g2_dev.jsonl --seed-triples data/processed/pilot_triples.jsonl --out reports/g2_dev
python -m agentic_graphrag run-baseline --no-llm --cases evals/datasets/g2_dev.jsonl \
  --chunks data/processed/pilot_chunks.jsonl --out reports/g2_dev
python -m agentic_graphrag eval --agentic reports/g2_dev/poc_run.jsonl \
  --baseline reports/g2_dev/baseline_run.jsonl --cases evals/datasets/g2_dev.jsonl --stem g2_dev_eval
python -m agentic_graphrag badcase --run reports/g2_dev/poc_run.jsonl --cases evals/datasets/g2_dev.jsonl
# 一键： ./scripts/p2_ev_kg_pipeline.sh
```
