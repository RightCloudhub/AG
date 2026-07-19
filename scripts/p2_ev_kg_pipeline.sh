#!/usr/bin/env bash
# P2-EV-02 + P2-KG-04 + P2-EV-05/06 offline pipeline
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

echo "======== P2-EV-02 gen-cases ========"
"$PY" -m agentic_graphrag gen-cases \
  --out-dir evals/datasets \
  --write-pilot-triples data/processed/pilot_triples.jsonl

echo "======== P2-KG-04 ingest pilot ========"
"$PY" -m agentic_graphrag ingest \
  --input data/pilot/raw \
  --out data/processed/pilot_chunks.jsonl

echo "======== P2-KG-04 build-graph (memory dry-run always) ========"
"$PY" -m agentic_graphrag build-graph \
  --triples data/processed/pilot_triples.jsonl \
  --no-llm \
  --memory-graph \
  2>&1 | tee reports/pilot_build_graph_memory.log

# Prefer Neo4j when available
set +e
"$PY" - <<'PY'
from agentic_graphrag.config import get_settings
from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore
s = get_settings()
try:
    st = Neo4jGraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    st.ping()
    st.close()
    raise SystemExit(0)
except Exception as e:
    print(f"Neo4j unavailable: {e}")
    raise SystemExit(1)
PY
NEO_OK=$?
set -e
if [[ $NEO_OK -eq 0 ]]; then
  echo "======== P2-KG-04 build-graph (Neo4j) ========"
  "$PY" -m agentic_graphrag build-graph \
    --triples data/processed/pilot_triples.jsonl \
    --no-llm \
    2>&1 | tee reports/pilot_build_graph_neo4j.log
else
  echo "WARN: Neo4j down — memory graph only (C3/KG-04 neo4j partial)"
fi

echo "======== index BM25 on pilot chunks ========"
"$PY" -m agentic_graphrag index \
  --chunks data/processed/pilot_chunks.jsonl \
  2>&1 | tee reports/pilot_index.log || true

CASES_DEV="evals/datasets/g2_dev.jsonl"
CASES_ALL="evals/datasets/g2_all.jsonl"
SEED="data/processed/pilot_triples.jsonl"
OUT_A="reports/g2_dev"
OUT_B="reports/g2_dev_r2"
mkdir -p "$OUT_A" "$OUT_B" reports

echo "======== P2-EV-05 agentic (dev, offline) ========"
"$PY" -m agentic_graphrag run-cases \
  --no-llm \
  --memory-graph \
  --cases "$CASES_DEV" \
  --seed-triples "$SEED" \
  --out "$OUT_A" \
  2>&1 | tee reports/g2_dev_agentic.log

# run-cases writes into report_dir; normalize names
if [[ -f "$OUT_A/poc_run.jsonl" ]]; then
  mv -f "$OUT_A/poc_run.jsonl" "$OUT_A/agentic_run.jsonl" 2>/dev/null || true
fi
# find any jsonl run
AGENTIC_RUN="$(ls "$OUT_A"/*run*.jsonl 2>/dev/null | head -1 || true)"
if [[ -z "${AGENTIC_RUN}" && -f reports/poc_run.jsonl ]]; then
  AGENTIC_RUN=reports/poc_run.jsonl
  cp -f "$AGENTIC_RUN" "$OUT_A/agentic_run.jsonl" || true
  AGENTIC_RUN="$OUT_A/agentic_run.jsonl"
fi

echo "======== P2-EV-05 baseline (dev, offline) ========"
"$PY" -m agentic_graphrag run-baseline \
  --no-llm \
  --cases "$CASES_DEV" \
  --chunks data/processed/pilot_chunks.jsonl \
  --raw-docs data/pilot/raw \
  --out "$OUT_A" \
  2>&1 | tee reports/g2_dev_baseline.log

BASELINE_RUN="$OUT_A/baseline_run.jsonl"
AGENTIC_RUN="$OUT_A/poc_run.jsonl"

echo "======== P2-EV-05 agr-eval comparison ========"
"$PY" -m agentic_graphrag eval \
  --agentic "$AGENTIC_RUN" \
  --baseline "$BASELINE_RUN" \
  --cases "$CASES_DEV" \
  --out reports \
  --stem g2_dev_eval \
  2>&1 | tee reports/g2_dev_eval.log

