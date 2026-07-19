# 评测集标注规范（P2-EV-02）

**范围：** ≥200 条多跳金标 case（答案 + 支持证据）；dev / heldout / guardrail 分集。  
**构建策略：** 合成路径模板 + 确定性图遍历（`eval/gold_gen.py`）+ 人工抽检修订。  
**关联：** `plan/workstreams/evaluation.md` · FR-OP-04 · AC-1/2/7 · 风险 R6/R7。

---

## 1. Case 字段（schema）

每行 JSONL 对应 `EvalCase`（`src/agentic_graphrag/eval/cases.py`）：

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 全局唯一，如 `g2-2hop-0001` |
| `question` | 是 | 自然语言问题（中/英均可；本集默认英文模板） |
| `gold_answer` | 是* | 金标答案字符串；无答案题用 `no answer` |
| `hops` | 是 | 名义跳数；`no_answer` 可为 0 |
| `category` | 推荐 | `2hop` / `3hop` / `open` / `no_answer` |
| `gold_path` | 有答必填 | 交错节点与关系标签，如 `["NovaTech","SUBSIDIARY_OF","Apex","CEO_OF","Elena"]` |
| `gold_evidence` | 有答必填 | 证据 token 列表：实体名、关系类型、（可选）chunk/doc id |
| `notes` | 否 | 标注备注、歧义说明 |
| `metadata` | 否 | `template`、`label_source`、`annotation_status`、`split` 等 |

\* `no_answer` 类：`gold_answer="no answer"`，`gold_path`/`gold_evidence` 可为空。

### 证据口径

1. **节点/边（优先）：** 推理必需的实体名 + 关系类型（与 Schema 一致，大写关系）。  
2. **文档片段（可选增强）：** `doc_id` / `chunk_id` / 短 span；写入 `gold_evidence` 或 `metadata.spans`。  
3. **最小充分集：** 仅保留回答该题**必要**的证据，避免把整图塞进 evidence。  
4. **证据 Recall（AC-2）：** 跳数 ≥2 的题上，评测用 evidence token 是否出现在推理链 / 预测文本中（见 `eval/metrics.py`）。

---

## 2. 分层目标（G2）

| 类别 | 数量 | 说明 |
|------|------|------|
| 2 跳 | ≥90 | 如「子公司的母公司 CEO」 |
| 3 跳 | ≥60 | 如「产品生产商的竞争方的 CEO」 |
| 开放路径 | ≥30 | 交集/事件参与/共享供应商等 |
| 无答案 | ≥20 | 图谱确认无此实体/关系，考察诚实兜底 |
| **合计** | **≥200** | `validate_stratification(..., strict_total=True)` |

**护栏集（不计 200）：** 发散、循环诱导、超长、注入、空输入等（AC-6），见 `g2_guardrail.jsonl`。

---

## 3. 分集策略（防过拟合 R7）

| 集合 | 比例 / 规模 | 用途 |
|------|-------------|------|
| **dev** | ~75% 金标 | 日常调优、badcase 回流只进 dev |
| **heldout** | ~25% 金标 | 门禁/G2·G3 正式数字；禁止用 heldout 做 prompt 迭代 |
| **guardrail** | ~25 条 | 护栏专项；单独报告 |

划分算法：对 `case_id` 做 SHA1 稳定分桶（`eval/split_sets.py`），保证重跑可复现。

门禁建议：

- G2 趋势：**dev** 上 Accuracy +≥15pp、Recall ≥75%  
- G3 终态：**heldout** 上 +≥25pp、Recall ≥85%

---

## 4. 标注流程

### 4.1 自动生成（本仓库默认）

```bash
# 1) 确定性 pilot 三元组（对齐 generate_pilot_corpus 宇宙）
python -m agentic_graphrag gen-cases
# → data/processed/pilot_triples.jsonl
# → evals/datasets/g2_{all,dev,heldout,guardrail}.jsonl
# → evals/datasets/g2_dataset_summary.json
```

`metadata.label_source = deterministic_path_template`  
`metadata.annotation_status = auto_gold`

### 4.2 人工修订（抽检）

1. 每类随机抽 **≥10%**（至少 20 条 2hop、15 条 3hop、10 条 open、全部 no_answer 扫一眼）。  
2. 检查：  
   - 问题是否**真多跳**（不能单跳图查询就秒答且与 gold_path 矛盾）  
   - 答案是否唯一/规范（人名、公司名与图谱字面一致）  
   - `gold_path` / `gold_evidence` 是否充分且无多余噪声  
3. 修订后设 `metadata.annotation_status = human_reviewed`，必要时改 `notes`。  
4. **不得**仅因模型答错而改 heldout 金标；金标错误走 `gold_error` 归因并修正后重跑。

### 4.3 判定对错（简版）

| 标签 | 条件 |
|------|------|
| correct | 答案与 gold 字符串/别名匹配（`score_pair`）或无答案题诚实拒答 |
| incorrect | 答错、张冠李戴、无依据臆造 |
| gold_error | 金标本身错误（进 dev 修订队列） |

---

## 5. 禁止事项

- 用 heldout 做反复 prompt 调参（R7）。  
- 把护栏题混入 200 条金标总数。  
- 金标答案使用与图谱不一致的昵称而不写 aliases。  
- 无证据的「常识题」进入 2hop/3hop 桶。

---

## 6. 产物清单

| 路径 | 说明 |
|------|------|
| `evals/datasets/g2_all.jsonl` | 全量 ≥200 金标 |
| `evals/datasets/g2_dev.jsonl` / `dev.jsonl` | 开发集 |
| `evals/datasets/g2_heldout.jsonl` / `heldout.jsonl` | 门禁集 |
| `evals/datasets/g2_guardrail.jsonl` / `guardrail.jsonl` | 护栏集 |
| `evals/datasets/g2_dataset_summary.json` | 分层与分集统计 |
| `data/processed/pilot_triples.jsonl` | 金标/建图共用三元组 |
| `evals/datasets/poc_cases.jsonl` | 历史 20 条 POC（保留） |

---

## 7. 验收（P2-EV-02 Done）

- [x] `validate_stratification` strict：total≥200 且各类下限满足  
- [x] 每条有答 case 含 `gold_path` 或非空 `gold_evidence`  
- [x] dev/heldout/guardrail 三分文件存在且 summary 可审计  
- [x] 本规范文档入库  
- [ ] 人工抽检比例达标（发布 G2 前由评测负责人签字）
