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

### 2. 基础设施

```bash
docker compose up -d
# Neo4j Browser: http://localhost:7474  (neo4j / agentic-graphrag)
# Qdrant:       http://localhost:6333
```

### 3. 文档接入与建图（离线 seed，无需 LLM）

```bash
# 分段
python -m agentic_graphrag.cli  # 查看入口；或使用下方脚本

# 推荐：直接调用模块入口
python -c "from agentic_graphrag.cli import ingest_main; ingest_main([])"

# 用 seed 三元组入图（不调 LLM）
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

### 4. 带 LLM 的全量抽取

```bash
# 需要有效 LLM_API_KEY
agr-ingest
agr-build-graph
agr-index
agr-run-cases
```

### 5. 离线评测与 G1

```bash
python -m agentic_graphrag run-cases --no-llm   # 20 case → reports/poc_run.jsonl + accuracy
python -m agentic_graphrag score
python -m agentic_graphrag spotcheck            # P1-KG-05 seed baseline
# G1 memo: reports/G1_review.md  (Conditional-Go)
```

### 6. 测试

```bash
pytest -q
```

## 仓库布局

见 [plan/engineering/repo-structure.md](./plan/engineering/repo-structure.md)。

```
configs/          # 配置、Schema、Prompt（无密钥）
src/agentic_graphrag/
  agent/          # LangGraph 循环 + 护栏/Memory
  retrieval/      # 向量/图/全文
  knowledge/      # 接入、抽取、入图
  llm/            # Provider + Budget
  stores/         # Repository 适配器
  generation/     # 答案与推理链
data/raw/         # 示例语料
evals/datasets/   # 20 条 POC case
tests/            # 单元测试
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
