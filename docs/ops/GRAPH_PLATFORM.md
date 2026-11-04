# 知识库双模、摘要与取证运行手册

本手册对应 `feat/extend` 的工程实现。验证记录见
[交付验收](../reviews/GRAPH_PLATFORM_EXTENSION.md)，任务与设计差异见
[扩展工作流](../../plan/workstreams/graph-platform-extension.md)。真实领域效果、真实模型费用
及 Claude/Cursor 客户端验收仍按该记录单独执行。

## 1. 安装与运行边界

```bash
./scripts/setup.sh --locked --extras dev,webapp,summary,contracts
source .venv/bin/activate
AGR_PROFILE=offline AGR_ALLOW_LLM=0 AGR_INGEST_INLINE=1 agr-api
```

`summary` 安装 Leiden/igraph；`contracts` 只供 SDK/MCP 联合契约测试，部署核心服务不必安装。
默认库继续支持现有离线 Schema 演示。开放抽取、LLM 登记表规划、生成式摘要与 Global
问答需要显式启用模型能力；没有能力时返回 `CAPABILITY_UNAVAILABLE`，不会产生伪摘要。

当前协调层使用**单主机本地 SQLite**。同机 API/worker 使用相同 `AGR_RUNTIME_DIR`、
部署配置和后端；不要把 SQLite 放到网络共享盘充当多主机协调服务。
离线图/向量/全文索引使用进程内对象，优先使用上述 inline worker。
独立 worker 的共享后端部署要求见 [基础 RUNBOOK](RUNBOOK.md)。

SQLite 保存 generation 清单、完整快照载荷、写入意图、来源撤回、社区及摘要任务。
已发布快照从载荷恢复独立的进程内读索引，保证读取不混用活动版本；大型知识库的载荷复制、
恢复内存与多进程吞吐尚未完成容量验收。

## 2. 建库与选择范围

在“知识库”创建并选择一个库，指定 `schema` 或 `open`、复核策略、摘要开关及已发布模型方案。
`review_policy=required` 仅支持 Schema 模式；Open 模式在表单中禁用该选项，创建/修改 API
返回 422。已有 Open 库应使用 `confidence`，不能依赖必审策略阻止开放抽取发布。
顶栏和管理视图共享选择；查询、上传、任务、复核、原文及审计均校验同一租户/知识库作用域。
租户由 API Key 或 Web 身份确定，不能在请求中自行切换。

现有客户端省略 `kb_id` 时使用 `default`。默认库按需建立，并保留旧租户存储键的初始版本
兼容。已有 UUID/版本不需重新生成；只有旧文档 ID 迁移才使用第 6 节命令。
新库为空，不复制另一个库的资料或演示 seed。

API 例子（先在本地环境设置 `AG_API_URL`、`AG_API_KEY`）：

```bash
curl --fail-with-body "$AG_API_URL/v1/knowledge-bases" \
  -H "Authorization: Bearer $AG_API_KEY" -H 'Content-Type: application/json' \
  --data '{"kb_id":"contracts","name":"合同库","build_mode":"schema","review_policy":"required","global_summary_enabled":true}'
```

模式选择：

| 配置 | 行为 |
|---|---|
| Schema + `confidence` | 使用版本化 Schema 校验，冲突/低置信度进入复核 |
| Schema + `required` | 候选先审后发布，未审事实不能被查询检索 |
| Open | 接受自由实体/关系名称、描述、别名和原文引用；不调用公司域规则 planner |
| `global_summary_enabled` | 开启独立摘要就绪状态及后台任务，不改变原始文档是否已索引 |

`PATCH /v1/knowledge-bases/{kb_id}` 需要最新 `expected_revision`。
普通重命名更新配置；模型绑定、复核策略或摘要开关变化通过影子 generation 发布。
模式不能用普通 PATCH 直接切换；开放图升级 Schema 使用第 5 节的人审流程。

## 3. 七角色模型与索引版本

部署目录配置在 `configs/default.yaml` 的 `models` 下，结构见
[model_profiles.example.yaml](../../configs/model_profiles.example.yaml)。服务启动时加载目录；
管理界面选择目录 ID，不能传入任意地址或密钥。
完整能力、鉴权和发布步骤见 [MODEL_PROFILES.md](MODEL_PROFILES.md)。

1. 为 triage、researcher、verifier、writer、extractor、embedding 绑定模型；rerank 可为空。
2. 保存草稿，测试连通性，确认所有必需能力通过，再发布。测试本身不发布。
3. 在知识库配置中绑定方案与 revision。查询快照固定该 revision；发布方案新版本不会
   静默修改已经绑定的知识库。
4. 要应用新版本，读取知识库最新 revision 后提交 PATCH，并给操作一个唯一 `operation_id`。
   构建失败保留旧 generation，发布成功后新查询才读取新配置。

