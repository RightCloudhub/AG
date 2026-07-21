# P5-UI-01：试用 Web 前端框架化重构（Vue 3 零构建）— 执行计划

**任务 ID**：P5-UI-01 · **版本**：V0.1（2026-07-21）· **状态**：**[~] 进行中 — 代码半落地，`web/` 暂不可用（先读 §0）**
**关联**：[engineering/rules.md](../engineering/rules.md) §8（零构建；预留"阶段五引入框架需 ADR"路径）· [engineering/tech-stack.md](../engineering/tech-stack.md) §1/§3（前端选型待定项，本计划关闭）· [workstreams/api-and-ui.md](../workstreams/api-and-ui.md) §2 · `tests/unit/test_web_claude_ui.py`
**约束**：全程**不运行**项目 / 测试 / npm，**不下载**任何依赖（Vue 运行时由浏览器按 §2 策略加载，或后续人工 vendor）；一切需运行才能确认的点进 §7 验证清单。

---

## 0. 当前状态（⚠ 半落地红旗）

已落地 5 个文件（逐文件注记见 §6），组件层 / CSS 拆分 / 测试与文档同步**未落地**。当前工作树的真实状态：

- `GET /web` 渲染静态壳但 **Vue 无法挂载**：`app.js` 静态 import `./js/components/index.js`（尚不存在）→ 模块图解析失败，`boot()` 不执行，页面残留未编译的 mustache 占位符（`[v-cloak]` 样式也未落地），composer 不可用。缺件期**连 boot 错误卡也不会出现**（`renderBootError` 位于同一失败模块内）。
- `index.html` 引用的 `chat.css` / `panels.css` 尚不存在（404，不阻塞渲染）；旧 `app.css` **未动**，旧类样式仍生效，仅 §5-R4 新类暂无样式。
- `pytest tests/unit/test_web_claude_ui.py` 预期**红**（代码检查结论，未运行）：旧断言 `id="answerBox"`、`renderAnswerWithCitations`、`renderPlanTree`、`escapeHtml`、`id="progressList"` 等指向已被替换的实现。
- **流程差异挂账**：rules.md §7 要求"先 ADR 再动代码"，本次代码先行；补救为 §5-R8（ADR-006）列为剩余工作首项。已在 [docs/IMPORTANT.md](../../docs/IMPORTANT.md) §5 挂账。

**回滚**（恢复原生 JS 版 UI，随时可用）：

```bash
git restore web/index.html web/static/app.js
git clean -fd web/static/js
```

---

## 1. 目标与动机

1. **交互增强**（用户诉求）：会话历史、逐 turn 反馈、流中中止、失败重试、复制推理链、健康状态——命令式 DOM 同步（旧 `app.js` 15 个 render/事件函数，433 行）在这些状态交叉下维护成本超标。
2. **关闭选型悬案**：tech-stack.md §3 "前端技术栈（试点阶段前定即可）" 至今未定；rules.md §8 本就预留"阶段五引入框架需 ADR + 更新 EXTERNAL_RUNTIMES.md"的通道。
3. **边界不变**：零构建实质保留（无 npm / 打包器 / Node 工具链）；只调 `/v1/*` envelope；SSE 全事件覆盖；V1 不做清单（多轮上下文 / 图谱编辑 / 移动端 / 路径编辑器）原样保留。

---

## 2. 选型决策（ADR-006 草案要点；正式条目见 §5-R8）

**决策**：Vue 3（钉版 **3.5.13**，`vue.esm-browser.prod.js` 全量构建，含浏览器内模板编译），以**运行时 ESM 动态 import** 引入：本地 vendor 优先（`web/static/vendor/`，一次 vendor 即完全离线）→ 钉版 jsdelivr → 钉版 unpkg 兜底。Options API，组件模块零 Vue import（纯对象），框架面收敛在 `web/` 内。

| 备选 | 否决理由 |
|---|---|
| React | 无 JSX/构建链时人机工学差，违背零构建实质 |
| Preact + htm | 体积最小，但模板即标签字符串、生态/中文资料弱于 Vue，团队上手成本高 |
| Alpine.js | 指令式点缀适合开关，不适合引用角标切分、路径 chips 等列表密集渲染 |
| htmx | 服务端返回 HTML 片段的范式；本项目 SSE 契约是 JSON 事件流，需重写服务端，否决 |

**升级流程**：改版本必须同步三处——ADR-006、`app.js` 加载清单（`VUE_VERSION`）、vendor 文件；过 §7 验证清单后合入。

