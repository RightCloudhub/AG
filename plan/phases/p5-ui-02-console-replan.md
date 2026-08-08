# P5-UI-02：前端重规划——试用问答 → 角色感知控制台

**任务 ID**：P5-UI-02 · **版本**：V1.0（2026-08-08）· **状态**：**[ ] 规划定稿，待评审后实施**
**关联**：[workstreams/api-and-ui.md](../workstreams/api-and-ui.md) §2 V1.5 · [engineering/rules.md](../engineering/rules.md) §8 · [engineering/tech-stack.md](../engineering/tech-stack.md) ADR-006（**不变，无需新 ADR**）· 前作 [p5-ui-01-vue-refactor.md](./p5-ui-01-vue-refactor.md) · 视觉基准 [docs/UI_DESIGN.md](../../docs/UI_DESIGN.md) · 账本 [docs/IMPORTANT.md](../../docs/IMPORTANT.md) §0/§5

---

## 0. 结论（TL;DR）

前端从「单一试用问答页」重定位为「**角色感知工作台**」：问答（现状行为冻结保留）+ 知识运维 + 审核 + 可观测 + 图谱浏览五视图，按 RBAC 角色显隐。技术形态**不变**——仍是 ADR-006 的 Vue 3.5.13 零构建（无 npm / 无打包器 / 无新运行时依赖，视图切换自研 hash 实现），因此**不需要新 ADR**。实施顺序：先 M0 两个后端前置端点与 M2 结构改造，再逐里程碑加视图；每个里程碑独立可合入。

## 1. 重规划动机（为什么现在重排）

1. **后端能力面已远超 UI**：ENT-01…08（2026-07-25）交付后，`/v1` 下 10+ 端点（上传 / 抽取任务 / 审核队列 / 审计回查 / metrics / budget / audit-events / traces / graph entities）**没有任何界面**，运维全靠 runbook 里的 curl + 手工拼 admin key。
2. **人环质量回路没有可操作界面**：负反馈 → 审核队列 → 决策（P3-KG-03 / P4-OPS-03，风险 R9 的缓解主链）目前只能 API 操作，实际使用被工具摩擦压制。
3. **RBAC 已上线而 UI 角色盲**：侧栏已有 API Key 输入（2026-07-23 审计修复），但 UI 不知道 key 对应角色，无法按能力显隐；若靠「试探 403」发现权限，会污染 ENT-03 安全审计事件流——因此需要 `GET /v1/me`（M0）。
4. **结构已到天花板**：`root.js` 237/300 行，单根组件架构再挂任何视图都会破 rules §1/§8 硬指标；必须先视图化拆分，再谈功能。
5. **高价值复用就在手边**：`GET /v1/audit/queries/{id}` 返回的推理链与 `chain-view.js` 视图模型同构——审计回查视图近乎零成本获得完整链路渲染。

## 2. 现状盘点（P5-UI-01 交付物 → 缺口矩阵）

已交付（权威见 [api-and-ui.md](../workstreams/api-and-ui.md) §2.1–§2.4）：对话 turns / SSE 流式 / 中止重试 / 逐 turn 反馈 / 推理链可视化 / 健康点 / API Key 输入（localStorage `agr_api_key` → `Authorization: Bearer`）。

| 后端能力（均已交付） | 端点 | 服务端角色 | UI 现状 |
|---|---|---|---|
| 问答 + 流式 + 反馈 | `POST /query` · `/query/stream` · `/feedback` | 全部 | ✅ P5-UI-01 |
| 文档上传治理 | `POST /docs`（≤5MB / ≤20 文件 / md·txt） | operator+ | ❌ 无 |
| 抽取任务状态 | `GET /ingest-tasks/{id}`（**无列表端点**） | 全部 | ❌ 无 |
| 审核队列 + 决策 | `GET /review-queue` · `POST …/{id}/decision` | 决策 operator+ | ❌ 无 |
| 审计回查 | `GET /audit/queries/{id}`（租户内） | 全部 | ❌ 无 |
| 指标 / 预算 / 安全事件 / trace | `GET /metrics` · `/budget/snapshot` · `/audit-events` · `/traces/{id}` | admin | ❌ 无 |
| 图实体浏览 | `GET /graph/entities`（P5-CAP-01 脚手架） | reader+ | ❌ 无（原「详情页 UI 待立项」） |
| 身份回显 | **不存在** | — | M0 前置 U-01 |

