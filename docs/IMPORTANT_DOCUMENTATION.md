# Important Documentation — BL-01…14 修复变更集的待验证清单

> **本文件是「未执行验证」的替代交付物。**
> 依据全局规则：本次**未运行项目、未安装依赖、未执行测试**（环境中亦无 `python` / `.venv` / `ruff`）。
> 所有正确性主张均通过**代码逻辑检查**（读代码、比对调用方、手算指标）得出。
> 下列条目必须在有可执行环境时逐条实跑确认。
>
> 变更来源：[`BUSINESS_LOGIC.md`](./BUSINESS_LOGIC.md) §4 缺口清单，落地状态见其 §7。
> 仍挂账项见 [`IMPORTANT.md`](./IMPORTANT.md) §6「业务逻辑断链」。

---

## 1. 必跑门禁（按顺序）

```bash
# 0) 环境
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 1) 硬性代码度量（文件 ≤300 行、函数 ≤50 非空行、嵌套 ≤3、位置参数 ≤3、CC ≤10）
python scripts/check_code_metrics.py

# 2) Lint / format
ruff check src tests scripts
ruff format --check src tests scripts

# 3) 单测 + 覆盖率门禁
pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q

# 4) 离线主回路（确定性，无 LLM / 无 Docker）
agr-run-cases --no-llm
python -m agentic_graphrag score

# 5) 离线 EV 演练（含增量冲突 drill）
PYTHONPATH=src python scripts/p3_ev_offline.py

# 6) 主验证包
./scripts/verify_all.sh --with-tests
```

### 1.1 本次已做的静态核对（可跳过重算，但建议复核）

| 核对项 | 方式 | 结果 |
|---|---|---|
| 所有改动文件 ≤300 行 | `wc -l` | 最大 `config.py` = **300**（恰好达限，再加一行即失败） |
| 函数 ≤50 非空行（**含 `def` 行**——度量脚本用 `lines[lineno-1:end_lineno]`） | awk 复刻 | 最长 `cli/graph_cmd.py:_extract_triples` = 48、`generation/answer.py:generate_answer` = 48、`graph_builder.py:load_triples_into_graph` = 45 |
| 位置参数 ≤3 | awk 复刻 | 无超限 |
| 行宽 ≤100 | awk | 无超限 |
| 嵌套 ≤3 | 缩进启发式 + 逐个 `elif` 人工核 | 无超限（`elif` 在该脚本中**会**增加一层，已逐处确认均 ≤3） |
| CC ≤10 | 手算 AST 规则 | 最紧的是 `ingest_worker._run_task_batch` ≈ **10**（恰好达限），其余 ≤9 |

> ⚠️ CC 与嵌套是**人工估算**，`scripts/check_code_metrics.py` 是唯一权威 —— 步骤 1 必跑。

---

## 2. 行为变更 —— 需实跑确认（回归风险）

### 2.1 会改变既有输出/语义的变更

| # | 变更 | 影响面 | 验证方式 |
|---|---|---|---|
| C-01 | `fabrication_rate` **口径收紧**：从「claim 有无 evidence_ids」改为「与运行期门禁同口径」（id 存在 + 词法命中） | `reports/*.json`、`eval/report.py` markdown、任何跟踪该指标的历史序列 | 对同一 `reports/poc_run.jsonl` 前后跑 `python -m agentic_graphrag score`，确认 `fabrication_rate` **可能升高**；`unbound_claim_rate` 应等于旧的 `fabrication_rate` |
| C-02 | 新增 `unbound_claim_rate` 字段 | `SystemMetrics.to_dict()`、报告 markdown | 断言报告 JSON 含该键；旧报告缺该键时 `_summary` 用 `.get()` 返回 `None`，不应抛异常 |
| C-03 | `load_triples_into_graph` **默认开 schema 门禁**（`schema=None` 语义反转） | 所有入图路径 | 见 §3.1 兼容性核对；断言 seed 图 23 条全部通过（`triples_rejected == 0`） |
| C-04 | PDF 上传被拒（413 + 「暂不支持」） | `POST /v1/docs`、文档承诺 | 上传 `.pdf`，断言 413；文档已同步（README / ARCHITECTURE / ENTERPRISE_READINESS / ops-runbook / CLAUDE.md） |
| C-05 | 复核项重复决策返回 **409**（原为静默覆盖） | `POST /v1/review-queue/{id}/decision` | 连续两次 approve，第二次断言 409 |
| C-06 | `RetrievalCache.retrieval_key` **加入租户维度** | 所有检索缓存条目 | 键格式变化 ⇒ 进程重启后旧内存缓存自然失效（内存缓存无持久化，无需迁移）；断言双租户同问题不互相命中 |
| C-07 | AUTO_UPDATE 冲突现在**真的删除**被取代的旧边（内存图新增 `delete_relation`） | 增量更新后的关系总数 | `test_incremental_updater_no_clear`：批 2 后关系数应为 **1**（旧为 2）。该用例只断言实体数，不会失败，但行为已变 |
| C-08 | `ConflictAction.SKIP` **已删除** | 任何 `import` 或 `match` 该成员的下游 | 全仓 grep 已确认无引用；外部脚本需自查 |
| C-09 | `RetrievalCache.persist_embeddings` → `persist_cache_stats`，产物 `data/cache/embeddings.json` → `cache_stats.json` | 运维脚本 / 监控采集 | 全仓 grep 已确认无调用方；若有外部采集器需改路径 |
| C-10 | `fuse_and_cache(..., cache_result=)` 参数**已移除**（无调用方） | `agent/executor_dispatch.py` | grep 确认仅 `executor.py` 调用且未传该参数 |
| C-11 | 规划广度上限 `guardrails.max_sub_questions = 6` | 超过 6 个子问题的 plan | 被截断的节点写入 `chain.metadata.dropped_sub_questions`；未执行节点写入 `unexecuted_sub_questions` |
| C-12 | 护栏文案变更：`max_hops exceeded (5/4)` → 追加 `; breadth stop: …` 或 `; depth stop` | 断言护栏原因文本的用例 | `tests/unit/test_guardrails_memory.py:24` 只断言 `"max_hops" in reason`，**不受影响**；其他断言全等文本的地方需检查 |

