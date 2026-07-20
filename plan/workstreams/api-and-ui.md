# 工作流：服务接口与问答界面

**覆盖需求**：FR-API-01 ~ 05、FR-AN-03、NFR-06/07 · **相关阶段任务**：P2-ARCH-03、P3-PERF-06、P3-KG-04、P4-UI-*
**负责人**：检索系统工程 / 前端支援（试点阶段）
**版本**：V1.1（2026-07-20）— 补充实现现状与前端交付说明；设计与实现的差异以「⚠ 差异」标注并同步挂账 [docs/IMPORTANT.md](../../docs/IMPORTANT.md)。

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

### 1.3 流式响应（FR-API-02 / P3-PERF-06）[x] ⚠ 差异

`POST /v1/query/stream`（SSE，`sse.py` 逐帧编码，带自增 `id`）。事件序列（`service_query.py`）：

| 事件 | 载荷 | 触发 |
|---|---|---|
| `cache_hit` | `{query_id}` | 命中答案缓存（随后直接发 `answer`；`force_agentic` 时绕过） |
| `triage` | 分诊决策 JSON | 每次非缓存查询开头 |
| `sub_question` | `{hop, sub_question}` | 每个推理步骤 |
| `hop_done` | `{hop, conclusion, critic_action}` | 每个推理步骤 |
| `answer` | 完整推理链 JSON | 结束 |
| `error` | `{code, message}` | 异常终止（`ApiError` 保留错误码；其余仅异常类型名） |

> ⚠ **差异（挂账）**：原设计要求由 LangGraph `astream_events` 实时映射生成进度事件；当前实现为**查询执行完成后按 steps 回放**（`_stream_run` 先跑完 `execute_run_query` 再逐步 yield）。对短查询体验等价，长查询无真实时进度。真·增量流式仍延期，见 [docs/IMPORTANT.md](../../docs/IMPORTANT.md) §6。

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

## 2. 问答 Web 界面（FR-API-05 / P4-UI-01）— 已交付

**定位**：内部试用工具，功能优先于视觉；Claude 风格浅色对话壳，单页应用。

### 2.1 技术形态（零构建）

| 项 | 现状 |
|---|---|
| 代码 | `web/index.html` + `web/static/app.js`（原生 JS，IIFE，无框架）+ `web/static/app.css` |
| 构建 | **无**：无 npm/打包器/前端依赖（见 [docs/EXTERNAL_RUNTIMES.md](../../docs/EXTERNAL_RUNTIMES.md)）；改完刷新即生效 |
| 挂载 | `agr-api` 静态挂载：`GET /web` 返回 `index.html`，资源在 `/web/static/*` |
| SSE 消费 | `fetch` + `ReadableStream` 手工解析 `event:`/`data:` 帧（**非** `EventSource`，因需 POST + JSON body） |
| 结构冒烟测试 | `tests/unit/test_web_claude_ui.py`（文件存在性、关键 DOM id、路由挂载） |

### 2.2 页面结构与功能（实现）

1. **侧栏**：品牌区 + 高级选项（`forceAgentic` 强制 Agentic、`maxHops` 最大跳数 1~10、`useStream` SSE 开关，默认开）
2. **空态**：示例问题 chips，点击即发问
3. **对话流**：用户消息气泡（多轮**展示**，但每次提问独立、无上下文携带）
4. **进度卡**（SSE）：分诊路由、逐 hop 子问题与结论、缓存命中、错误
5. **答案气泡**：正文 + 元信息行（置信度 level/score · 路由 · 状态）
6. **推理链**（FR-AN-03）：可折叠「推理链 JSON」（query_id/route/status/claims/cost/explored_paths）+ 默认展开「步骤与证据」列表（hop、critic 动作、子问题、结论、证据 id、工具）
7. **反馈行**：准确 / 不准确 + 可选原因 → `POST /v1/feedback`（关联最近一次 `query_id`）
8. **输入区**：textarea 自适应高度，Enter 发送 / Shift+Enter 换行

### 2.3 设计 vs 实现差异（挂账，见 IMPORTANT.md §5/§6）

| 原设计 | 实现现状 |
|---|---|
| 论断内联引用**角标**，点击展开原文片段 | 证据以 evidence_ids 列在步骤卡；无角标与原文展开 |
| 子问题分解**树**（节点状态） | 扁平步骤列表 |
| 图路径**可视化**（可点节点/边） | `explored_paths` 仅在折叠 JSON 中可见 |

### 2.4 前端必须遵守的规则

见 [engineering/rules.md](../engineering/rules.md) §8。核心：零构建不引入前端依赖；一切动态文本经 `escapeHtml`/`textContent` 注入（XSS）；只调 `/v1/*` 且遵守 envelope；改动后保持 `test_web_claude_ui.py` 结构断言同步。

### 2.5 界面明确不做（V1）

- 多轮对话上下文（每次提问独立）
- 图谱编辑能力（治理走独立审核界面 P3-KG-03）
- 移动端适配
- 图路径可视化**编辑器**

### 2.6 验证清单（不运行项目时的核查点）

- [ ] `GET /web` 与 `/web/static/*` 挂载存在（`app.py`），冒烟测试断言未过期
- [ ] `app.js` 请求体字段与 `QueryRequest` schema 一致（question/force_agentic/max_hops）
- [ ] SSE 分支覆盖 §1.3 全部事件类型（含 `cache_hit`）
- [ ] 反馈提交携带 `query_id` 且处理 `success=false`
- [ ] 新增 DOM 注入点均走 `escapeHtml`/`textContent`
