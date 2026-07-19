# G1 → G2 Live LLM re-run memo (C2)

**Date**: 2026-07-20  
**Model**: `[官逆]gpt-4.1-mini` / `[官逆]gpt-4.1-nano` via `https://api.gemai.cc/v1`  
**Corpus**: interim `data/raw` (6 docs) for extract; Neo4j seed graph for 20 cases  

## Extract spotcheck (C2a)

| metric | value |
|--------|-------|
| sample | 38 (`reports/triple_spotcheck_llm.summary.json`) |
| correct_rate | **100%** |
| pending_human | **0** |
| pass ≥70% | **yes** |

## 20 cases (C2b)

| metric | value |
|--------|-------|
| accuracy | **15.0%** (3/20) — `reports/poc_accuracy_llm.json` |
| offline delta | offline seed was 20/20; live partial due to rate limit |
| completed without process crash | yes (errors captured per case) |
| rate-limit / 403 | **17/20** cases after first 3 succeeded |

First 3 live cases (before 403) all **correct** (Elena Varga; Orion+Meridian; QuantumEdge products).

## Failures / themes

1. **Provider HTTP 403** on `/chat/completions` after short burst — not schema/plan bugs for those rows.  
2. Planner prompt bug fixed during this run: unescaped `{from:sq1}` in `configs/prompts/planner.md` caused `KeyError: 'from'` (fixed).  
3. Graph evidence path works offline (C3 14/20 on Neo4j `--no-llm`).

## Conclusion

- [x] **C2a** extract gate ≥70%  
- [x] **C2b** cases report archived (full 20 rows)  
- [ ] **C2b quality re-run** when LLM quota recovers (target honest live accuracy vs offline)  
- Notes: G1→G2 playbook does **not** require live accuracy ≥60%; <40% requires attribution — **attributed to gateway 403**.

## Bugs fixed en route

- `neo4j_store`: attributes stored as JSON string (Neo4j rejects nested Map properties)  
- `planner.md`: escape `{{from:sq1}}` for `.format()`  
- `.env`: re-encoded UTF-8 (GBK model-name prefix broke Settings load)