### 2.2 修复类变更（应带来可观测的正向变化）

| # | 变更 | 期望现象 |
|---|---|---|
| F-01 | SSE `_initial_state` 补 `tenant_id`（BL-04） | 流式路径不再以 `tenant_id=None` 检索（四个后端均把 `None` 当「不过滤」） |
| F-02 | `_REPAIR_HINTS` 按具体失败原因生成重生成提示（BL-02） | 词法支撑失败时的重试不再是确定性浪费 |
| F-03 | CJK 二元组切词（BL-02） | 纯中文 claim 不再必然落 `citation_fallback` |
| F-04 | 图证据对象锚定（BL-13） | 引用了「关系不符但主语相同」的边时，门禁应拒绝 |
| F-05 | `IngestTaskStore.requeue_stale()`（BL-06） | worker 崩溃后任务可被重新取到 |
| F-06 | `_build_cli_worker()` 注入 `task_store`（BL-05） | `--once` 会消费队列而非退回旧的 doc-store 扫描 |
| F-07 | 审计落库失败 `logger.error`（BL-10） | 不再静默丢失合规记录 |
| F-08 | 全量 embed 失败 ⇒ 任务判 `failed`（BL-10） | 不再以 `done` + 0 向量收场 |
| F-09 | 上传分片读取（256KB）+ 严格 UTF-8（BL-08） | 超大文件不再先整体入内存；非 UTF-8 被拒 |
| F-10 | `/v1/feedback` 与 `/v1/review-queue/*/decision` 一律 404（BL-09） | 不可作存在性探测 |
| F-11 | `/v1/graph/entities` 加 RBAC + 租户过滤（BL-09） | 关闭鉴权时行为不变（`require_role` 在 `AGR_REQUIRE_AUTH` 未开时 no-op） |
| F-12 | `BatchResult.conflicts_kept` + `accepted` 取实际 upsert 数（BL-11） | 批次计数守恒 |
| F-13 | `ReviewQueue._load` 跳过损坏行（BL-11） | 单行损坏不再拖垮 `QueryService` 构造 |
| F-14 | `_TASKS` 有界 `OrderedDict`（上限 500）（BL-11） | 进程生命周期内不再无限增长 |

---

## 3. 兼容性核对（已静态确认，仍建议实跑复核）

### 3.1 「默认开门禁」对既有三元组的影响（C-03）

`load_triples_into_graph(schema=None)` 现在加载 `configs/schema/domain_v0.yaml` 并按
`knowledge.extract_confidence_threshold`（0.5）过滤。已逐条核对以下来源**全部合规**：

| 来源 | 条数 | 关系/类型 | 最低置信度 |
|---|---|---|---|
| `data/processed/seed_triples.jsonl` | 23 | `CEO_OF`/`COMPETES_WITH`/`PARENT_OF`/`PARTICIPATED_IN`/`PRODUCES`/`SUBSIDIARY_OF`/`SUPPLIES`/`SUPPLIES_FOR`/`WORKED_AT`，head/tail 类型与 schema 逐对匹配 | 0.80 |
| `knowledge/pilot_triples.py` | 生成式 | 同上 + `WORKS_AT` | 0.95 |
| `eval/p3_ev.py` drill | 2 seed + 3 batch | `SUBSIDIARY_OF`/`CEO_OF` | 0.60 |
| `tests/unit/test_p3_ev.py` | 2 | `PARENT_OF`/`CEO_OF` | 0.90 |
| `tests/unit/test_phase3_kg_and_budget.py` | 2 | `PARENT_OF` | 0.60 |
| `tests/unit/test_multihop_graph_evidence.py` / `test_graph_recursion_fallback.py` | 读 seed 文件 | 同 seed | 0.80 |

