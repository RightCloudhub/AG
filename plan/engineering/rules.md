# 硬性规则（必须严格遵守）

**定位**：本文件汇总对本仓库**所有代码与文档变更**具有强制力的规则，是唯一的规则汇总入口。各规则注明**强制机制**（CI / 门禁脚本 / 评审约定）。与 CLAUDE.md、各工程文档冲突时以本文件与实际门禁为准。
**版本**：V1.0（2026-07-20）

| 强制层级 | 机制 |
|---|---|
| CI（每次 push/PR 阻塞） | `.github/workflows/ci.yml`：ruff lint + format check + 单测 + 覆盖率 ≥80% |
| 门禁脚本（提交前自查） | `scripts/check_code_metrics.py`、`scripts/g1_to_g2_gate.sh` |
| 评审约定（人工核查） | 本文件 §4~§9 |

## 1. 代码硬指标（脚本强制）

对 `src/agentic_graphrag` 下所有 Python 代码生效，`python scripts/check_code_metrics.py` 必须通过：

| 指标 | 上限 |
|---|---|
| 文件行数 | ≤ 300 |
| 函数长度（非空行） | ≤ 50 |
| 嵌套深度 | ≤ 3 |
| 位置参数 | ≤ 3（超出改用 dataclass/对象，参照 `AgentRunOptions`/`ExecutorDeps` 先例） |
| 圈复杂度 | ≤ 10 |
| 魔法数字 | 禁止——提取具名常量（参照 `GraphRetrievalConfig` 把上限收进配置的先例） |

超限的既有模块必须**拆分**而非豁免（先例：`loop` / `loop_runtime` / `loop_handlers`，`executor` / `executor_plan` / `executor_dispatch`，`service` / `service_helpers` / `service_query`）。新模块目标 200~400 行。

## 2. Lint 与格式（CI 强制）

- `ruff check src tests scripts` 与 `ruff format --check src tests scripts` 零告警。
- 配置即 `pyproject.toml`：line-length 100、target py312、规则集 `E,F,I,UP,B`。不得局部关闭规则来"过检"，确需豁免时注明理由。

## 3. 测试与覆盖率（CI 强制）

- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80` 必须通过。
- **确定性优先**：单测一律不打真实 LLM / 真实网络——离线路径（内存图 + seed + MockLLM）或 stub。真实后端只进集成/回归脚本（`scripts/neo4j_regression.sh` 等）。
- 覆盖率 **omit 名单**（`pyproject.toml`）是显式记录的债务：不得为规避覆盖率悄悄扩充；新增 omit 必须写明理由并在 [docs/IMPORTANT.md](../../docs/IMPORTANT.md) §6 挂账。
- 每个 bugfix 附回归测试（先写失败测试复现，再修）。
- Prompt（`configs/prompts/*.md`）变更不阻塞单测门禁，但**必须**跑 dev 评测集回归后合入。

## 4. 提交与版本管理（评审约定）

- 提交信息：`<type>(<scope>)?: <description>`；type ∈ `feat/fix/refactor/docs/test/chore/perf/ci`。
- **不添加** "Co-Authored-By" 等尾注。
- 提交前过 §5 安全清单；门禁（G1~G4）未过不得进入下一阶段的合入。

## 5. 安全清单（提交前逐项核对）

- 无硬编码密钥：密钥一律环境变量（`.env` 不入库，模板在 `.env.example`）；`configs/` 只放非敏感配置。
- 全部外部输入 schema 校验、fail fast（NFR-07）。
- Cypher/SQL 一律参数化（实体名含引号/特殊字符不得拼串）。
- 错误响应不泄露堆栈/内部路径（envelope 兜底只返回异常类型名）。
- Web 注入点：动态文本经 `escapeHtml`/`textContent`，禁止未转义 `innerHTML` 拼接。
- 鉴权/限流路径（`api/auth.py`）相关测试保持常绿。
- Prompt 注入基础防护：用户输入与系统指令隔离，检索内容按不可信数据处理。
- 发现安全问题：**立即停止**当前工作 → 全量安全审查 → 修完 CRITICAL 再继续 → 轮换可能暴露的密钥。

## 6. 架构边界（评审约定）

- **存储走协议**：应用代码只依赖 `stores/interfaces.py` 的 `GraphStore/VectorStore/FulltextStore/DocStore`，不得 import Neo4j/Qdrant 客户端类型；实现选择只发生在组合根 `stores/factory.py`（+`api/service.py`）。在线后端 import 保持**懒加载**。
- **langgraph 限定在 `agent/`** 内且版本锁定；不引入 LangChain 检索/链抽象——检索与 LLM 调用一律走本仓库 `retrieval/`、`llm/` 接口（ADR-005）。
- 节点实现为"状态入→状态出"，单测不依赖 LangGraph 运行时。
- 新配置项进 `AppConfig`（`configs/default.yaml`）或 `Settings`（环境变量），经 `config.py` 读取；不散落 `os.environ` 直读（现存 `AGR_*` 开关除外）。
- 路径解析一律 `resolve_path()`（相对仓库根），不依赖 cwd。
- **离线路径是默认**：新功能必须在 `--no-llm` + 内存后端下可运行或显式声明仅在线；不得把在线依赖引入 CI 路径。
- 离线启发式（`generation/offline_heuristics/`）只服务演示语料的确定性评测：不得为通过评测往里加规则来"修复"生产（live LLM）问题，反之亦然。

## 7. 工程诚实与文档同步（评审约定）

- **工程完成 ≠ 产品验收**：合成/离线结果不得充当 G2+ 门禁证据；报告与 README 状态表保持这一区分。
- 有意延期、阻塞、明确不做的事项**必须**挂账 [docs/IMPORTANT.md](../../docs/IMPORTANT.md)；关闭时同步勾销（同一变更集内更新门禁 JSON / 风险状态）。
- 任务状态标记统一：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 完成 / `[-]` 取消（注明原因）。
- 改技术选型：先在 [tech-stack.md](./tech-stack.md) 追加 ADR，再动代码。
- 设计与实现出现差异：在对应 workstream 文档以「⚠ 差异」标注并挂账，不得静默改写设计假装一致。
- 不运行项目的变更：把需要人工验证的点写成文档中的**验证清单**（先例：api-and-ui.md §2.6）。

## 8. 前端（`web/`）规则

- **零构建**：不引入 npm/打包器/框架/前端依赖（POC~试点阶段约束；若阶段五引入需 ADR + 更新 [docs/EXTERNAL_RUNTIMES.md](../../docs/EXTERNAL_RUNTIMES.md)）。
- 只调用 `/v1/*` API 并遵守统一 envelope；不得绕过 `success/error` 判定。
- SSE 消费必须覆盖全部事件类型（`cache_hit/triage/sub_question/hop_done/answer/error`），未知事件静默忽略。
- 所有动态文本注入走 `escapeHtml`/`textContent`（§5 XSS 项）。
- DOM 结构变更同步更新 `tests/unit/test_web_claude_ui.py` 的结构断言。
- V1 明确不做（未改 PRD 不得实现）：多轮对话上下文、图谱编辑、移动端适配、图路径可视化编辑器。

## 9. 规则变更流程

规则的增删改须经评审（重大者走 ADR），并在**同一变更集**内同步：本文件版本行、根 `CLAUDE.md`（供 AI 协作载入）、受影响的门禁脚本/CI 配置。规则与门禁脚本不一致时，先修文档或脚本使其一致，再合入业务变更。
