# 架构重构计划与进度（REFACTOR_PLAN）

> 分支：`refactor/arch-v2`。原始评审意见来自架构文档，每一项都已对照代码核实；本页记录核实结论和落地状态。
> 状态：✅ 已完成 · 🟡 部分完成 · ⏳ 待做 · ➖ 核实后不成立 / 不需要

历史问题与修复记录见 [CHANGELOG](../../CHANGELOG.md#refactor-fixes)。

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
| 1.3 | Guardrails 只管控制流，计量归 Budget | 🟡 | Guardrails 已通过 `BudgetTracker.check()` 查询余量；API / SSE / eval 的查询预算已读取同一配置；预算上限字段仍放在 GuardrailConfig |
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
| 5.1 | LLM 中间件管道 | ⏳ | V3-11 |
| 5.2 | 按任务路由模型 | 🟡 | 已按 `LLMRole` 记账；声明式路由见 V3-09 |
| 5.3 | 所有模型调用经网关 | ✅ | embedding 和 NLI 经过 provider；查询路径的工具规划与 embedding 计量在 V3 第一批才真正接通 |
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
| 8.2–8.4 | 组件级评测、Experiment、数据飞轮 | 🟡 | 配对 A/B（Wilson CI + 精确 McNemar，`scripts/eval_compare.py`）和金标审计（`scripts/audit_gold.py`）已完成；组件级指标见 V2 的 E-4 |

## 架构优化 V2（外部评估核对，详见 [ARCH_OPTIMIZATION_V2.md](ARCH_OPTIMIZATION_V2.md)）

| ID | 项 | 状态 | 说明 |
|---|---|---|---|
| O-1 | 评测统计 + 配对对比 | ✅ | `eval/stats.py`、`eval/compare.py` |
| O-2 | 金标审计 | ✅ | `eval/gold_audit.py`；CI 使用 `--max-flagged 0` 阻塞合并 |
| O-3 | BM25 租户泄漏 + 跨进程共享 | ✅ | 快照写入 `tenant_id`；live / hybrid 用 `SharedFileBM25Store` |
| O-4 | 事件可靠投递 | ✅ | critical 订阅者支持重试 + fail-safe；缓存失效失败时清空缓存并计数 |
| O-5 | 两阶段召回 + 重排 | ✅（默认关闭） | lexical / HTTP cross-encoder；离线 A/B 没有显著收益 |
| O-6 | 边到 chunk 溯源透出 | ✅ | `structured.source_chunk_ids` + Citation |
| O-7 | 分层 critic | ✅（默认 llm） | 确定性闸门可减少调用，实际收益待 live 配对验收 |
| O-8 | 熔断按路由拆分 + 滑窗 + 降级 | ✅（默认等价 V1） | `llm/circuit.py`、`config_llm.py` |
| P-1、E-3/E-4、R-2~R-5、S-2/S-3、K-2~K-4、G-1/G-2、L-2、X-1/X-2 | 后续批次 | ⏳ | 见 V2 §5 |

## 多智能体编排优化 V3（详见 [ARCH_OPTIMIZATION_V3.md](ARCH_OPTIMIZATION_V3.md)）

| 批次 | 内容 | 状态 |
|---|---|---|
| 第一批 | 跳过语义、递归上界、新查询重置、预算单一来源、计量补全、逐角色记账、进程级熔断、指标语义、数据最小化 | ✅ 代码复核与离线配对通过；[验证记录](../../CHANGELOG.md#arch-v3-batch-1)；live 配对待做 |
| V3-01…06 | LLM 替身 + CI、失败策略、截止时间、答案校验、计划权威、决策记录与归因 | ⏳ |
| V3-07…10 | 确定性优先规划、真并行、角色注册表、ContextAssembler | ⏳ |
| V3-11…15 | LLM 管道、控制状态、执行事件、登记表策略、运行状态持久化 | ⏳ |

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
| `test` | `mypy` + `tsc`（`web/jsconfig.json`）+ `pytest tests/unit` + 覆盖率 ≥80%（含前端测试 `test_web_claude_ui.py` / `test_web_wizard_ui.py`） |
| `eval-offline` | `agr-run-cases --no-llm --memory-graph`：poc 的 agentic 与 `--enable-triage` 两条路径各须 ≥30/31；g2_all（pilot 三元组）须 ≥196/200；probe_compositional ≥52/54、probe_negative ≥19/20；金标审计 `--max-flagged 0` 阻断。报告都作为 artifact 上传 |

全程离线：不启动 Neo4j/Qdrant，不需要 LLM key。`tests/unit/conftest.py` 在会话开始时清除 `AGR_PROFILE` 等变量，避免外部环境覆盖用例自己设定的 Profile（保证用例自行设置的 Profile 生效）。

## 兼容性

- 旧导入路径全部保留为模块别名（`sys.modules` 指向同一模块对象），monkeypatch 任意一侧效果相同。
- 行为变化：live 模式不再静默降级；`create_live_bundle(allow_memory_graph_fallback=True)` 直接报错；`agr-run-cases` 默认走 QueryApp（`--direct` 恢复旧行为）。
- 前端结构与向导测试纳入 CI，覆盖资源、安全和界面结构约束。
