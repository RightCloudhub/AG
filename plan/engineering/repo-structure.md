# 代码仓库结构

**原则**（遵循全局编码规范，并按仓库硬指标收紧）：按功能域组织而非按类型；多个小文件优于大文件（单文件 ≤300 行、函数 ≤50 行、嵌套 ≤3、位置参数 ≤3、CC ≤10，由 `scripts/check_code_metrics.py` 强制；新模块目标 200–300 行）；存储与 LLM 全部走抽象接口（Repository 模式，NFR-10）。

> 以 Python/FastAPI 为例（ADR-004）；若评审改选语言，结构原则不变。

```
agentic-graphrag/
├── pyproject.toml
├── README.md
├── configs/                      # 配置外置（无密钥！密钥走环境变量）
│   ├── default.yaml              # 护栏默认值、Top-K、分诊阈值等
│   ├── schema/                   # 图谱 Schema 版本化定义
│   └── prompts/                  # 各角色 Prompt 版本化管理
├── src/agentic_graphrag/
│   ├── core/                     # 共享内核：契约 · 端口 · 领域事件 · IndexVersion
│   ├── bootstrap/                # 组合根：Profile + 能力矩阵 + store 装配
│   ├── app/                      # 应用层：QueryApp / IngestApp / ReviewApp
│   ├── api/                      # 接入层
│   │   ├── routes/               # query · knowledge（docs/review/metrics/audit）· workspace · admin · deps
│   │   ├── envelope.py           # 统一响应封装（FR-API-04）
│   │   ├── auth.py               # 鉴权 + 速率限制
│   │   └── sse.py                # 流式响应（FR-API-02）
│   ├── agent/                    # Agent 编排层（核心）
│   │   ├── triage.py             # 复杂度分诊（FR-AG-01）
│   │   ├── planner.py            # 子问题 DAG（FR-AG-02）
│   │   ├── executor.py           # 工具编排（FR-AG-03）
│   │   ├── critic.py             # 反思校验（FR-AG-04）
│   │   ├── memory.py             # 状态管理（FR-AG-05）
│   │   ├── guardrails.py         # 护栏（FR-AG-06）
│   │   ├── loop.py               # LangGraph StateGraph 组装与编译（ADR-005）
│   │   └── tools/                # 工具注册表 + 外部工具接口（FR-AG-08 预留）
│   ├── retrieval/                # 检索与推理层
│   │   ├── vector.py             # FR-RT-01
│   │   ├── graph.py              # 邻居/路径/子图 + 剪枝（FR-RT-02）
│   │   ├── fulltext.py           # FR-RT-03
│   │   ├── fusion.py             # RRF + Re-ranker 接口（FR-RT-04）
│   │   └── contracts.py          # 统一候选项契约
│   ├── knowledge/                # 知识层：图谱构建管线
│   │   ├── ingest.py             # 文档接入与分段（FR-KG-01）
│   │   ├── extraction.py         # 三元组抽取门面（FR-KG-02；另有 extract_core / extract_pipeline / extract_chunk）
│   │   ├── schema_check.py       # FR-KG-03
│   │   ├── resolution.py         # 实体消歧（FR-KG-04）
│   │   ├── incremental.py        # 增量更新与冲突检测（FR-KG-05）
│   │   └── review/               # 审核队列（FR-KG-06）
│   ├── generation/               # 答案生成
│   │   ├── answer.py             # 引用绑定 + 编造拦截（FR-AN-01）
│   │   └── trace.py              # 兼容别名 → core/reasoning.py（推理链契约；FR-AN-02/04）
│   ├── stores/                   # 存储抽象（Repository 模式）
│   │   ├── interfaces.py         # 兼容别名 → core/ports/stores.py（GraphStore/VectorStore/DocStore/…）
│   │   ├── neo4j_store.py
│   │   ├── vector_store.py
│   │   └── fulltext_store.py
│   ├── llm/                      # LLMProvider 抽象 + 网关（ADR-003）
│   │   ├── provider.py
│   │   ├── budget.py             # 兼容别名 → governance/budget.py（成本核算；FR-OP-02）
│   │   ├── circuit.py            # 连续失败熔断（FR-OP-02）
│   │   └── structured.py         # 结构化输出 + 校验重试
│   ├── governance/               # 预算 / 审计账本 / 保留策略 / 脱敏
│   ├── observability/            # trace / 指标（NFR-08, FR-OP-01）
│   ├── cli/                      # 命令行驱动层（`__main__.py` 分发 14 个子命令）
│   └── webapp/                   # 可选独立 Web 应用（ADR-007）：身份库 · Cookie 会话 · 租户
├── evals/                        # 评测体系（workstreams/evaluation.md）
│   ├── datasets/                 # dev / heldout / guardrail 评测集
│   ├── baseline/                 # 纯向量 RAG Baseline
│   ├── judge.py                  # 确定性判卷（包含 / 词重叠；LLM 判卷延后）
│   └── run.py                    # 一键评测入口（FR-OP-04）
├── web/                          # Vue 3 零构建前端（index.html 控制台 + application.html 多租户应用；static/{js,css,vendor}；无 npm）
├── tests/                        # 当前只有 unit/（含走 QueryApp 的离线端到端用例）；integration/e2e 目录不存在，真实后端由 scripts/ 门禁脚本覆盖
├── scripts/                      # setup.sh（环境初始化）· doctor.py（环境体检）· 建图、索引重建、门禁与演练脚本
└── docs/                         # 运维手册、API 文档、标注规范
```

## 说明

- **POC 阶段**：允许只实现 `knowledge/`、`retrieval/`、`agent/`、`evals/` 的最小子集并以脚本驱动，但目录骨架与 `contracts.py`/`interfaces.py` 等接口定义从一开始就按此布局，避免 MVP 期大迁移。
- **配置与密钥**：`configs/` 只放非敏感配置；API Key 等一律环境变量注入并在启动时校验存在（安全规范）。
- **Prompt 即代码**：`configs/prompts/` 纳入版本管理，变更走评测回归（P5-GOV-03 机制）。
- **前端（`web/`）**：零构建 Vue 3 工作台：`static/js/`（`app.js` · `shell/` · `views/chat.js` · `chat/` · `admin/` · `store/` · `i18n.js`）、`static/css/*`（各 ≤300 行）、`static/vendor/`（Vue 3.5.13 + Lucide 子集）；视觉规范 [docs/design/UI_DESIGN.md](../../docs/design/UI_DESIGN.md) §10，规则见 rules.md §8。
- **框架边界（ADR-005）**：langgraph 依赖限定在 `agent/` 模块内，版本锁定；不引入 LangChain 检索/链抽象，检索与 LLM 调用一律走本仓库 `retrieval/`、`llm/` 的自研接口。