## 3. 定位与不变量

**新定位**：内部**工作台**（试用问答 + 知识运维 + 可观测），仍是功能优先于视觉的内部工具，不是对外产品。

重规划不动摇的边界：

- **ADR-006 原样有效**：Vue 3.5.13 钉版、vendored-first、零构建、无新运行时依赖（含 vue-router——不引入）。凡需新增运行时依赖（如图表库），**先新 ADR** 再动代码（rules §8 白名单机制）。
- **rules §8 全部条款照旧**：envelope-only、SSE 全事件覆盖、禁 `v-html`/`innerHTML`、JS/CSS/HTML 行数与复杂度硬指标、结构变更同步测试。
- **角色优雅降级**：无权限视图渲染友好「无权限」态（`FORBIDDEN` envelope → 视图级 state-card），不空白、不静默吞错。
- **不试探权限**：UI 一律以 `/v1/me` 结果显隐导航，不得靠打 403 探测（避免污染审计事件流）。
- **离线优先**：全部视图对默认离线服务可用；CI 断言不依赖浏览器（结构断言 + TestClient 冒烟）。
- **V1 明确不做清单不变**：多轮上下文、图谱编辑、移动端、路径编辑器（rules §8；改动须先改 PRD）。

## 4. 目标信息架构（hash 视图）

| 视图 | hash | 导航最低角色 | 内容 |
|---|---|---|---|
| 问答（默认） | `#/chat` | anonymous | 现有对话 UI 原样迁移（行为冻结） |
| 知识运维 | `#/knowledge` | operator | 上传（前端预检 5MB/20/md·txt；PDF 显式提示「暂不支持」）+ 抽取任务列表与状态轮询 |
| 审核 | `#/review` | operator | 队列列表（来源类型 / 置信度 / 上下文）+ 通过/拒绝决策；重复决策 409、跨租户 404 显式提示；**文案不得暗示已写图**（BL-01/03 挂账） |
| 可观测 | `#/ops` | admin | 指标数字卡 + 预算快照 + 安全事件浏览（since/until/tenant/action 过滤，默认近 24h）+ 按 query_id 审计回查（复用 chain-view 渲染完整推理链） |
| 图谱浏览 | `#/graph` | reader | 实体分页表（limit/offset）；详情/邻域需 API 扩展，本期不做（§9） |

侧栏常驻：品牌 + 健康点 + **身份区**（API Key 输入沿用；显示 `/v1/me` 的 tenant/user/role；清除 key 即登出）+ 角色过滤后的导航。

> 角色语义与服务端一致：鉴权关闭部署下匿名 principal 角色为 reader（`auth.py` 默认），带 key 时无论鉴权开关均按 key 角色生效——导航显隐逻辑对 auth on/off 两种部署**同一套代码**，无需特判。

## 5. 目标模块结构（行数预算，全部 ≤300 硬指标）

| 文件 | 职责 | 预算 | 动作 |
|---|---|---|---|
| `index.html` | 壳 + 侧栏 + 视图容器（视图内容全部走组件 string template，压 in-DOM 模板体积） | ≤220 | 改（现 133） |
| `static/app.js` | Vue 加载 + boot | ~65 | 不动 |
| `static/tokens.css` | 设计令牌 + 纸面质感配方（[UI_DESIGN](../../docs/UI_DESIGN.md) §3–§5；自 app.css 抽出） | ≤140 | 新（U-14） |
| `static/js/api.js` | fetch/envelope/SSE/key 核心 + `/v1/me` + 401/403 归一 | ≤170 | 改（现 128） |
| `static/js/api-console.js` | 知识 / 审核 / 可观测域 API 客户端 | ≤160 | 新 |
| `static/js/router.js` | hash ↔ 当前视图同步（自研 ~40 行，零依赖） | ≤60 | 新 |
| `static/js/views/registry.js` | 视图表（id / 标题 / 最低角色 / 组件名），§4 表的唯一代码真源 | ≤40 | 新 |
| `static/js/root.js` | 壳组件：身份态 + 导航 + 视图分发（问答编排迁出） | ≤200 | 改（现 237） |
| `static/js/views/chat.js` | 现 root.js 的问答编排（行为冻结迁移） | ≤240 | 新 |
| `static/js/views/knowledge.js` | 上传 + 任务列表/轮询 | ≤250 | 新 |
| `static/js/views/review.js` | 队列 + 决策 | ≤220 | 新 |
| `static/js/views/ops.js` | 指标 / 预算 / 事件 / 审计回查 | ≤260 | 新 |
| `static/js/views/graph.js` | 实体分页表 | ≤150 | 新 |
| `static/js/components/console-widgets.js` | data-table / filter-bar / stat-card / state-card（空态 / 无权限 / 错误） | ≤220 | 新 |
| `static/console.css` | 控制台视图样式（现有三 CSS 不动） | ≤300 | 新 |

