# 技术选型与决策记录（ADR）

**约定**：选型变更必须先在本文件追加 ADR 条目（编号递增，含背景/决策/理由/影响），评审通过后再动代码。状态：`提议` / `已采纳` / `已废弃`。

## 1. 选型总表（初始建议，待 POC 前定稿）

| 层 | 组件 | 建议 | 备选 | 状态 |
|---|---|---|---|---|
| 图数据库 | GraphStore | Neo4j（生态成熟，Cypher 表达力强，团队上手快） | NebulaGraph（数据超亿级边时切换） | 已采纳（ADR-001） |
| 向量库 | VectorStore | Qdrant（POC 默认）；与图库解耦 | Milvus/pgvector | 已采纳（ADR-002） |
| 全文检索 | FulltextStore | POC：rank_bm25 进程内；规模化可换 ES/OpenSearch | Elasticsearch | 已采纳（POC） |
| LLM | LLMProvider | 强/轻双档位，OpenAI 兼容网关，供应商可替换（NFR-10） | — | 已采纳（ADR-003） |
| 服务框架 | API | Python（FastAPI）——LLM/检索生态最全；POC 先 CLI | Go/TS | 已采纳（ADR-004） |
| 前端 | 试用界面 | Vue 3 零构建（钉版 3.5.13，运行时 ESM） | Preact+htm / Alpine | 已采纳（ADR-006） |
| 编排 | Agent 框架 | **LangGraph（StateGraph）+ 自研控制逻辑**（护栏/Memory去重/推理链） | AgentScope（仅深度绑定 Qwen/DashScope 生态时重议） | 已采纳（ADR-005） |
| 可观测 | Trace/监控 | OpenTelemetry + 现有监控栈 | — | 待定 |

> 参考开源实现（立项书附录）：Microsoft GraphRAG、LightRAG、HippoRAG —— POC 前安排 2-3 天源码调研，可借鉴索引构建与社区摘要思路；Agent 循环按本项目设计基于 LangGraph 实现（ADR-005）。

## 2. ADR 条目

### ADR-001：图数据库选 Neo4j（已采纳，2026-07 POC 启动）
- **背景**：PRD 开放问题 #1；试点领域数据量预估在千万边以下。
- **决策**：POC/MVP 用 Neo4j Community；GraphStore 接口抽象隔离，数据规模超阈值时迁移 NebulaGraph。
- **理由**：生态成熟、Cypher 学习曲线平缓、单机即可支撑试点规模；团队图谱经验不足（风险登记 R4），选最低摩擦方案。
- **影响**：图检索工具基于 Cypher 参数化模板实现；迁移成本被接口抽象控制在检索层内部。

### ADR-002：向量库独立于图库 / Qdrant（已采纳，2026-07 POC 启动）
- **背景**：部分图库带向量能力，但成熟度参差。
- **决策**：向量检索用独立 **Qdrant**；不用图库内置向量功能。POC 另提供 `InMemoryVectorStore` 便于离线测试。
- **理由**：三路检索独立演进（NFR-10）；Qdrant 单容器运维成本低。

### ADR-003：LLM 双档位 + 统一网关（已采纳，2026-07 POC 启动）
- **背景**：Agent 多轮调用成本高（风险 R2）；不同角色对模型能力要求不同。
- **决策**：Planner/Critic/生成用强档位，Executor 工具选择/分诊用轻档位；全部经统一 LLMProvider 接口（OpenAI 兼容）+ 缓存 + BudgetTracker。
- **影响**：成本核算与预算熔断（FR-OP-02）在网关层统一实现。

### ADR-004：服务端语言 Python/FastAPI（已采纳，2026-07 POC 启动）
- **背景**：需快速迭代 Prompt 与检索策略。
- **决策**：Python ≥3.12 + FastAPI + Pydantic（schema 校验天然满足 NFR-07）。POC 阶段以 CLI/脚本驱动，API 路由预留。
- **影响**：异步 IO 支撑检索并行化（FR-RT-05）。

### ADR-005：Agent 循环采用 LangGraph 运行时 + 自研控制逻辑（已采纳，2026-07 修订）
- **背景**：原提议为全自研轻量循环。经 LangGraph vs AgentScope vs 组合方案评估后修订本决策。
- **决策**：Agent 循环基于 LangGraph（≥1.0，API 已稳定）的 `StateGraph` 实现：分诊/Planner/Executor/Critic/生成为节点，Critic 动作枚举映射为条件边路由。护栏、Memory 去重、推理链构建、引用拦截等控制逻辑仍自研，作为节点/状态逻辑挂载。**不引入 LangChain 的检索与链抽象**——检索走本项目工具接口，LLM 调用仍走自研网关（ADR-003）。
- **理由**：
  - 本项目 Agent 层是显式状态机（分诊→Planner→Executor→Critic→条件回环+硬性出口），与 StateGraph 的节点+条件边模型一一对应，几乎无适配成本。
  - 免费获得工程地基：checkpoint（状态持久化，服务 FR-AN-04 审计回放）、事件流 `astream_events`（直接映射 SSE 事件，FR-API-02）、中断/恢复、`recursion_limit` 最终兜底——这些正是原自研方案中成本最高的健壮性管道。
  - 降低团队 Agent 经验不足风险（R4）。