**验证点：** 断言 `stats["triples_rejected"] == 0`。若真域语料接入后出现拒绝，
`api/service.py:_load_seed_graph` 会打 WARNING（含 `rejection_reasons`），不静默。

**已 gate 的调用方**（必须传 `pre_gated=True`，否则重复计数）：
`cli/graph_cmd.py:_write_graph`（`_gate_and_report` 先 gate）、`knowledge/incremental.py:_apply_locked`（`_gate` 先 gate）。

### 3.2 现有用例的预期结果（静态推演，需实跑确认）

| 用例 | 推演结论 |
|---|---|
| `test_enterprise_readiness.py::test_upload_allowed_extension` | `.md` → 200 ✔ |
| `test_enterprise_readiness.py::test_upload_disallowed_extension` | `.exe` → 413 ✔ |
| `test_enterprise_readiness.py::test_upload_too_many_files` | 25 个 → 413 ✔ |
| `test_graph_entities_api.py::test_graph_entities_endpoint_returns_seed_entities` | RBAC 在未开鉴权时 no-op；`_tenant_matches` 允许空租户的 seed 记录 ⇒ 仍返回非空 ✔ |
| `test_phase3_triage_fusion_cache.py`（`set_retrieval`/`get_retrieval` 无租户参数） | 两侧同为 `tenant_id=None` ⇒ 同键 ✔ |
| `test_phase3_kg_and_budget.py::test_incremental_updater_no_clear` | 实体数 2 → 3，断言 `e2 >= e1` ✔；关系数由 2 变 1（见 C-07） |
| `test_phase3_kg_and_budget.py::test_review_queue_decide` | `tenant_id=None` ⇒ `_tenant_allows` 放行；`pending` ⇒ 不触发 409 ✔ |
| `test_p3_ev.py::test_incremental_drill_smoke` | `post_query_ok` 依赖 `HoldCo` 邻居 ≥1（Acme/Elena/Nova 均在）✔；`batch_accepted` = 1 |
| `test_guardrails_memory.py:24` | 只断言 `"max_hops" in reason` ⇒ 不受文案追加影响 ✔ |

### 3.3 需要新增的用例（当前**缺测**）

| # | 用例 | 断言 | 对应 |
|---|---|---|---|
| N-01 | CJK claim 词法支撑 | 中文 claim + 中文证据 ⇒ `claims_lexically_supported` 为 True | BL-02 / V-03 |
| N-02 | 对象锚定拒绝 | 证据 `A -[FOUNDED_BY]-> X`（`structured.kind="neighbor"`, `tail="X"`），claim「A 的 CEO 是 Y」引用该 id ⇒ `validate_answered_claims` 返回 `"claim text not supported by cited evidence content"` | BL-13 / V-18 |
| N-03 | path 锚定 | `structured.kind="path"` 且 claim 只命中 1 个节点 ⇒ 拒绝；命中 ≥2 ⇒ 通过 | BL-13 |
| N-04 | 缓存租户隔离 | 租户 A `set_retrieval` 后，租户 B `get_retrieval` 同问题返回 `None` | BL-04 / V-06 |
| N-05 | 流式租户隔离 | 租户 B 走 `/v1/query/stream` 不应看到租户 A 的 chunk | BL-04 / V-05（**最高优先**） |
| N-06 | `requeue_stale` | 造一个 `updated_at` 超期的 `EXTRACTING` 任务 ⇒ 回到 `QUEUED` 且出现在 `pending()` | BL-06 / V-08 |
| N-07 | worker 消费队列 | `_build_cli_worker()` 返回的 worker 其 `task_store is not None` | BL-05 / V-07 |
| N-08 | 无扩展名上传 | 文件名 `payload`（无点）⇒ 413 | BL-08 / V-09 |
| N-09 | 超大文件 | 100MB 上传 ⇒ 413，且进程内存不应达到 100MB 量级 | BL-08 / V-10 |
| N-10 | 非 UTF-8 上传 | 含 `0xFF` 字节的 `.txt` ⇒ 413 | BL-08 |
| N-11 | 跨租户复核决策 | 租户 A 的 operator 对租户 B 的 item_id ⇒ 404 | BL-09 / V-11 |
| N-12 | 重复决策 | 第二次 decision ⇒ 409 | BL-11 / C-05 |
| N-13 | feedback 存在性一致 | 「他租户 query_id」与「随机 query_id」返回同为 404 | BL-09 / V-13 |
| N-14 | `/v1/graph/entities` 租户过滤 | 双租户实体共存时返回集合正确 | BL-09 / V-12 |
| N-15 | 批次计数守恒 | 含 KEEP_OLD 的批次：`accepted + rejected + conflicts_auto + conflicts_review + conflicts_kept` == 输入数 | BL-11 / V-14 |
| N-16 | 广度上限 | 桩化产出 8 个子问题的 plan ⇒ `sub_questions` 只剩 6，`metadata.dropped_sub_questions` 有 2 条 | BL-12 / V-16 |
| N-17 | 护栏文案区分 | planned=6 > max_hops=4 ⇒ reason 含 `breadth stop`；planned ≤ max_hops ⇒ 含 `depth stop` | BL-12 |
| N-18 | 内存图删边 | `InMemoryGraphStore.delete_relation(id)` 删除后 `counts()["relationships"]` 减 1，跨租户同 id 一并删除 | BL-11 / C-07 |
| N-19 | 审计落库失败可见 | 注入抛异常的 audit store ⇒ `save_chain_audit` 返回 False 且打日志，查询本身仍成功 | BL-10 |
| N-20 | 全量 embed 失败 | `embed_fn` 全抛 ⇒ `IngestResult.error` 为 `embed_failed_all (n/n)`，任务判 `failed` | BL-10 |
| N-21 | 损坏复核队列行 | 队列文件插入一行 `{bad json` ⇒ `ReviewQueue` 构造成功且跳过该行 | BL-11 |
| N-22 | `_TASKS` 上界 | 创建 600 个任务 ⇒ `len(_TASKS) == 500` | BL-11 |
| N-23 | 流式 `request_id` | SSE 路径落库的 chain 其 `metadata.request_id` 非空 | BL-11 |
| N-24 | `unbound_claim_rate` 兼容旧行 | 无 `chain.metadata.evidence` 的历史行 ⇒ `_claims_fail_gate` 返回 False（不冤枉旧数据） | C-01 |

