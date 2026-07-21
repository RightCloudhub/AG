# AgenticGraphRAG

图谱增强的多跳推理智能问答系统。

知识图谱作结构化记忆，Agent（规划 → 执行 → 反思 → 护栏）作决策大脑；在可审计、成本可控的前提下，提升跨实体 / 跨文档多跳问答准确率与可解释性。

- **PRD**: [PRD.md](./PRD.md)
- **立项建议书**: [lixiang.md](./lixiang.md)
- **实施计划**: [plan/README.md](./plan/README.md)
- **延期 / 缺口总账**: [docs/IMPORTANT.md](./docs/IMPORTANT.md)
- **运维手册**: [docs/ops-runbook.md](./docs/ops-runbook.md)

## 当前状态

| 面 | 状态 |
|----|------|
| 阶段一～三（代码） | 大体落地：抽取 / 入图、三路检索 + RRF、Agent 循环、SSE、护栏、审计 |
| **试用 Web UI（P4-UI-01 / P5-UI-01）** | **已完成** — Vue 3 零构建对话壳（ADR-006），挂载于 `/web` |
| API 鉴权 / 限流（P4-UI-02） | **已完成** — `AGR_REQUIRE_AUTH` / `AGR_API_KEYS` |
| 效果门禁（G2/G3/G4） | 仍开：held-out live 评测、生产部署与灰度流程 |

> 工程进度 ≠ 产品验收。真域语料、live 评测签字、生产部署仍见 [docs/IMPORTANT.md](./docs/IMPORTANT.md)。

## 架构（四层）

1. **接入层** — FastAPI（`POST /v1/query`、SSE stream、知识/审计/反馈 API）+ **试用 Web**（`/web`）+ CLI  
2. **Agent 编排** — 分诊 → Fast Path / Agentic（LangGraph：Planner → Executor → Critic → Answer）+ 护栏/Memory  
3. **检索** — 向量 / 图路径 / BM25 并行 + RRF 融合 + 缓存  
4. **知识层** — 文档抽取 → Schema 校验 → 消歧/增量 → Neo4j + Qdrant + BM25  

## 试用 Web 界面（已完成）

内部试用 SPA（功能优先），**Vue 3 零构建**（钉版 3.5.13，无 npm；[ADR-006](./plan/engineering/tech-stack.md)），代码在 `web/`，由 `agr-api` 静态挂载。可选 vendor 见 [`web/static/vendor/README.md`](./web/static/vendor/README.md)。

| 能力 | 说明 |
|------|------|
| 提问区 | 输入框 + 高级选项（最大跳数、强制 Agentic、SSE 开关）+ 侧栏健康点 |
| 会话历史 | 逐 turn 保留（仅展示；每次请求仍独立、无上下文） |
| 进度区 | 每 turn 消费 `POST /v1/query/stream`：**真·增量**分诊 / 子问题 / hop；流中可「停止」 |
| 答案区 | 正文 + 论断引用角标（点击高亮）+ 置信度 / 路由 / 状态 |
| 推理链 | 子问题树 + 图路径 chips（溢出提示）+ 可复制 JSON + 步骤与证据 |
| 反馈 | 每 turn 准确 / 不准确 + 可选原因 → `POST /v1/feedback` |
| 重试 | 「强制 Agentic 重问」chip（绕缓存） |

**明确不做（V1）**：多轮对话上下文、图谱编辑、移动端适配、图路径可视化编辑器。

```bash
agr-api
# 打开 http://localhost:8000/web
# 完全离线：先 curl vendor（见 web/static/vendor/README.md）
```

相关任务：`P4-UI-01` / `P4-UI-02` / `P5-UI-01`（[phase-4-pilot](./plan/phases/phase-4-pilot.md) · [p5-ui-01-vue-refactor](./plan/phases/p5-ui-01-vue-refactor.md)）。  
结构与冒烟测试：`tests/unit/test_web_claude_ui.py`。

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

### 6. HTTP API + 试用 Web

```bash
agr-api
# 或: uvicorn agentic_graphrag.api.app:create_app --factory --port 8000

curl -s http://127.0.0.1:8000/healthz
curl -s -X POST http://127.0.0.1:8000/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Who is the CEO of Apex Holdings?","max_hops":4}'

# 浏览器打开试用界面
# http://127.0.0.1:8000/web
```

默认离线：seed 三元组 + 内存图 + Mock LLM。配置 `LLM_API_KEY` 与 `AGR_ALLOW_LLM=1` 后可启用 live LLM 答案路径。

常用环境变量（完整见 [docs/ops-runbook.md](./docs/ops-runbook.md)）：

| 变量 | 说明 |
|------|------|
| `AGR_ALLOW_LLM=1` | API 启用真实 LLM |
| `AGR_REQUIRE_AUTH=1` | 强制 API Key |
| `AGR_API_KEYS=tenant:key,...` | 租户密钥 |
| `AGR_RATE_LIMIT_QPS` | 租户 QPS（默认 20） |

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
  api/            # FastAPI：envelope、query/stream、鉴权、反馈
  agent/          # LangGraph 循环 + 护栏/Memory
  retrieval/      # 向量/图/全文 + 融合
  knowledge/      # 接入、抽取、入图
  llm/            # LLMClient Protocol + Provider + Budget
  stores/         # Repository 协议 + factory 组合根
  generation/     # 答案与推理链
web/              # 试用 Web UI（P4-UI-01 / P5-UI-01 Vue 3）：index.html + static/
data/raw/         # 示例语料
evals/datasets/   # 评测 case（poc / g2 / heldout 等）
tests/            # 单元测试（含 web UI 结构冒烟）
docs/             # 运维手册、延期总账
.github/workflows # CI
```

## 范围说明

**已交付（代码）**：Schema V0、抽取/入图、三路检索 + RRF、Agent 循环、推理链 JSON、分诊 / Fast Path、SSE、试用 Web、鉴权限流、反馈回路、审计脚手架。

**产品 / 流程仍开**：真实试点域语料签字、held-out live 达标、生产部署与灰度、G4 全套验收（AC-1~7）。

**V1 UI 明确不做**：多轮对话上下文、图谱编辑、移动端适配。

## 选型（默认已采纳）

| 组件 | 选型 |
|------|------|
| 图库 | Neo4j 5 Community |
| 向量 | Qdrant |
| 全文 | rank_bm25（进程内） |
| Agent | LangGraph StateGraph（ADR-005） |
| 语言 | Python + Pydantic |
| 试用 UI | 静态 SPA（`web/`，FastAPI 挂载） |

## 许可证

内部项目 / 待定。