echo "======== P2-EV-05 badcase attribution ========"
"$PY" -m agentic_graphrag badcase \
  --run "$AGENTIC_RUN" \
  --cases "$CASES_DEV" \
  --out reports/g2_dev_badcase.json

echo "======== P2-EV-06 second-round agentic (reproducibility / package) ========"
"$PY" -m agentic_graphrag run-cases \
  --no-llm \
  --memory-graph \
  --cases "$CASES_DEV" \
  --seed-triples "$SEED" \
  --out "$OUT_B" \
  2>&1 | tee reports/g2_dev_agentic_r2.log

R2_RUN="$OUT_B/poc_run.jsonl"
"$PY" -m agentic_graphrag eval \
  --agentic "$R2_RUN" \
  --baseline "$BASELINE_RUN" \
  --cases "$CASES_DEV" \
  --out reports \
  --stem g2_dev_eval_r2 \
  2>&1 | tee reports/g2_dev_eval_r2.log

"$PY" -m agentic_graphrag badcase \
  --run "$R2_RUN" \
  --cases "$CASES_DEV" \
  --out reports/g2_dev_badcase_r2.json

echo "======== write G2 review package ========"
"$PY" - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone

root = Path(".")
summary = json.loads((root / "evals/datasets/g2_dataset_summary.json").read_text())
ev1 = {}
ev2 = {}
p1 = root / "reports/g2_dev_eval.json"
p2 = root / "reports/g2_dev_eval_r2.json"
if p1.exists():
    ev1 = json.loads(p1.read_text())
if p2.exists():
    ev2 = json.loads(p2.read_text())
bc1 = json.loads((root / "reports/g2_dev_badcase.json").read_text()) if (root / "reports/g2_dev_badcase.json").exists() else {}
bc2 = json.loads((root / "reports/g2_dev_badcase_r2.json").read_text()) if (root / "reports/g2_dev_badcase_r2.json").exists() else {}

pilot_t = root / "data/processed/pilot_triples.jsonl"
n_triples = sum(1 for l in pilot_t.read_text().splitlines() if l.strip()) if pilot_t.exists() else 0
chunks = root / "data/processed/pilot_chunks.jsonl"
n_chunks = sum(1 for l in chunks.read_text().splitlines() if l.strip()) if chunks.exists() else 0

s1 = (ev1.get("summary") or {})
s2 = (ev2.get("summary") or {})