---

## 4. 仍未实施（**不是**遗漏，是需先立项）

| 缺口 | 为什么本次不做 |
|---|---|
| **BL-01** 上传 → 图谱无运行期通路 | 功能新建：需要「抽取 → 消解 → 冲突 → 入图」的运行期编排 + 幂等/回滚语义。审计文件自身（§6）也标注为「需先立项」，且**须在 BL-14 决策之后**——否则入图通路建成后再补时间字段要回填全量图谱 |
| **BL-03** 复核决策无执行器 | 同上：批准需产生图谱副作用。本次只补齐决策自身的正确性（终态保护 409、租户校验 404） |
| **BL-14** 图谱无时间维度 | 数据模型变更（schema + 抽取 prompt + 冲突打分三处联动）。按仓库约定，技术选型变更**须先写 ADR**（`plan/engineering/tech-stack.md`）再动代码 |
| **BL-12 的并发分支执行** | 需要仓库尚不具备的并发编排（executor 目前线性推进 `current_index`）。本次只修「静默丢弃 + 文案误导」这一有害部分；收益应先由 V-17 量化 |
| **BL-13 的真 NLI 判定** | 需引入模型依赖（禁止本次下载）。另：评测侧**无法复现**对象锚定层——`_attach_evidence_catalog` 只持久化 `id`/`content`，丢弃 `structured` |

---

## 5. 安全检查表（提交前逐项确认）

| 项 | 本变更集状态 |
|---|---|
| 无硬编码密钥 | ✔ 未引入任何字面量密钥；新增配置项 `guardrails.max_sub_questions` 为数值 |
| 用户输入校验 | ✔ 上传扩展名/大小/编码三重校验收紧（BL-08）；`limit`/`offset` 被 clamp 到 `[0, MAX_ENTITY_PAGE]` |
| SQL 注入 | 不适用（无 SQL；Neo4j 走参数化 Cypher，本次未改） |
| XSS | 不适用（本次未改 `web/`；`v-html`/`innerHTML` 仍禁用） |
| CSRF | 不适用（无 Cookie 会话，API-Key 鉴权） |
| 认证/授权 | ✔ `/v1/graph/entities` 补 RBAC 依赖；复核决策补租户校验 |
| 限流 | ✔ 沿用 `AuthRateLimitMiddleware`（全局），本次未新增绕过路径 |
| 错误信息不泄露 | ✔ 「他租户」与「不存在」统一 404（消除存在性探测）；`decide` 的 409 只回状态字符串，不回内容 |

---

*本文件随 BL 修复变更集同批提交。任一条目实跑通过后，请在此勾记并同步 [`BUSINESS_LOGIC.md`](./BUSINESS_LOGIC.md) §7。*
