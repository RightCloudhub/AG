# 工作流：服务接口与问答界面

**覆盖需求**：FR-API-01 ~ 05、FR-AN-03、NFR-06/07 · **相关阶段任务**：P2-ARCH-03、P3-PERF-06、P3-KG-04、P4-UI-*
**负责人**：检索系统工程 / 前端支援（试点阶段）
**版本**：V1.3（2026-07-21）— P5-UI-01 Vue 3 零构建重构 + 交互增强（会话历史 / 中止 / 逐 turn 反馈 / 健康点）；ADR-006。

实现入口：`src/agentic_graphrag/api/`（`app.py` 组装与异常处理、`routes/query.py`、`routes/knowledge.py`、`auth.py`、`envelope.py`、`sse.py`、`errors.py`、`service*.py`）；前端 `web/`。

## 1. API 设计与实现现状

### 1.1 统一响应封装（FR-API-04）[x]

```json
{ "success": true, "data": { }, "error": null, "meta": { "total": 0, "page": 1, "limit": 20 } }
```

错误时 `success=false`，`error` 含 `code`（机器可读枚举）与 `message`（用户友好，不泄露内部细节，NFR-06）。实现：`envelope.py` + `app.py` 三级异常处理器（`ApiError` / 入参校验 / 兜底 500，兜底仅返回异常类型名，不含堆栈）。

错误码全集（`errors.py`）：`INVALID_INPUT`、`BUDGET_EXCEEDED`、`TIMEOUT_PARTIAL`、`RATE_LIMITED`、`UNAUTHORIZED`、`INTERNAL_ERROR`、`SERVICE_UNAVAILABLE`。

### 1.2 查询 API（FR-API-01）[x]

`POST /v1/query`（`routes/query.py`）

| 入参 | 类型 | 说明 |
|---|---|---|
| question | string，必填 | 1~2000 字符，schema 校验（NFR-07） |
| max_hops | int，可选 | 上限受服务端硬顶约束 |
| timeout_ms | int，可选 | 同上 |
| force_agentic | bool，可选 | 跳过分诊强制 Agentic（调试用；同时绕过答案缓存） |

