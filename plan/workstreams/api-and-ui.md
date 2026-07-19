# 工作流：服务接口与问答界面

**覆盖需求**：FR-API-01 ~ 05、FR-AN-03、NFR-06/07 · **相关阶段任务**：P2-ARCH-03、P3-PERF-06、P3-KG-04、P4-UI-*
**负责人**：检索系统工程 / 前端支援（试点阶段）

## 1. API 设计

### 1.1 统一响应封装（FR-API-04）

```json
{ "success": true, "data": { }, "error": null, "meta": { "total": 0, "page": 1, "limit": 20 } }
```

错误时 `success=false`，`error` 含 `code`（机器可读枚举）与 `message`（用户友好，不泄露内部细节，NFR-06）。

### 1.2 查询 API（FR-API-01，阶段二）

`POST /v1/query`

| 入参 | 类型 | 说明 |
|---|---|---|
| question | string，必填 | 1~2000 字符，schema 校验（NFR-07） |
| max_hops | int，可选 | 上限受服务端硬顶约束 |
| timeout_ms | int，可选 | 同上 |
| force_agentic | bool，可选 | 跳过分诊强制 Agentic（调试用） |

出参 `data` = [推理链契约 JSON](./agent-orchestration.md#3-推理链契约fr-an-02p2-ag-06-定稿)（含 answer、claims+引用、status、cost）。

错误码约定（节选）：`BUDGET_EXCEEDED`（预算熔断）、`TIMEOUT_PARTIAL`（超时返回部分结果）、`RATE_LIMITED`、`INVALID_INPUT`。

### 1.3 流式响应（FR-API-02，阶段三）

`POST /v1/query/stream`（SSE）。事件类型：
- `triage` → 路由结果（fast_path/agentic）
- `sub_question` → 当前子问题与跳数
- `hop_done` → 该跳结论摘要
- `answer` → 最终推理链 JSON
- `error` → 错误终止

SSE 事件由 Agent 循环的 LangGraph 事件流（`astream_events`）映射生成（ADR-005），API 层不另行维护进度状态。

### 1.4 知识管理 API（FR-API-03，阶段三）

- `POST /v1/docs` 批量上传（含来源元数据）
- `GET /v1/ingest-tasks/{id}` 抽取任务状态
- `GET /v1/review-queue` / `POST /v1/review-queue/{id}/decision` 审核队列
- `GET /v1/audit/queries/{query_id}` 推理链审计回查（FR-AN-04）

### 1.5 横切要求

- 鉴权：API Key / 内部 SSO，租户维度数据隔离（NFR-06）
- 速率限制：租户级 QPS + 用户级并发查询数上限
- 全部入参 schema 校验，fail fast（NFR-07）
- 请求携带/生成 `query_id`，贯穿全链路 trace（NFR-08）

## 2. 问答 Web 界面（FR-API-05，阶段四）

**定位**：内部试用工具，功能优先于视觉；单页应用即可。

### 页面结构
1. **提问区**：输入框 + 高级选项（最大跳数、强制 Agentic）
2. **进度区**（消费 SSE）：当前子问题、跳数进度、已耗时
3. **答案区**：答案正文，论断内联引用角标，点击展开原文片段
4. **推理链区**（FR-AN-03，复用 P3-AN-02 组件）：
   - 子问题分解树（节点状态：已答/部分/失败）
   - 图路径可视化（命中的节点/边，可点击看来源）
   - 证据引用列表
5. **反馈区**：准确 / 不准确（+可选原因），提交关联 query_id（FR-OP-03）

### 界面明确不做（V1）
- 多轮对话上下文（每次提问独立）
- 图谱编辑能力（治理走独立审核界面 P3-KG-03）
- 移动端适配
