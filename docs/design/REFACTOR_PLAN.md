# 架构重构计划与进度（REFACTOR_PLAN）

> 分支：`refactor/arch-v2`。原始评审意见来自架构文档，每一项都已对照代码核实；本页记录核实结论和落地状态。
> 状态：✅ 已完成 · 🟡 部分完成 · ⏳ 待做 · ➖ 核实后不成立 / 不需要

## 核实中额外发现、已修复的问题

| 问题 | 位置 | 处理 |
|---|---|---|
| live 模式会静默降级：Qdrant 初始化失败无条件回退内存向量；`QueryService.create_live` 默认 `allow_memory_graph_fallback=True`，Neo4j 不可达时 API 带空内存图启动 | `stores/factory.py`、`api/service.py` | ✅ 移除隐式回退；Neo4j 和 Qdrant 都会 ping；混合部署须声明 `AGR_PROFILE=hybrid` |
| Neo4j 下运行期实体对齐、图谱浏览、词表刷新全部空转（`getattr(graph, "list_entities")` 只有内存图实现） | `knowledge/graph_ingest.py` 等 | ✅ 实现 `Neo4jGraphStore.list_entities()` 并加入 `GraphStore` 协议 |
| 工具注册表与调度表命名不一致（`graph_paths` vs `graph_path`），注册表从未被使用 | `agent/tools/registry.py` | ✅ 统一命名，调度全部改走注册表 |

## 逐项状态

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 0-A | 应用层 `app/` | ✅ | QueryApp / IngestApp / ReviewApp；`api/` 只做 HTTP；eval 走 QueryApp；`app/` 不依赖 `api/`（单测守护） |
| 0-B | `core/` 共享内核 | ✅ | 契约（Candidate、ReasoningChain）、端口（stores / state / llm）、领域事件、IndexVersion；`core/` 不依赖其他包（单测守护） |
| 0-C | 组合根 + Profile | 🟡 | `bootstrap/`：Profile、能力矩阵、存储装配已完成；LLM / 缓存 / checkpointer 的装配仍在 `app/wiring.py`，阶段 3 迁入 |
| 0-D | 读写分离 + 领域事件 | 🟡 | GraphCommitted / IndexUpdated / ReviewDecided + 进程内总线已替换 `on_graph_commit` 回调；outbox + Redis Streams 在阶段 5 |
| 0-E | 状态平面端口 | ⏳ | 阶段 5 |
| 0-F | 治理 / 遥测以装饰器注入 | 🟡 | 已拆出 `governance/`；以端口装饰器注入在阶段 3 |
| 1.1 | 统一 QueryPipeline，非流式 = 流式消费到底 | ✅ | `agent/pipeline.py`；Fast 升级时继承证据；`poc_cases` 31/31 结果一致 |
| 1.2 | 工具全部注册，调度只依赖注册表 | ✅ | ToolSpec 含成本、超时；中间件挂载点已具备，护栏 / trace 中间件在阶段 3 |
| 1.3 | Guardrails 只管控制流，计量归 Budget | 🟡 | Guardrails 已通过 `BudgetTracker.check()` 查询余量；预算上限字段仍放在 GuardrailConfig |
| 1.4 | AgentState 显式契约 + 版本 | 🟡 | 单一定义、`state_version`、单一构造器已完成；checkpointer 走 StateStore 在阶段 5 |
| 1.5 | answer → executor 回边 | ✅ | 结构化 `evidence_gap`；每次运行最多 1 次，受 hop 上限约束 |
| 2.1 | 检索管线插件化 | ⏳ | 阶段 6 |
| 2.2 | QueryUnderstanding / QueryFrame | ⏳ | 阶段 6 |
| 2.3 | GraphTraversal 下推 | ⏳ | 阶段 6 |
| 2.4 | Evidence 按类型拆分 | ⏳ | 阶段 6 |
| 2.5 | 缓存装饰器 + IndexVersion 上移 | 🟡 | IndexVersion 已在 `core/versioning.py`，失效已由事件驱动；装饰器化在阶段 6 |
| 3.1 | 单一 IngestionPipeline | ⏳ | 核实：运行期已有 schema 门控和一层名称对齐；缺 journal / 重试 / 隔离区。阶段 4 |
| 3.2 | 事实账本 + 投影器 | ⏳ | 阶段 4 |
| 3.3 | 复核作为独立有界上下文 | 🟡 | ReviewApp + ReviewDecided 事件；处理器已按 (类型, 决策) 注册 |
| 3.4 | Schema 注册表单一来源 | ⏳ | 阶段 4 |
| 3.5 | 演示代码隔离 | ⏳ | 核实：`extract_rules` 是运行期兜底，需先定兜底策略 |
| 4.1 | 端口按类别重组 | 🟡 | 存储 / 状态 / LLM 端口已移到 `core/ports/`；Ledger 端口在阶段 4 |
| 4.2 | 跨存储写入协调 | ⏳ | 阶段 4（账本 + 投影器） |
| 4.3 | TenantScope 一等参数 | ⏳ | 阶段 5 |
| 4.4 | Profile 替代开关组合与隐式回退 | ✅ | |
| 5.1 | LLM 中间件管道 | ⏳ | 阶段 6 |
| 5.2 | 按任务路由模型 | ⏳ | 阶段 6 |
| 5.3 | 所有模型调用经网关 | ✅ | 核实：embedding 和 NLI 原本就经过 provider；补上了 embedding 调用计量 |
| 5.4 | Prompt 注册表 + 版本 | ✅ | 蕴含判定 prompt 移入 `configs/prompts/`；`chain.metadata.prompt_versions` |
| 5.5 | 保持库形态、可替换为独立网关 | ➖ | 现状即如此 |
| 6.1 | Compose → Verify → Assess | 🟡 | 已有 Verdict 分类（表述 / 缺证）；Verifier 链插件化在阶段 6 |
| 6.2 | 验证结果回流 Agent | ✅ | 见 1.5 |
| 6.3 | ReasoningChain 移到 core + ChainBuilder | 🟡 | 已移到 core；由事件流装配在阶段 3 |
| 6.4 | `audit_store.py` 移出 generation | ✅ | `governance/audit_store.py` |
| 7.1 | 统一执行事件模型 | ⏳ | 阶段 3 |
| 7.2 | Instrumented* 装饰器 | ⏳ | 阶段 3 |
| 7.3 | 治理与遥测拆开 | ✅ | `governance/` 与 `observability/` |
| 7.4 | Telemetry 端口化 | ⏳ | 阶段 3 |
| 8.1 | 评测走线上路径 | ✅ | `agr-run-cases` 默认走 QueryApp（eval Profile） |
| 8.2–8.4 | 组件级评测、Experiment、数据飞轮 | ⏳ | 阶段 3 之后 |

