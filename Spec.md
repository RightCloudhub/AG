# Spec — 系统级规则

本文件定义 AgenticGraphRAG 的架构、运行时、接口与安全约束。
工程流程、测试门禁和评审要求统一见 [工程规则](plan/engineering/rules.md)；
模块说明见 [架构文档](docs/ARCHITECTURE.md)，有意延期或例外记入
[债务台账](docs/reviews/IMPORTANT.md)。文档与实现不一致时须明确差异并同步修正。

**版本**：V1.3（2026-11-04）— 依 ADR-010～012 同步知识库快照、按角色用模及取证/审计契约。
下文源码路径均相对 `src/agentic_graphrag/`；可调参数以 `config.py` 加载后的有效配置为准。

## S1. 架构不变量

- **S1.1 组合根与协议边界**：运行 profile 与后端能力在 `bootstrap/` 解析，
  由 `build_stores()` 调用 `stores/factory.create_bundle()` 装配存储，应用服务在 `app/` 组装。
  存储与 LLM 契约位于 `core/ports/`；应用和领域代码不得依赖 Neo4j / Qdrant 客户端类型。
  旧路径 `api/service.py`、`stores/interfaces.py` 等仅为兼容别名，新代码使用当前模块路径。
- **S1.2 在线依赖懒加载**：在线存储在 factory 中按需加载，客户端依赖限制在存储适配器内；
  未安装在线依赖时，离线路径仍须可运行。
- **S1.3 依赖方向**：驱动层（API / CLI / eval / worker）经 `app/` 调用领域逻辑；
  领域层不得反向依赖驱动层，`core/` 不依赖项目内其他包。
  LangGraph 限定在 `agent/` 且版本锁定，不引入 LangChain 检索或链抽象。
- **S1.4 节点状态契约**：agent 图节点按“状态入 → 状态出”实现，节点单测可脱离 LangGraph 运行时。
- **S1.5 配置单入口**：新配置项进入 `AppConfig`（YAML）或 `Settings`（环境变量），
  经 `config.py` 加载；已有环境覆盖项优先于 YAML。
  除既有 `AGR_*` 开关外，不散落读取 `os.environ`；仓库数据路径经 `resolve_path()` 解析。

## S2. 离线默认与在线显式启用

- **S2.1 离线可用**：默认内存后端与无真实 LLM 的路径不需要 API Key、Docker 或网络服务。
  新功能须支持该路径，或明确声明仅在线并在债务台账挂账；单测与离线 CI 不引入在线依赖。
- **S2.2 离线可复现**：相同输入、配置与数据快照应得到相同的答案、状态与路由。
  离线评测不得受本机遗留索引或缓存污染；`eval` profile 关闭答案 / 检索缓存及本地索引加载。
- **S2.3 profile 显式选择**：`AGR_PROFILE=offline|live|hybrid|eval` 优先于旧开关
  `AGR_USE_LIVE_STORES=1`（等价于 `live`），未设置时为 `offline`。
  `live` 使用 Neo4j + Qdrant，Neo4j + 内存向量须声明为 `hybrid`；
  profile 无效、所需存储凭据缺失或后端不可达时失败，不静默切换后端。
  服务启用真实 LLM 还须 `AGR_ALLOW_LLM=1`，独立于存储 profile。
  传统 strong/light 默认要求有效 `LLM_API_KEY`，本地无鉴权服务须显式
  `LLM_AUTH_REQUIRED=false`；角色目录分别校验 provider 的 `auth_required`/`secret_ref`。
  API 启动时校验显式启用的在线能力与所需凭据。
- **S2.4 启发式隔离**：`generation/offline_heuristics/` 仅服务演示语料的确定性评测。
  不得靠添加语料特定规则掩盖生产 LLM 问题或制造评测提升。

## S3. 运行时护栏

- **S3.1 统一执行与计量**：流式和非流式查询共用局部 `agent/pipeline.py` 或
  全局 `agent/global_query.py`，先固定知识库/文档选择及快照。
  `agent/guardrails.py` 控制跳数、子问题广度、截止时间与递归上限，
  `governance/budget.py` 统一计量 LLM 调用和 token；Fast Path 升级、重试不得绕过计量。
  限制从 `AppConfig.guardrails` 加载，请求覆盖只作用于本次查询并受服务端上限约束，
  不修改全局配置；递归下限由 `min_recursion_limit()` 随跳数计算。
- **S3.2 预算预留与结算**：租户 / 用户预算经 `governance/budget_policy.py` 原子检查并预留；
  准入超限时在执行前拒绝。成功、失败与取消均结算已发生用量，释放剩余预留；
  已发送给供应商的调用不能因客户端断流而当作免费调用。