真实业务调用要求 `AGR_ALLOW_LLM=1`。传统 strong/light 配置默认需要 `LLM_API_KEY`；
本地无鉴权服务需显式 `LLM_AUTH_REQUIRED=false`。目录内 provider 分别声明
`auth_required`/`secret_ref`，本地模型不会因没有全局 key 被误拒绝。

embedding 空间包含 provider、model/revision、维数和归一化约定。
已发布方案不能直接更换 embedding 目录 ID；创建新方案、测试并通过知识库影子重建切换。
有文档的库在未启用模型时拒绝 embedding 变化。相同维数不表示空间兼容。
实体描述变化而没有可用 embedder 时，不沿用旧描述向量；下一次有模型的 reindex 重建。

## 4. 文档、社区摘要与 Global 问答

“知识库 → 概括索引”显示文档总数、ready/partial、社区报告数和任务状态。
原文索引就绪与摘要就绪分开显示。无图关系的文档仍有文档摘要入口。

| API | 用途 |
|---|---|
| `GET /v1/knowledge-bases/{kb_id}/summary-index` | 当前 generation 的覆盖与就绪状态 |
| `POST /v1/knowledge-bases/{kb_id}/summary-index/build` | `{}` 幂等排队当前摘要 |
| 同上，`{"rebuild":true,"operation_id":"…"}` | 创建新 generation 后重建，需唯一操作 ID |
| `GET /v1/knowledge-bases/{kb_id}/summary-jobs` | 分页任务；可按 `status=failed` 过滤 |
| `POST /v1/knowledge-bases/{kb_id}/summary-jobs/{job_id}/retry` | 重试当前 generation 的失败任务 |

inline ingest worker 同时轮询摘要工作；启用模型的独立 ingest worker 也消费这些任务：

```bash
python -m agentic_graphrag.knowledge.ingest_worker --once
```

构建请求负责排队；须有上述 worker 才会推进。摘要任务保存租约、输入指纹与模型/Prompt
版本。worker 重启后重扫持久化工作，重复消费幂等；旧 generation 的晚到结果不能覆盖新版本。
修复模型故障后先重试失败任务，输入或 Prompt 变化则显式 rebuild。

社区更新按变更集寻找受影响组件，未变化的摘要复用。桥边、删除、合并可能扩大重算范围，
超过 `summaries.incremental_full_rebuild_ratio` 时执行全量重算，不能承诺恒定更新成本。

```bash
curl --fail-with-body "$AG_API_URL/v1/query" \
  -H "Authorization: Bearer $AG_API_KEY" -H 'Content-Type: application/json' \
  --data '{"question":"这些合同有哪些共同风险？","kb_id":"contracts","strategy":"global","audit_required":true}'
```

`strategy=auto|local|global`；`force_agentic=true` 与显式 `global` 不兼容。
`doc_ids` 可限定文档子集，省略表示库内授权范围，空列表被拒绝。
Global 使用文档/社区 map、reduce 和原文 verifier，并返回覆盖分母、已处理范围与 partial。
子集不能被表述为全库结论；摘要不足、预算耗尽或部分 map 失败必须保留不足说明。

建库与查询预算分别在 `summaries.index_max_llm_calls/index_max_tokens` 和
`query_max_llm_calls/query_max_tokens` 配置。调用、token 和角色版本写入任务/审计元数据；
供应商实际费用仍需结合部署价格、建库、重试及查询总用量核算。
离线查询的预算单位存在最低记账值，不代表发生付费 LLM 请求。

## 5. 复核与开放图升级 Schema

上传任务进入 `review` 时，在当前库的人工审核列表处理候选。
`POST /v1/review-queue/{id}/decision?kb_id=…` 接受已有人工决策契约。
执行失败后使用 `POST /v1/review-queue/{id}/retry?kb_id=…`；不能靠重复造一条决策跳过恢复状态。
待审期间进程内图重启时，retry 和 reconcile 从持久化批准记录恢复已批准事实及已完成的
抽取状态，再完成剩余决定与发布；恢复不重新调用抽取模型，也不修改既有批准回执。
确认别名/实体合并使用同一可恢复写入路径，保留来源与合并谱系。

开放图升级流程：

1. `POST /v1/knowledge-bases/{kb_id}/schema-drafts`，例如 `{"aliases":{}}`。
   草案包含实体类型、关系统计、distinct pairs、支持文档和原文样例。
2. 读取草案，人工完善 `schema`、`relation_mapping`、`entity_mapping`。
   向 `…/schema-drafts/{draft_id}/approve` 提交这些字段及 `schema_id`、`schema_version`、
   `reviewer`；服务端使用认证身份记录审核人。
3. 向 `…/schema-drafts/{draft_id}/promote` 提交 `operation_id`，在影子 generation 校验后发布。
   未映射事实会阻止发布，避免静默丢弃；输入指纹过期须重新生成/审核草案。
4. 新 Schema 保持 LLM 规划，不能自动启用旧公司域规则。规则适配需要独立验证。