出参 `data` = [推理链契约 JSON](./agent-orchestration.md#3-推理链契约fr-an-02p2-ag-06-定稿)（含 answer、claims+引用、status、cost、`metadata.confidence`）。服务默认**离线**装配（内存图 + seed 三元组 + MockLLM，`service.build_default_service`）；`AGR_ALLOW_LLM=1` 且配置有效 `LLM_API_KEY` 时启用真实 LLM。

### 1.3 流式响应（FR-API-02 / P3-PERF-06）[x]

`POST /v1/query/stream`（SSE，`sse.py` 逐帧编码）。事件序列（`service_stream.py` + `agent/loop_stream.py`）：

| 事件 | 载荷 | 触发 |
|---|---|---|
| `cache_hit` | `{query_id}` | 命中答案缓存（随后直接发 `answer`；`force_agentic` 时绕过） |
| `triage` | 分诊决策 JSON | 非缓存查询开头；`force_agentic` 时发合成帧 `{route:agentic, rule_hit:force_agentic}` |
| `sub_question` | `{hop, sub_question}` | Executor 节点完成时（LangGraph `stream(updates)` 实时） |
| `hop_done` | `{hop, conclusion, critic_action}` | Critic 节点完成时 |
| `answer` | 完整推理链 JSON | 结束（finalize + audit/cache/metrics 之后） |
| `error` | `{code, message}` | 异常终止（`ApiError` 保留错误码；其余仅异常类型名） |

> **实现说明**：设计文档写的是 `astream_events`；同步 FastAPI 生成器用 LangGraph `graph.stream(..., stream_mode="updates")` 做节点级映射，语义等价（每个节点完成后立即 yield），见 `agent/loop_stream.py`。

### 1.4 知识管理 API（FR-API-03）[x]

均在 `routes/knowledge.py`，前缀 `/v1`：

| 端点 | 用途 |
|---|---|
| `POST /docs` | 批量上传（含来源元数据） |
| `GET /ingest-tasks/{task_id}` | 抽取任务状态 |
| `GET /review-queue` · `POST /review-queue/{item_id}/decision` | 审核队列（P3-KG-03） |
| `GET /audit/queries/{query_id}` | 推理链审计回查（FR-AN-04 / P3-AN-01） |
| `POST /feedback` | 反馈回路（FR-OP-03）：负反馈入 ReviewQueue 并写回 audit metadata |
| `GET /metrics` | 监控指标（P4-REL-03） |
| `GET /graph/entities` | 图实体浏览脚手架（P5-CAP） |

### 1.5 横切要求 [x]（部分）

- 鉴权 + 速率限制（P4-UI-02，`auth.py` 中间件）：`AGR_REQUIRE_AUTH=1` 强制 API Key；`AGR_API_KEYS=tenant:key,...`；租户级 `AGR_RATE_LIMIT_QPS`（默认 20）与并发 `AGR_RATE_LIMIT_CONCURRENT`（默认 10）。
- 全部入参 schema 校验，fail fast（NFR-07）[x]。
- 请求生成/携带 `query_id`，贯穿推理链与审计存储（NFR-08）[x]。
- ⚠ 租户**数据级**隔离核查（图/文档按租户切分）仍在 P4-REL-01（运维侧），代码当前仅贯穿 principal。

## 2. 问答 Web 界面（FR-API-05 / P4-UI-01 · P5-UI-01）— 已交付

**定位**：内部试用工具，功能优先于视觉；Claude 风格浅色对话壳，Vue 3 零构建单页应用（ADR-006）。

### 2.1 技术形态（零构建 + 钉版 Vue 3）

| 项 | 现状 |
|---|---|
| 框架 | Vue 3.5.13（Options API；in-DOM 根模板 + 组件 string template）；[ADR-006](../engineering/tech-stack.md) |
| 加载 | vendored-first → 钉版 jsDelivr → 钉版 unpkg；**无** npm / 打包器（见 [docs/EXTERNAL_RUNTIMES.md](../../docs/EXTERNAL_RUNTIMES.md) + `web/static/vendor/README.md`） |
| 模块 | `app.js`（boot）· `js/root.js` · `js/api.js` · `js/chain-view.js` · `js/components/{index,widgets,answer-turn}.js` |
| 样式 | `app.css`（tokens/壳）· `chat.css`（线程/composer）· `panels.css`（反馈/树/路径）；各 ≤300 行 |
| 挂载 | `agr-api` 静态挂载：`GET /web` → `index.html`，资源 `/web/static/*` |
| SSE 消费 | `js/api.js`：`fetch` + `ReadableStream` 手工解析（**非** `EventSource`，因需 POST + JSON body） |
| 结构冒烟测试 | `tests/unit/test_web_claude_ui.py`（文件全集、钉版、注入安全、静态资源 200） |

### 2.2 页面结构与功能（实现）

1. **侧栏**：品牌区 + `/healthz` 健康点 + 高级选项（`forceAgentic` / `maxHops` 1~10 / `useStream`，默认开）
2. **空态**：示例问题 chips，点击即发问
3. **对话流**：逐 turn 保留（多轮**仅展示**，每次请求独立、无上下文携带）
4. **进度卡**（每 turn）：流中自动展开、完成自动收起；分诊 / hop / 缓存命中 / 错误
5. **答案卡**（`answer-turn`）：正文 + 元信息行 + 论断高亮角标 + 反馈状态机
6. **推理链**（FR-AN-03）：子问题树 · 图路径 chips（溢出显式 "+N 条未显示"）· 步骤与证据 · 可复制 JSON
7. **中止 / 重试**：流中「停止」（AbortController）；错误/完成均可「强制 Agentic 重问」
8. **输入区**：textarea 自适应高度，Enter 发送 / Shift+Enter 换行；busy 时停止按钮替换发送

### 2.3 推理链可视化交付（P4-UI 增强 + P5 增强）[x]

| 能力 | 实现 |
|---|---|
| 论断内联引用**角标** | `buildAnswerSegments` + `cite-btn`；点击高亮对应论断（`claim-active`）并滚动 |
| 子问题分解**树** | `plan-tree` 组件：`buildPlanNodes`，状态色（sufficient/partial/fail），展示 depends_on |
| 图路径**可视化** | `path-list`：`buildPathRows` → node/edge chips；`MAX_PATH_ROWS=40` 后显式溢出提示 |
| 复制推理链 | 折叠区「复制」→ `navigator.clipboard`（localhost secure context） |

仍明确不做：图路径**编辑器**、多轮上下文、移动端适配（见 §2.5）。

### 2.4 前端必须遵守的规则

见 [engineering/rules.md](../engineering/rules.md) §8。核心：零构建 + 仅钉版 Vue 3（ADR-006）；动态文本 mustache/`textContent`，禁 `v-html`/`.innerHTML`；只调 `/v1/*` 且遵守 envelope；改动后保持 `test_web_claude_ui.py` 同步。

### 2.5 界面明确不做（V1）

- 多轮对话上下文（每次提问独立）
- 图谱编辑能力（治理走独立审核界面 P3-KG-03）
- 移动端适配
- 图路径可视化**编辑器**

### 2.6 验证清单

工程冒烟（CI）：`tests/unit/test_web_claude_ui.py`。浏览器与离线 vendor 等人工项见执行计划 [phases/p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md) §7。

- [x] `GET /web` 与 `/web/static/*` 挂载存在；文件全集与钉版断言
- [x] 请求体字段与 `QueryRequest` schema 一致（question/force_agentic/max_hops）
- [x] SSE 分支覆盖 §1.3 全部事件类型（含 `cache_hit`）— 未知事件静默忽略
- [x] 反馈按 turn 携带 `query_id` 且处理 `success=false`
- [x] 全前端 `v-html` / `.innerHTML` 零命中