- **S3.3 可解释终止**：运行中触发护栏时保留已收集证据，返回部分结果或无法回答状态及终止原因。
  递归超限须恢复已有状态；准入拒绝和无法恢复的错误走结构化错误契约，不向客户端暴露裸异常。

## S4. 输出与接口契约

- **S4.1 推理链契约**：领域查询结果为 `core/reasoning.py` 的 `ReasoningChain`，
  JSON Schema 位于 `configs/schema/reasoning_chain_v1.json`（相对仓库根）。
  结构变更须通过 `python -m agentic_graphrag export-reasoning-schema` 同步导出，
  并保持向后兼容或升级版本；HTTP 查询视图另由 `app/contracts.py` 的 `QueryResultData` 定义。
- **S4.2 JSON envelope**：`/v1/*` 普通 JSON 业务响应遵守 `api/envelope.py` 的
  `success/data/error/meta` 契约，客户端必须检查 `success`；SSE 按事件契约消费。
- **S4.3 SSE 契约**：公开事件为
  `cache_hit / triage / thinking / sub_question / hop_done / degraded / global_progress / answer / error`，
  类型见 `api/stream_contract.py`，导出为 `configs/contracts/sse-v1.schema.json`。
  `answer` 携带推理链，`error` 携带 `code/message/query_id`；每条流最多一个终态。
  内部终止标记不对外发送。未知事件静默忽略；事件 ID 不表示支持持久化重放。
- **S4.4 审计与反馈**：应用层完成的查询须经 `governance/audit_store.py` 记录推理链，
  已保存记录可由 `GET /v1/audit/queries/{query_id}` 在所属租户/知识库内回查。
  查询和独立检索公开 `audit_status`；`audit_required=true` 时持久化失败返回
  `AUDIT_UNAVAILABLE`。原文/检索审计存于 `governance/source_store.py`，原文按服务器
  分配的证据 ID 与 SourceRef 解析，不能按客户端文件路径读取。
  `POST /v1/feedback` 关联所属租户的已有记录，不准确反馈进入人工复核队列。

## S5. 安全不变量

- **S5.1 密钥隔离**：密钥只来自环境或密钥管理服务；本地 `.env` 不入库，`configs/` 仅存非敏感配置。
- **S5.2 输入校验**：外部输入在边界按 schema 校验并快速拒绝非法值；Cypher / SQL 使用参数化查询。
- **S5.3 错误不泄密**：JSON 与 SSE 错误不包含堆栈、内部路径或密钥；
  未处理错误使用通用消息或异常类型名，不直接返回异常正文。
- **S5.4 鉴权与限流**：开启 `AGR_REQUIRE_AUTH=1` 时，`/v1/*` 须经 API Key → tenant 鉴权，
  并按路由执行 admin / operator / reader 权限检查；业务请求经租户 QPS / 并发限流。
  当前免鉴权及限流路径为 `/healthz`、`/metrics-prom`、`/docs`、`/openapi.json`、`/redoc`
  和 `/web` 前缀路径（`api/auth.py`）；新增公开路径须显式评审。
- **S5.5 前端文本安全**：自有 `web/` 代码的动态文本经 mustache / `textContent` 输出，
  禁止 `v-html` 与 `innerHTML`。
- **S5.6 提示边界**：用户输入与系统指令隔离；检索内容按不可信数据处理，不作为指令执行。
- **S5.7 租户与知识库隔离**：存储读写、缓存、任务、模型配置、摘要、审计和复核队列
  传递并校验 `tenant_id/kb_id`；省略知识库时兼容 `default`。查询固定完整 generation，
  发布受预期 revision 检查；删除标记优先于旧快照和回滚。已发送的网络帧无法撤回。
  用户预算身份默认绑定 API Key，不信任客户端 `X-User-Id`；信任该头须显式启用。

## S6. 评测与变更

离线合成评测不得替代真实 LLM / held-out 产品验收证据。
模板金标（`eval/gold_templates/`）须确定性生成，评测集与金标变更须可追溯。
门禁、Prompt 回归及规则评审流程统一遵循
[工程规则](plan/engineering/rules.md) §3、§7、§9，不在本文件重复维护阶段状态与阈值。

## 维护核验

- 架构或 profile 变更：核对 `test_core_kernel.py`、`test_bootstrap_profile.py` 与 `test_query_pipeline.py`。
- 护栏和接口变更：核对 `test_guardrails_from_config.py`、`test_reasoning_chain_schema.py`、
  `test_live_sse_stream.py` 及相关鉴权 / 租户隔离测试（均位于 `tests/unit/`）。
- 规则变更：同步本文件版本、受影响的工程规则与 `CLAUDE.md`；检查文档链接和源码路径。