Agent 建议接口 `…/review-queue/{id}/advice` 与人工决定分离。
`knowledge.agent_review.enabled` 默认 false；即使打开，也需关系白名单、独立 verifier、
同作用域且模型版本匹配的真实留出集校准、误放行置信上界和抽检共同通过。
`POST /v1/review-calibration?kb_id=…` 从已保存的建议/人工决策配对计算；
不能以合成集或未审核建议证明自动放行质量。Schema 升级始终需要人审。

## 6. 迁移、恢复与回滚

上线前记录各库的 `config_revision`、`active_generation`、Schema/model revision、文档数量
及当前任务。暂停写入后备份运行目录、文档原件、部署 YAML 与实际图/向量后端；
SQLite 使用一致性备份或在所有写进程停止后复制，不能只复制正在写入的主数据库文件。
`knowledge.sqlite3`、`model_profiles.sqlite3`、`sources.sqlite3` 和已有任务/审计存储
必须属于同一次备份；密钥在部署密钥管理中独立保管。

维护命令必须指定所属租户；`--kb` 默认 `default`：

```bash
python -m agentic_graphrag reconcile --tenant acme --kb contracts --operation-id acme-contracts-repair-20261104
python -m agentic_graphrag reindex --tenant acme --kb contracts --operation-id acme-contracts-reindex-20261104
python -m agentic_graphrag migrate-doc-ids --tenant acme --kb default --operation-id acme-default-migrate-20261104
```

在目标部署相同的 profile、模型配置和运行目录执行。旧 ID 迁移重映射任务引用并可续跑；
reconcile 回收过期任务、恢复未完成操作，再修补未登记文档；reindex 在影子版本完成后发布。
CLI 没有通用 dry-run，迁移前的清点/备份必须先完成，并在副本演练。

操作 ID 在共享 ingest task 库中须**全局唯一**，建议包含租户、知识库、动作和 UUID。
同一操作的故障恢复复用原 ID；另一作用域或不同输入使用新 ID。冲突会被拒绝。
CAS 发布冲突不自动 rebase：刷新活动 revision、核对并发变更，再以新操作构建；
不要直接修改 SQLite 的活动指针或强行把旧任务标成完成。

回滚由管理员提交：

```bash
curl --fail-with-body "$AG_API_URL/v1/knowledge-bases/contracts/rollback" \
  -H "Authorization: Bearer $AG_API_KEY" -H 'Content-Type: application/json' \
  --data '{"generation":"1","expected_revision":3}'
```

示例 revision/generation 须替换为清点记录。只有完整快照可发布，revision 不匹配返回 409。
回滚恢复配置和索引指针并使缓存失效；**删除标记优先于旧快照**，回滚不能复活已删除来源。
回滚前后对照文档、图事实、原文取证和模型 revision，再恢复业务写入。

## 7. 原文、审计与客户端

`GET /v1/evidence/{evidence_id}?kb_id=…&offset=0&limit=…` 只解析服务器分配的证据 ID。
返回 SourceRef 的文档版本/hash、规范化文本 span 及可用的页/段定位。
`offset`/`total_chars` 以 Unicode 字符计，浏览器与 SDK 均支持分页；它不接受文件路径。
状态为 `available`、`deleted`、`version_unavailable` 或 `unavailable`；不能恢复的旧来源
不伪造成精确原文。

`POST /v1/search` 返回候选、snapshot 和独立 `search_id`，不强制生成答案。
查询/检索返回 `audit_status=persisted|failed|disabled`；`audit_required=true` 时未持久化
返回 `AUDIT_UNAVAILABLE`。回查路径分别为 `/v1/audit/queries/{id}` 和
`/v1/audit/searches/{id}`，均携带 `kb_id` 并校验归属。

SSE 公开事件与 schema 见 [sse-v1.schema.json](../../configs/contracts/sse-v1.schema.json)。每条流最多一个
`answer` 或 `error` 终态，未知事件忽略；心跳是注释。断开连接会取消后续调度并结算已发调用。
事件 ID 用于诊断，**没有持久化断点重放**；客户端不要自动重发可能计费的 POST。

删除发生在查询完成前时，终态清除已撤回来源的结论，Web 同时清除本次中间推理显示。
已通过网络发送的进度帧或历史答案无法撤回；客户端保存的数据应按自身保留策略处理。
重新取证和审计回读继续检查删除状态。

- [Python SDK](../../sdk/python/README.md)：同步/异步查询、流、检索、原文与审计。
- [stdio MCP](../../integrations/mcp/README.md)：五个只读工具，通过 SDK 调公共 API；查询/检索强制审计。
- [OpenAPI](../../configs/contracts/openapi-v1.json)：导出及兼容检查命令为
  `python scripts/export_api_contracts.py --check --base-ref <base>`。

两个客户端包已有独立锁文件与 wheel/sdist，本轮未上传到包仓库。
通用官方 MCP 客户端自动化已覆盖实际工具调用和取证；Claude Desktop/Cursor 的安装配置、
真实供应商能力、Neo4j/Qdrant 迁移演练及领域留出集仍需在目标部署记录验收结果。
