# 工作流：Agent 编排（核心）

**覆盖需求**：FR-AG-01 ~ 08、FR-AN-01/02 · **相关阶段任务**：P1-AG-*、P2-AG-*、P3-PERF-01/05
**负责人**：Agent 编排与 Prompt 工程（1-2 人）

## 1. 循环状态机

```
问题 → [分诊] ─简单─→ Fast Path(单轮向量RAG+生成) → 答案
          │复杂
          ▼
      Planner(分解) → Executor(选工具+检索) → Critic(证据充分?)
          ▲                                       │
          └────── 不充分：下一跳/改写子问题 ←──────┤ (未触顶)
                                                  │充分 或 触顶
                                                  ▼
                                    答案生成(+推理链) / 诚实兜底
```

每一步读写 Memory；护栏在循环入口检查（跳数/调用次数/token 预算）。

**运行时（ADR-005，已采纳）**：循环基于 LangGraph `StateGraph` 实现 —— 分诊/Planner/Executor/Critic/生成为节点，Critic 动作枚举（§2.4）映射为条件边路由，`recursion_limit` 作为自研护栏之外的最终兜底。仅使用 LangGraph 的图/状态/checkpoint/事件流能力，**不引入 LangChain 的检索与链抽象**——检索走本项目工具接口，LLM 调用走自研网关（ADR-003）。评估过程与否决 AgentScope/组合方案的理由见 [tech-stack.md ADR-005](../engineering/tech-stack.md)。

## 2. 组件职责与接口

### 2.1 分诊器（FR-AG-01，阶段三）
- 两级：规则前置（单实体事实问句 → Fast Path）+ 轻量 LLM 判定（复杂度类别 + 预估跳数）。
- 判定错误的兜底：Fast Path 生成时若 Critic 快检发现证据不足，可升级为 Agentic 流程（一次为限）。
- 验证口径：分诊开启后整体 Accuracy 损失 <2pp（P3-EV-02）。

### 2.2 Planner（FR-AG-02）
- 输入：原问题 + Memory 摘要；输出：子问题 DAG（节点=子问题，边=依赖）。
- POC 先支持链式；MVP 支持树状/图状，且允许"占位子问题"——依赖前序结果才能具体化（如"X 的母公司是谁"→ 得到 Y 后生成"Y 的 CEO 是谁"）。
- 结构化输出（JSON Schema 校验），解析失败自动重试（上限 2 次）。

### 2.3 Executor（FR-AG-03）
- 对就绪子问题（依赖已满足）：选择工具（向量/图/全文，可多选）→ 并行执行 → 证据写入 Memory。
- 工具选择策略：关系型子问题优先图检索；语义型优先向量；专名精确匹配加全文。由 LLM 按工具描述选择，附选择理由（入推理链）。
- 外部工具（FR-AG-08，P2 需求）：注册制接口预留（名称/描述/参数 Schema/权限声明），阶段五落地。

### 2.4 Critic（FR-AG-04）
- 两级判定：子问题级（当前证据可回答该子问题？）与全局级（全部证据可回答原问题？）。
- 输出动作枚举：`sufficient` / `next_hop`（给出新子问题）/ `rewrite`（改写当前子问题）/ `give_up`（判定图谱无此知识）。
- 借鉴 ReAct/Reflexion/Self-RAG：判定须引用具体证据编号，禁止裸判断。

### 2.5 Memory（FR-AG-05）
- 内容：子问题 DAG 状态、证据集合（去重）、已探索图路径集合、已排除假设、各步耗时/成本。
- 防死循环：新子问题与已探索项做规范化相似比对，重复则拒绝并强制 Critic 换方向；同一图路径不二次检索。
- 作用域：单次查询内存态 + 结束后随推理链落库（FR-AN-04）。
- 实现（ADR-005）：状态承载于 LangGraph typed state，持久化经 checkpointer（同时服务审计回放）；去重比对、排除假设等判断逻辑自研——框架只存状态，不做语义判断。

### 2.6 护栏（FR-AG-06/07）
| 护栏 | 默认值 | 触顶行为 |
|---|---|---|
| 最大跳数 | 5 | 停止循环，基于已有证据生成"部分答案 + 未解决子问题清单"或诚实兜底 |
| 最大 LLM 调用 | 20 | 同上 |
| 单查询 token 预算 | 可配置 | 同上，并记录熔断事件（FR-OP-02 联动） |
| 单查询超时 | 与 API timeout 联动 | 返回已完成部分 + 超时标记 |
| LangGraph recursion_limit | 跳数上限 ×3 | 最终兜底：正常情况下不应触达（自研护栏先行拦截），触达即视为护栏缺陷，兜底输出并记录告警 |

**诚实兜底（AC-7 关键）**：证据不足时输出固定结构："无法基于现有知识回答 + 已探索路径摘要 + 缺失的关键信息"。生成层校验：答案中每个事实性论断必须绑定证据引用，无引用论断拦截重生成（上限 1 次，仍失败则降级为兜底输出）。

## 3. 推理链契约（FR-AN-02，P2-AG-06 定稿）

```json
{
  "query_id": "…", "question": "…", "route": "agentic|fast_path",
  "steps": [{
    "hop": 1, "sub_question": "…", "depends_on": [],
    "tool_calls": [{"tool": "graph_path", "reason": "…", "hits": ["node/edge/chunk 引用"]}],
    "evidence_ids": ["…"], "conclusion": "…", "critic_action": "next_hop"
  }],
  "answer": "…", "claims": [{"text": "…", "evidence_ids": ["…"]}],
  "status": "answered|partial|no_answer", "cost": {"llm_calls": 0, "tokens": 0, "latency_ms": 0}
}
```

此 JSON 同时服务：可视化（FR-AN-03）、审计落库（FR-AN-04）、评测归因（EV 工作流）。

## 4. 模型分级（P3-PERF-05）

| 角色 | 模型档位 | 理由 |
|---|---|---|
| Planner / Critic | 强模型 | 分解与判定质量决定上限 |
| Executor 工具选择、分诊 | 轻量模型 | 高频调用，任务模式化 |
| 答案生成 | 强模型 | 面向用户的最终输出 |

Prompt 与模型配置全部外置于配置文件，变更须过评测集回归（P5-GOV-03 机制自阶段三起执行）。