---

## 3. 目标架构（终态）

启动链：`index.html`（in-DOM 根模板）→ `app.js`（loadVueRuntime → `createApp(rootComponent)` → `registerComponents` → mount）。根组件持有 `turns[]`；每个 turn 为独立请求（**不携带上下文**，历史仅前端展示，V1 边界不破）。

Turn 状态形：`{ id, question, forceAgentic, status: streaming|done|error|aborted, progress[], result, error, feedback{state,message}, fbReason }`。

注入安全：动态文本一律 mustache / `textContent`；**禁止 `v-html` 与任何 `innerHTML`**（boot 失败卡也走 DOM API）——替代旧 `escapeHtml` 条款，进 §5-R9 规则改版。

| 文件 | 职责 | 行数（≤300 硬指标） | 状态 |
|---|---|---|---|
| `web/index.html` | 壳 + in-DOM 根模板（kebab-case 组件/事件） | 124 | **[x] 已落地** |
| `web/static/app.js` | Vue 加载器（vendored-first）+ boot + 错误卡 | 64 | **[x] 已落地** |
| `web/static/js/api.js` | envelope JSON + SSE 手工解析（框架无关） | 100 | **[x] 已落地** |
| `web/static/js/chain-view.js` | 纯视图模型构建器（框架无关） | 159 | **[x] 已落地** |
| `web/static/js/root.js` | 根组件：状态机 + 查询/流/反馈编排 | 203 | **[x] 已落地** |
| `web/static/js/components/index.js` | 全局组件注册 | ~15 | [ ] R1 |
| `web/static/js/components/widgets.js` | progress-log / plan-tree / path-list / steps-list | ~150 | [ ] R2 |
| `web/static/js/components/answer-turn.js` | 答案卡（角标/论断/反馈/折叠/复制/重试） | ~170 | [ ] R3 |
| `web/static/{app,chat,panels}.css` | 拆分：tokens+壳 / 线程+composer / 面板 | 各 ≤300 | [ ] R4 |
| `web/static/vendor/README.md` | vendor 说明（钉版 URL + 提交入库建议） | ~25 | [ ] R5 |

---

## 4. 交互增强（相对旧 UI 的 delta）

| 能力 | 旧 UI | 新 UI |
|---|---|---|
| 会话历史 | 单答案卡，每问清空 | 逐 turn 保留（仅展示，请求仍独立） |
| 进度 | 全局进度卡 | 每 turn 进度折叠卡：流中自动展开、完成自动收起、可回看 |
| 中止 | 无 | 流中「停止」按钮（AbortController），turn 标记 aborted 可重试 |
| 反馈 | 全局，仅最近一次 query_id | 每 turn 独立（idle/sending/good/bad 状态机 + 失败提示） |
| 重试 | 无 | 「强制 Agentic 重问」chip（force_agentic=true，绕缓存） |
| 推理链 JSON | 只读折叠 | + 一键复制（clipboard） |
| 引用角标 | 点击滚动 | + 论断高亮（activeClaim） |
| 服务状态 | 无 | 侧栏 `/healthz` 健康点 |
| 路径截断 | 静默截断 40 条 | 显式 "+N 条未显示"（消除静默上限） |
| 自动滚动 | 无条件 | 仅用户已近底部时跟随（不打断回看） |

---

## 5. 任务清单（状态标记按 rules.md §7 约定）