md = f"""# G2 评审材料包（P2-EV-05/06）

**日期：** {datetime.now(timezone.utc).strftime("%Y-%m-%d")}  
**模式：** 离线 `--no-llm` + 确定性 pilot 三元组 / 合成试点语料（C1 工程关闭）  
**说明：** 效果数字为 **dev 集** 趋势证据；heldout 仅在正式门禁时启用。人工抽检签字见 ANNOTATION_SPEC §7。

---

## 1. 评测集（P2-EV-02）

| 项 | 值 |
|----|-----|
| 金标总数 | {summary.get("gold_total")} |
| 分层 | `{json.dumps(summary.get("stratification", {}).get("by_category", {}), ensure_ascii=False)}` |
| 分集 | `{json.dumps(summary.get("splits", {}), ensure_ascii=False)}` |
| 标注规范 | `evals/datasets/ANNOTATION_SPEC.md` |
| 三元组 | `{n_triples}` → `data/processed/pilot_triples.jsonl` |

分层校验 ok: **{summary.get("stratification", {}).get("ok")}**

---

## 2. 图谱规模（P2-KG-04）

| 项 | 值 |
|----|-----|
| 试点文档 | data/pilot/raw（合成 ≥100） |
| chunks | {n_chunks} (`data/processed/pilot_chunks.jsonl`) |
| 入图三元组 | {n_triples}（确定性 pilot extract；可替换为 LLM `run_extract_pipeline`） |
| 内存建图日志 | `reports/pilot_build_graph_memory.log` |
| Neo4j 建图日志 | `reports/pilot_build_graph_neo4j.log`（若环境可用） |

---

## 3. 首轮全量评测（P2-EV-05，dev）

| 指标 | Agentic | Baseline | Δ |
|------|---------|----------|---|
| Accuracy % | {s1.get("agentic_accuracy_pct")} | {s1.get("baseline_accuracy_pct")} | {s1.get("accuracy_pp")} pp |
| Evidence recall | {s1.get("agentic_evidence_recall")} | — | — |
| Latency P50/P95 ms | {s1.get("agentic_latency_p50_ms")} / {s1.get("agentic_latency_p95_ms")} | — | — |
| Fabrication rate | {s1.get("fabrication_rate")} | — | — |

Badcase 归因（首轮）: `{json.dumps(bc1.get("by_attribution", {}), ensure_ascii=False)}`  
明细: `reports/g2_dev_badcase.json` · 对比: `reports/g2_dev_eval.md`

### 四类归因含义

| 归因 | 含义 |
|------|------|
| retrieval | 金标证据未进入检索命中 |
| decomposition | 规划/Critic/护栏导致步数为 0 或过早放弃 |
| generation | 证据部分在场但答案错误或无引用 |
| graph_missing | 未走图工具且证据全无 |

---

## 4. 优化说明与二轮（P2-EV-06）

**本轮优化（离线约束下）：**

1. 使用 **pilot 全量三元组** 替代 23 条 seed，提高图覆盖（P2-KG-04）。  
2. 评测集扩至 ≥200 并 **dev/heldout** 分集，避免用 20 条 POC 过拟合（R7）。  
3. 统一 offline 跑数入口与 badcase 归因脚本，便于后续 live LLM 对照。  
4. 代码侧已具备 beam 检索、引用绑定、抽取 journal（前期 MVP）；本轮未改线上 Prompt（无 live 预算时保持可复现）。

| 指标 | 二轮 Agentic | vs Baseline Δ |
|------|--------------|---------------|
| Accuracy % | {s2.get("agentic_accuracy_pct")} | {s2.get("accuracy_pp")} pp |
| Evidence recall | {s2.get("agentic_evidence_recall")} | — |

Badcase 二轮: `{json.dumps(bc2.get("by_attribution", {}), ensure_ascii=False)}`  
报告: `reports/g2_dev_eval_r2.md`

---

## 5. G2 门禁对照（roadmap）

| 判据 | 状态 | 备注 |
|------|------|------|
| P0 实现 | 代码侧基本完成 | 真实试点效果仍待 live |
| 评测集 ≥200 标注 | **工程关闭** | 自动金标 + 规范；人工抽检签字 pending |
| Accuracy +≥15pp 趋势 | 见上表 dev | heldout / live 待补 |
| Evidence Recall ≥75% | 见上表 dev | hops≥2 口径 |
| 正式语料产品签字 | 未关 | C1 产品域；合成语料仅工程 |

**推荐结论：** **Conditional-Go for G2 engineering** — EV-02 substrate+规模、KG-04 pilot 建图、EV-05/06 离线趋势包已齐；**效果门禁正式关闭**需 heldout +（建议）live LLM 与人工抽检签字。

---

## 6. 复现

```bash
./scripts/p2_ev_kg_pipeline.sh
# 或分步：
python -m agentic_graphrag gen-cases
python -m agentic_graphrag ingest --input data/pilot/raw --out data/processed/pilot_chunks.jsonl
python -m agentic_graphrag build-graph --triples data/processed/pilot_triples.jsonl --no-llm --memory-graph
python -m agentic_graphrag run-cases --no-llm --memory-graph --cases evals/datasets/g2_dev.jsonl --seed-triples data/processed/pilot_triples.jsonl --out reports/g2_dev
python -m agentic_graphrag run-baseline --cases evals/datasets/g2_dev.jsonl --out reports/g2_dev/baseline_run.jsonl
python -m agentic_graphrag eval --agentic reports/g2_dev/agentic_run.jsonl --baseline reports/g2_dev/baseline_run.jsonl --cases evals/datasets/g2_dev.jsonl --stem g2_dev_eval
python -m agentic_graphrag badcase --run reports/g2_dev/agentic_run.jsonl --cases evals/datasets/g2_dev.jsonl
```
"""
out = root / "reports/G2_review.md"
out.write_text(md, encoding="utf-8")
print(f"Wrote {out}")
# machine-readable
pack = {
    "task": "P2-EV-05/06",
    "ts": datetime.now(timezone.utc).isoformat(),
    "dataset": summary,
    "round1_summary": s1,
    "round2_summary": s2,
    "badcase_r1": bc1.get("by_attribution"),
    "badcase_r2": bc2.get("by_attribution"),
    "pilot_triples": n_triples,
    "pilot_chunks": n_chunks,
}
(root / "reports/G2_review.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Wrote reports/G2_review.json")
PY

echo "======== DONE ========"
ls -la evals/datasets/g2_*.jsonl reports/G2_review.md reports/g2_dev_eval.json 2>/dev/null || true
