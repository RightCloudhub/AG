# Spec — 系统级规则（System-Level Rules）

**定位**：本文件定义 AgenticGraphRAG **运行系统必须满足的系统级规则与不变量**（invariants）——
即无论代码如何演进，系统在架构、运行时、契约、安全四个层面都不得违反的约束。

与其它规范文档的分工与优先级：

| 文档 | 职责 | 冲突时 |
|---|---|---|
| [plan/engineering/rules.md](plan/engineering/rules.md) | 工程过程规则（代码指标 / lint / 测试 / 提交 / 评审），唯一规则汇总入口 | 工程规则以其为准 |
| 本文件（Spec.md） | 系统级规则：架构不变量、运行时护栏、输出契约、安全不变量 | 系统行为以本文件为准 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 描述性架构文档（模块地图、生命周期） | 描述性，不具强制力 |
| [docs/IMPORTANT.md](docs/IMPORTANT.md) | 债务 / 延期 / 不做事项台账 | 例外必须在此挂账 |

**版本**：V1.0（2026-07-23）。规则数值若与 `configs/default.yaml` 不一致，以配置文件为准并回改本文件。

---

## S1. 架构不变量

- **S1.1 单一组合根**：后端实现（Neo4j / Qdrant / 真实 LLM）的选择只发生在
  `stores/factory.py`（`create_offline_bundle()` / `create_live_bundle()`）与 `api/service.py`。
  应用代码只依赖 `stores/interfaces.py` 的 `GraphStore` / `VectorStore` / `FulltextStore` /
  `DocStore` 协议，任何位置不得 import Neo4j / Qdrant 客户端类型。
- **S1.2 在线依赖懒加载**：live 后端在 factory 内部惰性 import；离线路径在未安装在线依赖时必须可运行。
- **S1.3 依赖方向单向**：`web/·api/·cli/ → agent/ → retrieval/·generation/ → stores/·llm/`，
  不得出现反向 import。LangGraph 限定在 `agent/` 内且版本锁定（ADR-005）；
  不引入 LangChain 检索/链抽象。
- **S1.4 节点纯函数化**：agent 图节点实现为"状态入 → 状态出"，单测不依赖 LangGraph 运行时。
- **S1.5 配置单入口**：新配置项进 `AppConfig`（`configs/default.yaml`）或 `Settings`（env），
  经 `config.py` 读取，env 覆盖 YAML；不得散落 `os.environ` 直读（既有 `AGR_*` 开关除外）。
  路径解析一律 `resolve_path()`，与 cwd 无关。

## S2. 离线 / 在线双轨（离线是默认）

- **S2.1 离线默认可用**：任何新功能必须在 `--no-llm` + 内存后端（InMemoryGraphStore +
  seed triples + MockLLMProvider）下可运行，或显式声明"仅在线"并在 IMPORTANT.md 挂账。
  CI 与 20-case 评测路径不得引入在线依赖。
- **S2.2 离线确定性**：离线路径（相同输入 + 相同 seed 数据）必须产生确定性结果；
  离线评测可复现是 CI 的前提。
- **S2.3 在线显式开启**：真实 LLM 需 `AGR_ALLOW_LLM=1` + `LLM_API_KEY`；
  live 存储需 `AGR_USE_LIVE_STORES=1`（或 CLI `--neo4j`）。缺省一律回落离线。
- **S2.4 启发式隔离**：`generation/offline_heuristics/` 只服务演示语料的确定性评测，
  不是生产路径——不得为修复在线（live LLM）行为改它，也不得为通过离线评测往里加规则。

## S3. 运行时护栏（Guardrails）

所有 Agentic 查询全程受 `agent/guardrails.py` 约束；上限来自 `configs/default.yaml`
`guardrails:` 节（下表为当前值，**以配置为准**）：

| 护栏 | 当前值 | 说明 |
|---|---|---|
| max_hops | 4 | Agentic 推理最大跳数 |
| max_llm_calls | 16 | 单查询 LLM 调用上限 |
| max_tokens_per_query | 50000 | 单查询 token 预算（`llm/budget.py`） |
| query_timeout_seconds | 45 | 单查询墙钟超时 |
| recursion_limit | 15 | LangGraph 递归上限（≥ 2·max_hops+5） |