- [x] **R0** 基础五文件落地（§3 表 + §6 注记）——`index.html`、`app.js`、`js/api.js`、`js/chain-view.js`、`js/root.js`
- [ ] **R1** `components/index.js`：`registerComponents(app)` 注册 `answer-turn` / `progress-log` / `plan-tree` / `path-list` / `steps-list`
- [ ] **R2** `components/widgets.js`：ProgressLog（props `turn`；`<details :open="status==='streaming'">`；li 按 kind info/done/error 着色；状态标签映射）；PlanTree（`buildPlanNodes`）；PathList（`buildPathRows` → rows + hiddenCount 提示）；StepsList（`buildStepItems`）
- [ ] **R3** `components/answer-turn.js`：props `turn`；emits `send-feedback` / `retry-agentic`；data `copyState`/`activeClaim`；computed segments/claimItems/chainJson/metaLine；错误变体（重试 chip）+ 结果变体（meta 行、segment 循环 `sup.cite-btn`、论断面板 `claim-active`、反馈行、四个折叠）；summary 内「复制」按钮 `@click.stop.prevent`
- [ ] **R4** CSS 拆分与新类：`app.css` 精简为 tokens/壳（含新 token `--warn`、`--avatar-w`；`[v-cloak]`、`.rail-health`、`.health-dot.{ok,down,checking}`、`.boot-error`）；`chat.css`（线程/气泡/进度/composer/`.stop-btn`）；`panels.css`（反馈/折叠/引用/树/路径 + `.mini-btn`、`.copy-note`、`.claim-active`、`.feedback-note`、`.retry-row`、`.progress-state`、路径溢出行）；每文件 ≤300 行
- [ ] **R5** `vendor/README.md`：`curl -o web/static/vendor/vue.esm-browser.prod.js https://unpkg.com/vue@3.5.13/dist/vue.esm-browser.prod.js`；MIT、约 170KB min；**建议 vendor 后提交入库**（完全离线）；与 EXTERNAL_RUNTIMES.md 互链
- [ ] **R6** `tests/unit/test_web_claude_ui.py` 重写：文件存在性全集；html 断言 `id="app"`/`v-cloak`/`type="module"`/三个 css link/`id="q"`/`id="askForm"`/`answer-turn`；`app.js` 钉版 `vue@3.5.13` 且 vendor 路径先于 CDN；js 端点全集 + SSE 六事件名；**注入安全断言：全前端 `v-html` 与 `.innerHTML` 零命中**；`chain-view.js` 导出 buildAnswerSegments/buildPlanNodes/parsePath/describeStreamEvent；TestClient：`/web` 200 含 `id="app"`、`/web/static/js/api.js` 200、`chat.css` 200；保留 query/feedback/stream 真实 API 流程测试
- [ ] **R7**（并入 R6 执行）`HTTP_OK` 等具名常量，测试文件 ≤300 行
- [ ] **R8** **ADR-006** 正式落 tech-stack.md（内容=本文件 §2；§1 总表"前端"行→已采纳；§3 勾选"前端技术栈"待决策项）
- [ ] **R9** rules.md §8 改版 + 版本行 V1.0→V1.1（2026-07-21）：零构建（无工具链）不变；**框架白名单仅钉版 Vue 3（ADR-006）**；`escapeHtml` 条款改为"mustache/textContent，禁 v-html 与 innerHTML"；新增"JS 模块同样遵守 §1 硬指标（评审强制，门禁脚本只扫 Python）"；同一变更集同步根 `CLAUDE.md` Conventions 提法
- [ ] **R10** api-and-ui.md §2 升版 V1.3：§2.1 技术形态表（框架/加载策略/模块清单）、§2.2–2.3 并入 §4 增强项、§2.6 引用本文件 §7 验证清单；清除"原生 JS 无框架"旧表述
- [ ] **R11** EXTERNAL_RUNTIMES.md：§3 加"Vue 3 运行时（浏览器加载/vendor，**非 npm**）"行 + vendor 小节 + 变更记录行
- [ ] **R12** 状态同步：IMPORTANT.md §5 行改 [x]；phase-5-scale.md P5-UI-01 勾结；phase-4-pilot.md P4-UI 行加指针注；README「试用 Web 界面」小节更新
- [ ] **R13** 合入：单 changeset；提交信息 `feat(web): vue 3 zero-build refactor with interactive trial UI (P5-UI-01)`（无 Co-Authored-By）；提交前过 rules §5 安全清单 + §7 验证清单全绿

---

## 6. 已落地变更逐文件注记（R0）

> 以下"自查"均为**代码检查结论**（未运行）；运行验证统一见 §7。

**`web/index.html`（改写，124 行）** — in-DOM 根模板：侧栏（品牌/健康点/设置 v-model，保留 `forceAgentic`/`maxHops`/`useStream` 原 id 便于测试延续）、空态 suggestions、`turns` 循环（user 气泡 + `<progress-log>` + `<answer-turn>`）、composer（Enter/Shift+Enter 修饰符、busy 时「停止」替换「发送」）。kebab-case 组件与事件名规避 in-DOM 模板大小写限制；`v-cloak` 已标注但样式待 R4。**依赖缺口**：三个 css link 中两个 404；组件未注册前 Vue 不能挂载。

**`web/static/app.js`（改写，64 行）** — 钉版加载清单 `VENDOR_VUE_PATH → jsdelivr → unpkg`（`VUE_VERSION = "3.5.13"`）；`renderBootError` 全 DOM API + `textContent`。**自查**：静态 import `./js/components/index.js` 失败会连带 `renderBootError` 不可达（§0 红旗根因）；若要缺件期也有提示，可在 R1 落地时评估把错误卡改为独立内联 script——当前不改，按 R1 直接补齐。