`chain-view.js`、`components/{widgets,answer-turn,index}.js` 原样复用；三个现有 CSS 随视觉体系**值级刷新**（改令牌值与装饰类，选择器结构不重写；`app.css` 抽出令牌后回落 ≤240）——视觉规范唯一权威见 [docs/UI_DESIGN.md](../../docs/UI_DESIGN.md)。组件继续保持纯对象（零 Vue import）。

## 6. 里程碑与任务清单

- **M0 API 前置（后端小改，先行合入）**
  - [ ] **U-01** `GET /v1/me`：回显 `{tenant_id, user_id, role}`（含匿名 reader）；单测 + api-and-ui §1.4 同步
  - [ ] **U-02** `GET /v1/ingest-tasks`：列表分页（operator+，复用 `IngestTaskStore`；当前仅有单条查询）；单测同步
- **M1 身份与角色基座**
  - [ ] **U-03** boot 与 key 变更时探测 `/v1/me`，身份态入壳组件；侧栏身份区展示 tenant/user/role
  - [ ] **U-04** `api.js` 401/403 归一：错误携带 envelope `code`，视图层据此渲染 state-card
- **M2 视图骨架（行为冻结重构，先于一切新功能）**
  - [ ] **U-05** `router.js` + `views/registry.js` + `root.js` 壳化；问答编排原样迁 `views/chat.js`（§8 SSE 回归清单护航）
  - [ ] **U-06** `index.html` 导航 + 视图容器；`console.css` 基座；`console-widgets.js` 四组件
- **M2b 视觉体系落地（「制图室」方向，随 M2 合入或紧随其后）**
  - [ ] **U-14** `tokens.css` 抽出 + 令牌值刷新：墨青/朱砂色系、宋体展示层、动效令牌、纸面网格；`index.html` link 与测试清单同步 — 规范 [UI_DESIGN](../../docs/UI_DESIGN.md) §3–§6
  - [ ] **U-15** 组件视觉规范随视图落地：印章状态语言（无权/通过/驳回/异常/已停止）、仪表卡、数据表、hop 墨线时间轴 — 规范 UI_DESIGN §7–§8
- **M3 知识运维视图（operator+）**
  - [ ] **U-07** `views/knowledge.js`：上传（前端预检 + 逐文件结果）+ 任务列表/轮询（间隔 ≥2s；页面隐藏即停）
  - [ ] **U-08** `views/review.js`：队列 + 决策 + 409/404 显式态；文案标注「决策已记录，图谱写回待 BL-01/03 立项」
- **M4 可观测视图（admin）**
  - [ ] **U-09** `views/ops.js`：指标卡 + 预算快照 + 安全事件过滤浏览（强制分页 + 默认时间窗）
  - [ ] **U-10** 审计回查：query_id → `GET /audit/queries/{id}` → chain-view 渲染（与问答视图同构）
- **M5 图谱浏览（reader，低优先）**
  - [ ] **U-11** `views/graph.js` 实体分页表——关闭 P5-CAP-01「详情页 UI 待立项」中的**列表**部分；详情/邻域仍开放（§9）
- **M6 测试与文档收口**
  - [ ] **U-12** 测试拆分：`test_web_claude_ui.py`（问答回归，保 ≤300 行）+ 新 `test_web_console.py`（文件清单 / 注入 / 请求形状 / 角色-视图映射）
  - [ ] **U-13** 文档同步：api-and-ui §2 状态、phase-5 §2b、IMPORTANT §5、README 状态表；`EXTERNAL_RUNTIMES.md` **无需变更**（无新外部运行时）

依赖顺序：M0 与 M1 可并行，M2 必须先于 M3/M4/M5；M3/M4 相互独立可并行。每里程碑独立可合入，合入时同步勾选本清单。

## 7. 明确不做（本重规划范围内）