- **否决的备选**：
  - **AgentScope**：面向自主多智能体消息传递/会话与模拟场景，Qwen/DashScope 集成强、中文文档好；但本项目的 Planner/Executor/Critic 是"单一确定性管线中的角色"而非自主对话智能体，在消息传递框架上实现确定性护栏与状态控制需与框架对抗。仅当团队深度绑定 Qwen/DashScope 生态时重议。
  - **LangGraph + AgentScope 组合**：编排职责重叠（循环控制/记忆/追踪双份）、双运行时双学习曲线（放大 R4）、故障排查面翻倍，且无任何组件需要 AgentScope 的增量能力。否决。
  - **全自研（原方案）**：重试/中断/流式/checkpoint 管道的自研成本高于其带来的可控性收益；可控性通过"控制逻辑自研 + 框架仅作运行时"同样达成。
- **影响**：`agent/loop.py` 为 StateGraph 组装与编译；langgraph 版本锁定，升级须过评测回归（新增风险 R11）；框架依赖限定在 `agent/` 模块内，其余模块无感知，保留替换回自研的退路。

### ADR-006：试用 Web 前端采用 Vue 3 零构建（已采纳，2026-07-21）
- **背景**：rules.md §8 原约束 POC~试点「无前端框架」；阶段四交付的原生 JS 试用 UI（`web/static/app.js` 命令式 DOM，~15 个 render/事件函数）在会话历史、逐 turn 反馈、流中中止、失败重试、复制推理链、健康状态等交叉状态下维护成本超标。tech-stack.md §3「前端技术栈」待决；rules §8 预留「阶段五引入框架需 ADR + 更新 EXTERNAL_RUNTIMES.md」通道。详见执行计划 [phases/p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md)。
- **决策**：试用界面采用 **Vue 3**，钉版 **3.5.13**，产物为 `vue.esm-browser.prod.js`（全量构建，含浏览器内模板编译）。以**运行时 ESM 动态 import** 引入，**不**引入 npm / 打包器 / Node 工具链：
  1. 本地 vendor 优先：`web/static/vendor/vue.esm-browser.prod.js`（一次 vendor 即完全离线，见 `web/static/vendor/README.md`）
  2. 钉版 jsDelivr 镜像
  3. 钉版 unpkg 兜底
  - 组件用 **Options API**；组件模块为纯对象（不 import Vue），框架面收敛在 `web/`。
  - 启动链：`index.html`（in-DOM 根模板）→ `app.js`（`loadVueRuntime` → `createApp` → `registerComponents` → mount）。
- **理由**：
  - 零构建实质保留（无工具链），符合 rules §8 与 EXTERNAL_RUNTIMES「非 npm」边界。
  - 声明式模板适合引用角标切分、路径 chips、进度/反馈状态机等列表密集渲染；Options API + in-DOM 模板上手成本低。
  - 钉版 + vendor-first 可断网演示；CDN 仅作未 vendor 时的开发兜底。
- **否决的备选**：
  - **React**：无 JSX/构建链时人机工学差，违背零构建实质。
  - **Preact + htm**：体积最小，但模板即标签字符串、生态/中文资料弱于 Vue，团队上手成本高。
  - **Alpine.js**：指令式点缀适合开关，不适合引用角标切分、路径 chips 等列表密集渲染。
  - **htmx**：服务端返回 HTML 片段的范式；本项目 SSE 契约是 JSON 事件流，需重写服务端，否决。
- **影响**：
  - `web/` 模块化：`app.js` 加载器 + `js/root.js` / `js/api.js` / `js/chain-view.js` / `js/components/*`；CSS 拆为 `app.css` / `chat.css` / `panels.css`。
  - 注入安全：动态文本一律 mustache / `textContent`；**禁止 `v-html` 与任何 `innerHTML`**（替代原 `escapeHtml` 条款，见 rules.md §8 V1.1）。
  - 升级流程：改版本必须同步三处——本 ADR、`app.js` 的 `VUE_VERSION` 与加载清单、vendor 文件；过 [p5-ui-01-vue-refactor.md](../phases/p5-ui-01-vue-refactor.md) §7 验证清单后合入。
  - 仍明确不做（V1）：多轮上下文、图谱编辑、移动端适配、路径编辑器。

## 3. 待决策清单（评审会）

- [x] ADR-001~004 在 POC 启动时按默认采纳（仍可在评审会改选）
- [x] 向量库具体产品：Qdrant（POC）
- [x] 前端技术栈：Vue 3 零构建（ADR-006，2026-07-21）
- [ ] 部署形态：K8s / 单机 Docker Compose（试点规模决定）
- [ ] 正式试点领域与语料替换当前 interim 公司关系语料（P1-GOV-01 / R5）— 见 [g1-to-g2-transition.md](../phases/g1-to-g2-transition.md) C1 · `data/pilot/`
