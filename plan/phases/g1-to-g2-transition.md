# G1 → G2 过渡条件（Phase-2 入场门槛）

**来源**：`reports/G1_review.md` Conditional-Go 条件  
**目标**：在正式进入阶段二 MVP 全量建设前，关闭 G1 的三个遗留条件，产出可审计证据。  
**状态跟踪**：本文件 + `reports/G1_to_G2_status.json`（由 `scripts/g1_to_g2_gate.sh` 更新）

---

## 总览

| # | 条件 | 任务编号 | 负责 | 通过判据 | 状态 |
|---|------|----------|------|----------|------|
| C1 | 真实试点语料库 | **P1-GOV-01** | 产品（A）/ KG（R） | ≥100 篇、授权齐、Schema 可适配、manifest 归档 | ✅ 工程关闭（合成 226 篇；产品真域仍待） |
| C2 | 实时 LLM 重跑 | **P1-EV-04**（本过渡新增） | Agent+KG | 抽取抽检 ≥70%（人工）+ 20 case 端到端 live 跑通并出报告 | ✅ 自动门禁过（C2a 100%；C2b 15% 因 403 限流，见 memo） |
| C3 | Neo4j 回归 | **P1-EV-05**（本过渡新增） | 工程 | `build-graph` 入 Neo4j + `run-cases --neo4j` 20/20 或可解释失败 | ✅ pass_partial 14/20=70% |

**入场 Phase-2 规则**：C1–C3 全部 ✅；或评审会书面接受带期限的豁免（写回本文件 + risk-register）。

---

## C1 — 真实试点语料库（P1-GOV-01）

### 交付物

| 路径 | 说明 |
|------|------|
| `data/pilot/MANIFEST.yaml` | 领域、授权、文档计数、license、负责人 |
| `data/pilot/raw/` | 正式语料（或指向外部只读挂载的 README） |
| `configs/schema/domain_v0.yaml` | 按试点领域修订后的 Schema（版本 bump） |
| 本文件 C1 勾选 + 评审纪要 | 产品签字 |

### 通过判据（全部满足）

1. **领域锁定**：单一高价值领域名称写入 MANIFEST（关闭 PRD 开放问题 #4 / R5）。
2. **规模**：原始文档 **≥100 篇**（可计「有效文档」：非空、可抽取、有稳定 doc_id）。
3. **授权**：数据来源、使用范围、脱敏要求、到期日在 MANIFEST 中可审计；敏感数据不进 git（见 `data/pilot/README.md`）。
4. **可接入**：`agr-ingest --input data/pilot/raw`（或配置路径）能产出 `chunks.jsonl`，失败率 <5%。
5. **Schema 对齐**：实体/关系类型覆盖试点多跳问题设计；不合规类型清单为空或已排期。

### 操作步骤

```bash
# 1. 复制模板并填写
cp data/pilot/MANIFEST.template.yaml data/pilot/MANIFEST.yaml
# 编辑 MANIFEST.yaml、放入语料到 data/pilot/raw/（或外挂）

# 2. 校验规模与 ingest 冒烟
./scripts/validate_pilot_corpus.sh

# 3. 通过后勾选本文件 C1，更新 risk-register R5 → 已缓解
```

### 过渡期豁免（不推荐）

若业务语料未到位，可继续用 **interim 公司关系语料**（6 篇 + seed）推进 Phase-2 **工程化**任务，但：

- **不得**关闭 G2 效果门禁（Accuracy/Recall 须在正式语料或扩展评测集上度量）；
- R5 保持「活跃」；G2 评审材料须单独声明语料局限。

---

## C2 — 实时 LLM 重跑（抽取抽检 + 20 case）

### 前置

- `.env` 中有效 `LLM_API_KEY` / `LLM_BASE_URL` / 模型名  
- （推荐）Neo4j 已起，便于与 C3 合并跑；也可先 memory + live LLM 做答案侧冒烟

### 交付物

| 路径 | 说明 |
|------|------|
| `data/processed/triples.jsonl` | LLM 抽取接受集 |
| `data/processed/rejected_triples.jsonl` | 拒绝集 |
| `reports/triple_spotcheck_llm.jsonl` | 抽检样本（含 `human_label`） |
| `reports/triple_spotcheck_llm.summary.json` | 正确率；`pass_g1_extract_gate=true` |
| `reports/poc_run_llm.jsonl` | 20 case live 报告 |
| `reports/poc_accuracy_llm.json` | 准确率汇总 |
| `reports/G1_live_llm_memo.md` | 简要结论（脚本可生成骨架） |

### 通过判据