- 多轮上下文 / 图谱编辑 / 移动端 / 路径编辑器（rules §8 V1 清单，未改 PRD 不动）
- 图表可视化——指标视图仅数字卡 + 表格；引入任何图表库须先走 ADR
- PDF 上传「支持」——后端白名单无 PDF，UI 只做显式「暂不支持」提示（账见 IMPORTANT §6）
- 浏览器自动化 E2E 进 CI——保持结构断言 + TestClient；人工项走 §8 清单
- 审核决策的图谱写回——BL-01/03 独立立项，非 UI 范畴
- 密钥管理界面（key 的签发/轮换仍走 `AGR_API_KEYS` 环境变量，ENT-04 YAML 注册表待办不并入本计划）
- 暗色主题（单主题做精，试用工具不维护双主题——UI_DESIGN §10）
- CDN 字体 / 任何外链静态资源（字体仅系统栈或 vendored 文件走 EXTERNAL_RUNTIMES 流程——UI_DESIGN §3）

## 8. 验证清单（实施时逐项执行；本规划文档本身无可运行项）

CI 断言（进 `test_web_console.py` / 沿用 `test_web_claude_ui.py`）：

- [ ] 文件清单与行数：§5 全表存在且各 ≤300；Vue 钉版 3.5.13 未漂移
- [ ] 注入安全：全 `web/` 范围 `grep "v-html\|innerHTML"` 零命中（含全部新视图）
- [ ] 请求形状与 Pydantic schema 一致：decision body / 上传 multipart / audit-events 过滤参数 / `me` 响应字段
- [ ] `views/registry.js` 的角色-视图映射与 §4 表一致（结构断言）
- [ ] `/web/static/js/views/*`、`console.css` 静态挂载 200
- [ ] 设计约束（UI_DESIGN §9）：`web/static/*.css` 无 `http` 外链；`@font-face` 仅指向 `vendor/fonts/`（或零命中）；`tokens.css` 在清单内且 ≤140 行

人工浏览器矩阵（每里程碑合入前执行相关行）：

- [ ] 角色 × 视图：anonymous / reader / operator / admin 四种 key 下导航可见性与 403 友好态
- [ ] auth on/off 两种部署下身份区与导航行为一致（匿名=reader；带 key 按 key 角色）
- [ ] 上传三类拒绝路径（>5MB / >20 文件 / PDF）与逐文件结果显示；上传成功 → 任务出现在列表并可轮询到终态
- [ ] 审核重复决策 → 409 显式提示；决策成功后队列刷新；跨租户 item 404 态
- [ ] 已知 query_id 审计回查渲染完整链（角标 / 子问题树 / 路径 chips 与问答视图一致）
- [ ] 问答视图零回归：SSE 七事件（`cache_hit/triage/thinking/sub_question/hop_done/answer/error`）+ 中止 / 重试 / 逐 turn 反馈 / 缓存命中；vendor 断网 boot 正常
- [ ] 轮询防风暴：任务视图切走或标签页隐藏时轮询停止（2 核环境无 CPU 尖峰）
- [ ] 视觉验收（UI_DESIGN §9）：对比度抽查表实测全过 AA；`prefers-reduced-motion` 下无任何动画；无宋体环境回退栈（Noto Serif CJK / SimSun）渲染正常；印章为真文本（可选中、可读屏）

## 9. 风险与开放点

| 风险 / 开放点 | 处置 |
|---|---|
| ingest 任务无列表端点 | M0 U-02 前置补齐；若评审否决，知识视图降级为「仅显示本会话上传产生的任务」并在 IMPORTANT 挂账 |
| localStorage 存 key（试用工具口径） | 维持现状（2026-07-23 先例）；XSS 面被禁 `innerHTML` 条款压制；runbook 补「共享机器用后清除」提示 |
| `index.html` in-DOM 模板膨胀 | 视图一律组件 string template；index 只留容器（§5 预算 ≤220） |
| 安全事件量大拖慢 `#/ops` | audit-events 强制分页 + 默认近 24h 时间窗 |
| 图实体详情 / 邻域无 API | 本期不做；需要时先在 api-and-ui 立项 `GET /graph/entities/{id}` 再做 UI |
| chat 迁移引入回归 | M2 行为冻结 + §8 SSE 回归清单；`chain-view` / 既有组件零改动 |
| 新增文件数 vs 测试文件 300 行上限 | U-12 预先拆分测试模块，不挤压既有断言 |
