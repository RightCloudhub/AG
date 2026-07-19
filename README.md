# AgenticGraphRAG

图谱增强的多跳推理智能问答系统（POC）。

- **PRD**: [PRD.md](./PRD.md)
- **立项建议书**: [lixiang.md](./lixiang.md)
- **实施计划**: [plan/README.md](./plan/README.md)

## 架构（四层）

1. **接入层** — CLI（POC）/ 后续 FastAPI  
2. **Agent 编排** — LangGraph StateGraph：Planner → Executor → Critic → Answer + 护栏/Memory  
3. **检索** — 向量 / 图路径 / BM25  
4. **知识层** — 文档抽取 → Schema 校验 → Neo4j + Qdrant + BM25  

## 快速开始

### 1. 环境

```bash
# Python 3.12+（本机可用 3.12/3.13/3.14）
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY（可选；离线可用 seed 三元组 + --no-llm）
```

### 2. 基础设施（可选；离线路径不需要）

```bash
docker compose up -d
# Neo4j Browser: http://localhost:7474  (neo4j / agentic-graphrag)
# Qdrant:       http://localhost:6333
```

| 模式 | 图后端 | LLM |
|------|--------|-----|
| 离线 seed | `InMemoryGraphStore`（进程内） | 不需要（`--no-llm`） |
| 持久 seed | Neo4j（需 Docker） | 不需要（`--no-llm`） |
| 全量抽取 | Neo4j（需 Docker） | 需要 `LLM_API_KEY` |

> **`--no-llm` ≠ 自动跳过 Neo4j**，但 seed 建图路径会在 Neo4j 不可用时**自动回退**到内存图并打印 Warning。  
> 强制内存：`--memory-graph`。离线评测更省事：直接 `agr-run-cases --no-llm`（自己加载 seed，无需先 `build-graph`）。

### 3. 文档接入与建图（离线 seed，无需 LLM）

```bash
# 分段
python -m agentic_graphrag.cli  # 查看入口；或使用下方脚本

# 推荐：直接调用模块入口
python -c "from agentic_graphrag.cli import ingest_main; ingest_main([])"

# seed 三元组入图（不调 LLM；Neo4j 可用则写入，否则自动内存 dry-run）
python -c "from agentic_graphrag.cli import build_graph_main; build_graph_main(['--triples','data/processed/seed_triples.jsonl','--no-llm'])"

# BM25 索引（可跳过 embedding）
python -c "from agentic_graphrag.cli import index_main; index_main(['--no-embed'])"
```

安装 entry points 后也可使用：

```bash
agr-ingest
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm
agr-index --no-embed
agr-run-cases --no-llm
```

强制进程内 dry-run（即使 Neo4j 已启动）：

```bash
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm --memory-graph
```

### 4. 带 LLM 的全量抽取

```bash
# 需要有效 LLM_API_KEY + Neo4j（无自动回退）
agr-ingest
agr-build-graph
agr-index
agr-run-cases
```

### 5. 离线评测与 G1

`--no-llm` 时 `run-cases` **自行**把 `seed_triples.jsonl` 载入 `InMemoryGraphStore`，不依赖 Neo4j，也不依赖事先跑 `agr-build-graph`。

```bash
python -m agentic_graphrag run-cases --no-llm   # 20 case → reports/poc_run.jsonl + accuracy
# 或: agr-query --no-llm "Who is the CEO of Apex Holdings?"
python -m agentic_graphrag score
python -m agentic_graphrag spotcheck            # P1-KG-05 seed baseline
# G1 memo: reports/G1_review.md  (Conditional-Go)
```

### 5b. G1 → G2 过渡（Conditional-Go 关闭）

Playbook：[plan/phases/g1-to-g2-transition.md](./plan/phases/g1-to-g2-transition.md)

| 条件 | 命令 |
|------|------|
| C1 试点语料 P1-GOV-01 | 填 `data/pilot/MANIFEST.yaml` + 语料 → `./scripts/validate_pilot_corpus.sh` |
| C2 实时 LLM | `.env` 配好 key → `./scripts/llm_live_rerun.sh` → 人工标抽检 → `score-spotcheck` |
| C3 Neo4j 回归 | `docker compose up -d neo4j` → `./scripts/neo4j_regression.sh` |

```bash
./scripts/g1_to_g2_gate.sh                 # 汇总 C1/C2/C3
./scripts/g1_to_g2_gate.sh --with-llm      # 含 live LLM
# Neo4j + offline 答案器（图走 Neo4j，不调 LLM）：
agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm
agr-run-cases --no-llm --neo4j
```

### 6. HTTP API（P2-ARCH-03）

```bash
agr-api
# 或: uvicorn agentic_graphrag.api.app:create_app --factory --port 8000

curl -s http://127.0.0.1:8000/healthz
curl -s -X POST http://127.0.0.1:8000/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Who is the CEO of Apex Holdings?","max_hops":4}'
```

默认离线：seed 三元组 + 内存图 + Mock LLM。配置 `LLM_API_KEY` 后可启用 live LLM 答案路径。

### 7. Baseline 向量 RAG（P2-EV-03）

```bash
# 临时/interim 语料上跑纯向量基线（无图、无多跳）
python -m agentic_graphrag run-baseline --no-llm
# → reports/baseline_run.jsonl + baseline_accuracy.json
```

### 8. 测试与 CI（P2-ARCH-04）

```bash
ruff check src tests scripts
ruff format --check src tests scripts
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q
```

GitHub Actions：`.github/workflows/ci.yml`（lint + unit + coverage ≥80%）。

推理链契约 Schema：`configs/schema/reasoning_chain_v1.json`（`export-reasoning-schema`）。
## 仓库布局

见 [plan/engineering/repo-structure.md](./plan/engineering/repo-structure.md)。

```
configs/          # 配置、Schema、Prompt（无密钥）
src/agentic_graphrag/
  api/            # FastAPI：envelope、POST /v1/query
  agent/          # LangGraph 循环 + 护栏/Memory
  retrieval/      # 向量/图/全文
  knowledge/      # 接入、抽取、入图
  llm/            # LLMClient Protocol + Provider + Budget
  stores/         # Repository 协议 + factory 组合根
  generation/     # 答案与推理链
data/raw/         # 示例语料
evals/datasets/   # 20 条 POC case
tests/            # 单元测试
.github/workflows # CI
```

## POC 范围与非目标

**做**：Schema V0、抽取/入图、三路检索、Agent 循环、推理链 JSON、20 case、护栏。  
**不做**：Web UI、SSE API、实体消歧、增量更新、RRF、复杂度分诊（见 PRD 里程碑阶段一）。

## 选型（默认已采纳）

| 组件 | 选型 |
|------|------|
| 图库 | Neo4j 5 Community |
| 向量 | Qdrant |
| 全文 | rank_bm25（进程内） |
| Agent | LangGraph StateGraph（ADR-005） |
| 语言 | Python + Pydantic |

## 许可证

内部项目 / 待定。