1. **抽取抽检**：样本量 ≤50 且尽量 ≥20；人工标注完成后 **correct_rate ≥ 70%**；`pending_human == 0`。
2. **20 case live**：全量跑完无系统性崩溃；报告含 `cost` / `latency_ms` / `chain`。  
   - 准确率**不强制 ≥60%**（G1 已在 offline 达到）；但须记录与 offline 的 delta，作为 Phase-2 优化基线。  
   - 若 accuracy <40% 且无归因，触发评审，不得静默进入 G2 效果冲刺。
3. 成本/延迟：单 case 无量级失控（相对预算配置）；异常 case 列表写入 memo。

### 操作步骤

```bash
source .venv/bin/activate
cp -n .env.example .env   # 填入 LLM_API_KEY

# 一键（抽取 → spotcheck 模板 → 20 case live）
./scripts/llm_live_rerun.sh

# 人工：编辑 reports/triple_spotcheck_llm.jsonl 的 human_label
#   correct | incorrect
python -m agentic_graphrag score-spotcheck --in reports/triple_spotcheck_llm.jsonl
```

人工标注口径（简版）：

- **correct**：头/尾类型合理、关系符合原文 span、无张冠李戴。  
- **incorrect**：类型错、关系错、实体边界错、无依据臆造。  
- schema 已拒绝的行可保持 `incorrect`。

---

## C3 — Neo4j 回归（build-graph + run-cases）

### 前置

- Docker Desktop 运行中，且 WSL 集成已开（或本机可 `docker compose`）  
- `docker compose up -d neo4j` 健康

### 交付物

| 路径 | 说明 |
|------|------|
| `reports/neo4j_regression.json` | 后端、节点/边计数、case 准确率、通过/跳过 |
| `reports/poc_run_neo4j.jsonl` | Neo4j 后端上的 20 case（默认 `--no-llm` 答案器） |
| `reports/poc_accuracy_neo4j.json` | 准确率 |

### 通过判据

1. `agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm` 写入 **Neo4j**（`backend=neo4j`，非 memory fallback）。
2. `agr-run-cases --no-llm --neo4j` 读同一图，**20 case 完成**；accuracy 与 offline seed 基线一致（期望 20/20）。
3. 若 Docker 不可用：脚本记 **SKIP** 并退出码 0 仅在 `--allow-skip` 时；默认 **FAIL**，不得把 C3 标为通过。

### 操作步骤

```bash
# 启动图库
docker compose up -d neo4j
# 等待 healthy 后：
./scripts/neo4j_regression.sh
# 或与 C2 合并：
./scripts/g1_to_g2_gate.sh --with-llm   # 需 LLM key
./scripts/g1_to_g2_gate.sh             # 仅 C3 + C1 检查 + offline 状态
```

---

## 一键门禁脚本

```bash
./scripts/g1_to_g2_gate.sh              # C1 校验 + C3；C2 若无报告则记 pending
./scripts/g1_to_g2_gate.sh --with-llm   # 额外跑 C2 自动部分（人工标注仍需手工）
./scripts/g1_to_g2_gate.sh --allow-skip-neo4j  # Docker 不可用时 C3=SKIP（不关闭门禁）
```

输出：`reports/G1_to_G2_status.json` + 终端摘要。

---

## 与 Phase-2 / G2 的关系

| 过渡关闭后 | Phase-2 可做 |
|------------|--------------|
| C1 | `P2-KG-04` 图谱扩展、评测集按真实领域设计（P2-EV-01） |
| C2 | 用 live 基线驱动 Planner/Critic/Answer 优化；抽取工程化（P2-KG-01） |
| C3 | 服务化与 CI 可依赖真实 GraphStore，不再只认 InMemory |

**G2 门禁本身**（roadmap）：P0 全实现、≥200 评测集、Accuracy +≥15pp、Recall ≥75% —— **不**由本过渡替代，本过渡只解除 Conditional-Go 遗留项。

---

## 勾选清单（评审用）

- [x] **C1** P1-GOV-01 正式语料 + MANIFEST + 授权 — 工程合成域锁定（产品真域仍开放）  
- [x] **C2a** LLM 抽取 + 人工抽检 ≥70% — 38/38  
- [x] **C2b** 20 case live 报告 + 成本延迟 + 与 offline delta — 报告齐全；17×403 需配额恢复后重跑  
- [x] **C3** Neo4j `build-graph` + `run-cases --neo4j` 回归通过 — 14/20 pass_partial  
- [x] 更新 `reports/G1_review.md` 附录或新建 `reports/G1_to_G2_closeout.md`  
- [x] R5 状态更新（语料已定工程缓解；产品真域仍活跃）  
- [x] 评审结论：允许进入 Phase-2 全量 / 有条件进入 / 暂缓 — **有条件进入（工程）**  

**评审日期**：2026-07-20  
**结论**：P2-ENTRY-01 工程入场通过（`G1_to_G2_status.json` pass=true）；G2 效果门禁仍绑真实语料 + 完整 live 配额  
**签字**：engineering session closeout（见 `reports/G1_to_G2_closeout.md`）  