- **S3.1 护栏不可绕过**：任何执行路径（Fast Path 升级、重试、SSE 流式）都不得绕过护栏计数。
- **S3.2 预算原子性**：租户级预算（`llm/budget_policy.py`）预扣必须原子；超限请求快速失败，
  不得先执行后扣。
- **S3.3 优雅降级**：护栏触发（超时 / 超预算 / 递归超限）返回带已收集证据的部分结果 + 明确的
  终止原因，不得向调用方抛裸异常（先例：GraphRecursionError 恢复而非上抛）。

## S4. 输出与接口契约

- **S4.1 推理链契约**：查询输出统一为 `ReasoningChain`（`generation/trace.py`），
  其 JSON Schema 为 `configs/schema/reasoning_chain_v1.json`；结构变更必须重新导出 schema
  （`export-reasoning-schema`）并保持向后兼容或升版本号。
- **S4.2 统一 envelope**：所有 `/v1/*` 响应遵守统一 `success/error` envelope；
  客户端（含 `web/`）不得绕过 envelope 判定。
- **S4.3 SSE 事件全集**：流式接口事件类型为
  `cache_hit / triage / sub_question / hop_done / answer / error`；
  新增事件类型须同步更新 web 消费端与本条；消费端对未知事件静默忽略。
- **S4.4 审计可回查**：每次查询的 ReasoningChain 持久化到 AuditStore，
  经 `GET /v1/audit/queries/{query_id}` 可回查（AC-3）；
  `POST /v1/feedback` 必须能挂到既有 chain，不准确反馈入人工复核队列。

## S5. 安全不变量

（提交前逐项清单见 rules.md §5；本节为系统运行时必须恒成立的性质。）

- **S5.1 无硬编码密钥**：密钥只来自环境变量 / secret manager；`configs/` 只放非敏感配置；
  启动时校验所需密钥存在（在线模式）。
- **S5.2 输入边界校验**：全部外部输入 schema 校验、fail fast（NFR-07）；
  Cypher / SQL 一律参数化，不拼串。
- **S5.3 错误不泄密**：错误响应不含堆栈、内部路径、密钥；envelope 兜底只返回异常类型名。
- **S5.4 鉴权与限流**：开启 `AGR_REQUIRE_AUTH=1` 时所有 `/v1/*` 端点须经
  API Key → tenant 鉴权与 QPS / 并发限流（`api/auth.py`）；`/healthz`、`/web` 免鉴权。
- **S5.5 前端零注入**：`web/` 动态文本只经 mustache / `textContent`；
  禁止 `v-html` 与任何 `innerHTML`。
- **S5.6 提示注入防护**：用户输入与系统指令隔离；检索回来的内容一律按不可信数据处理，
  不得作为指令执行。

## S6. 评测与诚实性

- **S6.1 工程完成 ≠ 产品验收**：G1–G4 门禁（`plan/roadmap.md`）需要 live-LLM / held-out 证据；
  离线合成结果不得作为 G2+ 门禁证据，README 状态表保持该区分。
- **S6.2 金标确定性**：gold case 由模板（`eval/gold_templates/`）确定性生成；
  评测集变更须可追溯（`evals/datasets/` + ANNOTATION_SPEC.md）。
- **S6.3 Prompt 变更回归**：`configs/prompts/*.md` 变更合入前必须跑 dev 评测集回归。

## S7. 变更流程

本文件规则的增删改遵循 rules.md §9：经评审（重大者走 ADR），并在**同一变更集**内同步
版本行、根 `CLAUDE.md`（如涉及）、受影响的门禁脚本 / CI 配置。
系统行为与本文件不一致时：先修一致（改文档或改实现），再合入业务变更；
有意的例外必须在 [docs/IMPORTANT.md](docs/IMPORTANT.md) 挂账。

## 附：验证清单（本文件断言的人工核验点）

本文件不随 CI 自动校验，下列断言依赖人工 / 脚本核验：

- [ ] S3 表格数值与 `configs/default.yaml` `guardrails:` 节一致（改配置时同步）。
- [ ] S4.3 事件全集与 `agent/loop_stream.py` 实际发出的事件一致。
- [ ] S1.1 可用 `grep -rn "neo4j\|qdrant_client" src/agentic_graphrag --include="*.py"`
      抽查协议边界（命中应仅在 `stores/` 内）。
- [ ] S4.1 schema 文件与 `generation/trace.py` 模型同步（跑 `export-reasoning-schema` 比对）。
