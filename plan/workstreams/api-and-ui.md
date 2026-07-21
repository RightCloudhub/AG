# 工作流：服务接口与问答界面

**覆盖需求**：FR-API-01 ~ 05、FR-AN-03、NFR-06/07 · **相关阶段任务**：P2-ARCH-03、P3-PERF-06、P3-KG-04、P4-UI-*
**负责人**：检索系统工程 / 前端支援（试点阶段）
**版本**：V1.3（2026-07-21）— P5-UI-01 Vue 3 零构建重构（ADR-006）；关闭原生 JS 表述。

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

## 2. 问答 Web 界面（FR-API-05 / P4-UI-01 + P5-UI-01）— 已交付

**定位**：内部试用工具，功能优先于视觉；Claude 风格浅色对话壳，单页应用。

### 2.1 技术形态（零构建 + 钉版 Vue 3，ADR-006）

| 项 | 现状 |
|---|---|
| 框架 | **Vue 3**（钉版 3.5.13，`vue.esm-browser.prod.js` 全量构建，含浏览器内模板编译） |
| 加载策略 | 运行时 ESM 动态 import：vendor 优先（`web/static/vendor/`，完全离线）→ 钉版 jsdelivr → 钉版 unpkg 兜底 |
| 代码 | `web/index.html`（in-DOM 根模板）+ `web/static/app.js`（boot + 错误卡）+ `web/static/js/`（api.js / chain-view.js / root.js / components/） |
| 构建 | **无**：无 npm/打包器/前端依赖（见 [docs/EXTERNAL_RUNTIMES.md](../../docs/EXTERNAL_RUNTIMES.md)） |
| 挂载 | `agr-api` 静态挂载：`GET /web` 返回 `web/index.html`，资源在 `/web/static/*` |
| 注入安全 | mustache / `textContent` 仅；**禁止 `v-html` 与 `innerHTML`** |
| 结构冒烟测试 | `tests/unit/test_web_claude_ui.py`（文件全集、HTML 断言、注入安全零命中、API 流程） |

### 2.2 页面结构与功能（实现）

1. **侧栏**：品牌区 + 健康点（`/healthz` 实时） + 高级选项（`forceAgentic` 强制 Agentic、`maxHops` 最大跳数 1~10、`useStream` SSE 开关，默认开）
2. **空态**：示例问题 chips，点击即发问
3. **会话历史**：逐 turn 保留（仅展示，每次提问独立、无上下文携带）
4. **进度卡**（每 turn 独立，SSE）：分诊路由、逐 hop 子问题与结论、缓存命中、错误；流中自动展开、完成自动收起
5. **答案气泡**：正文 + 元信息行（置信度 level/score · 路由 · 状态）+ 引用角标
6. **反馈行**：每 turn 独立准确 / 不准确 + 可选原因 → `POST /v1/feedback`（关联 `query_id`）
7. **重试**：「强制 Agentic 重问」chip（`force_agentic=true`，绕缓存）
8. **中止**：流中「停止」按钮（AbortController），turn 标记 aborted 可重试
9. **推理链**（FR-AN-03）：可折叠「推理链 JSON」（含一键复制）+ 默认展开「步骤与证据」列表（hop、critic 动作、子问题、结论、证据 id、工具）
10. **输入区**：textarea 自适应高度，Enter 发送 / Shift+Enter 换行

### 2.3 推理链可视化交付（P4-UI 增强 + P5-UI-01 交互）[x]

| 能力 | 实现 |
|---|---|
| 论断内联引用**角标** | 答案正文 claim 角标（`cite-btn`）；点击高亮「论断与引用」列表（含 evidence_ids） |
| 子问题分解**树** | `plan-tree`：按 hop 的节点卡，状态色（sufficient/partial/fail），展示 depends_on |
| 图路径**可视化** | `path-list`：`explored_paths` 解析为 node/edge chips；>40 条显式 "+N 条未显示"（非编辑器） |
| 复制推理链 JSON | clipboard API，`copyState` 状态机 |
| 健康点 | 侧栏 `/healthz`，`ok`/`down`/`checking` 三色 |
| 中止 + 重试 | AbortController + `force_agentic` 重问 |

仍明确不做：图路径**编辑器**、多轮上下文、移动端适配（见 §2.5）。

### 2.4 前端必须遵守的规则

见 [engineering/rules.md](../engineering/rules.md) §8。核心：零构建不引入前端依赖；一切动态文本经 `escapeHtml`/`textContent` 注入（XSS）；只调 `/v1/*` 且遵守 envelope；改动后保持 `test_web_claude_ui.py` 结构断言同步。

### 2.5 界面明确不做（V1）

- 多轮对话上下文（每次提问独立）
- 图谱编辑能力（治理走独立审核界面 P3-KG-03）
- 移动端适配
- 图路径可视化**编辑器**

### 2.6 验证清单（不运行项目时的核查点）

- [x] `GET /web` 与 `/web/static/*` 挂载存在（`app.py`），冒烟测试断言未过期 — `tests/unit/test_web_claude_ui.py`
- [x] Vue 3 钉版 3.5.13，vendor 路径先于 CDN（`app.js` 断言）
- [x] 全前端 `v-html` 与 `.innerHTML` 零命中（注入安全断言）
- [x] `api.js` 端点全集 + SSE 六事件名（`cache_hit/triage/sub_question/hop_done/answer/error`）
- [x] `chain-view.js` 导出 `buildAnswerSegments/buildPlanNodes/parsePath/describeStreamEvent`