**`web/static/js/api.js`（新增，100 行）** — envelope 解包（`success=false` 抛 `message||code`）；SSE 手工解析（POST+JSON body 故不用 EventSource）：`\n\n` 分块、`event:`/`data:` 前缀常量、多 `data:` 行拼接、坏 JSON 静默丢帧；`friendlyError` 把 AbortError 归一为「已中止」。**自查注记**：`data:` 行 slice 后 `trim()` 比 SSE 规范（去单个前导空格）更激进，与旧实现一致，对 JSON 载荷无影响。

**`web/static/js/chain-view.js`（新增，159 行）** — 旧 render 函数的纯函数化等价迁移：`buildAnswerSegments`（内联 claim 匹配优先，退化为尾部角标）、`buildPlanNodes`/`buildStepItems`、`parsePath`（正则与旧版一致）、`buildPathRows`（`MAX_PATH_ROWS=40` 截断改为显式 hiddenCount）。`describeStreamEvent` 覆盖 `cache_hit/triage/sub_question/hop_done`，未知事件返回 null（rules §8 静默忽略）；`answer`/`error` 由 root 处理。

**`web/static/js/root.js`（新增，203 行）** — 根组件（Options API）。**自查**：turn 状态机 `streaming→done|error|aborted`，`finishWithError` 仅在 streaming 态生效 → 「停止」后的 AbortError 不会覆盖 aborted 标记；`runStream` 正常结束但未收到 `answer` 判「提前结束」错误；单飞并发（`busy` + 单 `_controller`）；跳数 clamp `MIN_HOPS=1..MAX_HOPS=10`；仅近底部（`NEAR_BOTTOM_PX=120`）自动跟随滚动；魔法数字均已具名常量。硬指标：全部函数 ≤50 行、嵌套 ≤3、位置参数 ≤3（`sendFeedback` 收对象）。

---

## 7. 验证清单（R6–R13 落地后必须人工执行）

- [ ] `pytest tests/unit/test_web_claude_ui.py -q`；随后全量 `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`（未动 `src/` Python，预期不受影响——仍需跑实证）
- [ ] `python scripts/check_code_metrics.py`（只扫 Python，应为 no-op）；`ruff check/format` 覆盖改动的测试文件
- [ ] JS/HTML 行数复核：`wc -l web/index.html web/static/*.css web/static/*.js web/static/js/**/*.js` 全部 ≤300
- [ ] 注入安全复查（rules §5/§8）：`grep -rn "innerHTML\|v-html" web/` 零命中；外链仅两条钉版 CDN
- [ ] 浏览器（`agr-api` 后打开 `/web`）：有网 CDN 挂载成功；vendor 后**断网**挂载成功；缺 vendor 且断网时出现 boot 错误卡（含 vendor 提示）
- [ ] 流式：逐 hop 实时出现（triage/sub_question/hop_done/answer）；同题二问触发 `cache_hit`；关 `useStream` 走同步路径
- [ ] 「停止」中止后：turn 标「已停止」、可「强制 Agentic 重问」、后续提问正常
- [ ] 反馈：每 turn 独立提交、`query_id` 正确、`success=false` 显示失败提示
- [ ] 角标点击 → 论断高亮 + 滚动；「复制」写入剪贴板（localhost secure context）；路径 >40 条显示 "+N 条未显示"（临时把 `MAX_PATH_ROWS` 调小验证）
- [ ] 健康点两态（正常 / 停服重开页面）；Enter 发送、Shift+Enter 换行、textarea 自适应 ≤128px

---

## 8. 风险与开放点

| 风险 | 处置 |
|---|---|
| CDN 不可达（本环境网关易 403 / 断网演示） | R5 vendor 一次即永久离线；boot 错误卡指路 |
| in-DOM 模板限制（大小写/自闭合） | 已全程 kebab-case + 显式闭合；R1–R3 沿用 |
| `<details :open>` 绑定语义 | 仅绑定值翻转时写 DOM：流中自动展开→完成自动收起，用户手动开合不被覆盖；评审确认该 UX |
| 半落地窗口被误部署 | §0 红旗 + IMPORTANT.md 挂账；合入前 `/web` 不可用为已知状态；回滚命令见 §0 |
| Vue 升级漂移 | §2 升级流程三处同步 + §7 回归 |