## 演进阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 修复核实中发现的三个隐藏问题 | ✅ |
| 1 | `core/`、`bootstrap/` + Profile | ✅（LLM 装配迁移留到阶段 3） |
| 2 | `app/` 应用层；CLI / eval 走应用层；流式与非流式合并 | ✅ |
| 3 | 执行事件模型 + 端口装饰器（trace、指标、SSE、ReasoningChain、审计统一来源） | ⏳ 下一批 |
| 4 | 单一写路径、事实账本、投影器、Schema 注册表 | ⏳ |
| 5 | 状态外置（StateStore / TenantScope / outbox），部署拆分 | ⏳ |
| 6 | LLM 中间件管道、检索管线化、GraphTraversal 下推、Evidence 分型 | ⏳ |

## CI 门禁

`.github/workflows/ci.yml` 与上述每阶段门禁一致，push 到 `main/master`、`feat/**`、`refactor/**` 以及所有 PR 时触发，三个 job 并行：

| Job | 内容 |
|---|---|
| `lint` | `ruff check` + `ruff format --check`（src tests scripts）+ `scripts/check_code_metrics.py` |
| `test` | `pytest tests/unit` + 覆盖率 ≥80%（包含 `test_web_claude_ui.py`） |
| `eval-offline` | `agr-run-cases --no-llm --memory-graph`：poc 的 agentic 与 `--enable-triage` 两条路径各须 ≥21/31；g2_all（pilot 三元组）须 ≥167/200；另外生成金标审计报告（只出报告，不拦截）。报告都作为 artifact 上传 |

全程离线：不启动 Neo4j/Qdrant，不需要 LLM key。`tests/unit/conftest.py` 在会话开始时清除 `AGR_PROFILE` 等变量，避免外部环境覆盖用例自己设定的 Profile（此前在 `AGR_PROFILE` 已导出的 shell 里 `test_live_credentials_fail_fast` 会失败）。

## 兼容性

- 旧导入路径全部保留为模块别名（`sys.modules` 指向同一模块对象），monkeypatch 任意一侧效果相同。
- 行为变化：live 模式不再静默降级；`create_live_bundle(allow_memory_graph_fallback=True)` 直接报错；`agr-run-cases` 默认走 QueryApp（`--direct` 恢复旧行为）。
- 原有问题已修复：`tests/unit/test_web_claude_ui.py` 的 7 个用例在重构前就失败（测试期望的 `web/static/*` 结构不存在）。2026-10-17 按 [UI_DESIGN.md](UI_DESIGN.md) 重建 Web 工作台后，8 个用例全部通过，CI 也不再忽略这个文件。
