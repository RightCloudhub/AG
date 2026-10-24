# 全项目类型安全与常量化检查

更新日期：2026-10-24。目标：消除重构时无法被静态检查发现的内部字符串引用、
不明确的接口类型和魔法值，保持离线默认、存储协议和零构建前端。

## 检查原则

- 内部有限集合（节点、工具、状态、模式、角色）使用枚举；行为使用方法引用和
  明确的 `Protocol`，不通过方法名字符串分发。
- 阈值、重试、分页、时间单位和截断长度使用有含义的常量或配置。
- 外部 JSON/HTTP/环境变量的字段名称集中在契约定义；入口校验后再进入业务代码。
- 字面量必须在定义处存在。文案、正则、协议格式和语料属于数据，集中在对应资源
  或具名定义中；不把所有字符串包装成没有业务含义的常量。
- 不使用 `Any`、`cast`、`type: ignore` 或整模块豁免来掩盖可以修复的类型错误。
  第三方未提供类型声明的适配边界单独记录。
- 每个行为修复先复现再修复；每部分完成后立即记录改动、验证命令和结果。

## 分部分计划

| 部分 | 范围 | 检查与修复内容 | 状态 |
| --- | --- | --- | --- |
| P0 基础门禁 | `pyproject.toml`、CI、验证脚本 | strict mypy/checkJs、锁文件、wheel 类型标记、全套门禁 | [x] |
| P1 核心契约与配置 | `core/`、`config*.py`、`bootstrap/` | 枚举、消息、事件泛型、可选能力协议、配置校验与常量 | [x] |
| P2 Agent 调度 | `agent/`、`agent/tools/` | 枚举引用、泛型注册表、具体图/检查点/快照、回调与 LLM 端口 | [x] |
| P3 存储与检索 | `stores/`、`retrieval/` | 可选能力、租户实体重连、Qdrant 当前接口、融合与排序模式 | [x] |
| P4 知识写入 | `knowledge/` | 状态与决定校验、实体合并、任务转换、写入后词表刷新 | [x] |
| P5 应用、API 与治理 | `app/`、`api/`、`governance/`、`observability/`、`generation/` | 具体服务/缓存/计量摘要、响应路由与状态、LLM 文本校验 | [x] |
| P6 前端 | `web/`（第三方 vendor 为声明边界） | API/SSE/状态引用、请求一致性、未知事件、证据展示、函数指标与五页浏览器验收 | [x] |
| P7 评测、脚本与集成验证 | `eval/`、`cli/`、`scripts/`、`tests/`、`configs/` | 类型与引用、离线下限、配对评测、全套检查与验证边界记录 | [x] |

状态遵循仓库约定：`[ ]` 未开始、`[~]` 进行中、`[x]` 完成、`[-]` 取消。
“完成”要求检查、必要修复、验证与本文件记录全部完成。

## 基线与问题台账

| 编号 | 所属部分 | 发现 | 处理与验证 | 状态 |
| --- | --- | --- | --- | --- |
| TS-001 | P0 | 无静态类型检查配置与 CI 门禁，初步检查 49 条、strict 250 条错误 | strict mypy 覆盖全部 247 个源文件；CI/脚本已接入；wheel 包含 py.typed | [x] |
| TS-002 | P2 | 图节点和内置工具仍通过重复字符串引用 | 节点、工具与注册表已统一枚举与泛型签名，相关回归通过 | [x] |
| TS-003 | P1/P2/P5 | `Any` 和反射调用削弱端口与状态的检查能力 | 具体端口、事件、服务、图、缓存与评分类型；移除可修复的类型忽略；JSON/第三方边界明确记录 | [x] |
| TS-004 | P6 | 上传缺少认证；SSE 未校验 HTTP、拆散多行 data、向客户端派发未知事件 | 4 项运行时回归先复现后修复，全部通过；API/SSE 契约 JS 严格检查通过 | [x] |
| TS-005 | P3 | Qdrant 适配器调用当前依赖已移除的 `search` 方法 | 无网络回归先复现后修复；`query_points` 返回检索结果并保留租户过滤；测试通过 | [x] |
| TS-006 | P6 | 前端结构测试引用旧版本资源，CI 排除了整个前端测试文件 | 移除 CI ignore；8 项页面结构/接口测试与 5 项 Node 回归合计 13 项通过 | [x] |
| TS-007 | P4 | 非法审核决定被当作 skip；无效持久化任务状态被接受 | 两项失败回归复现后，改用枚举校验；无效决定保留 pending | [x] |
| TS-008 | P3/P4 | 内存图实体合并仍按旧列表/裸 ID 索引，未重连或删除别名实体 | EntityMutation 与租户参数；内存图重连/删除/另一租户不变、Neo4j 事务参数回归通过 | [x] |
| TS-009 | P0/P7 | eval 声明确定性却隐式加载本地历史索引；首次比较输入不一致 | 原始代码加载同一索引也仅过 10/20 无答案题；隔离回归通过；候选恢复 167/200，配对无翻转 | [x] |
| TS-010 | P2 | SQLite 工厂通过反射分支向 saver 传文件名，接口要求连接对象 | 原始实现复现 `str` 无 `execute`；显式创建连接并在构造失败时关闭，2 项离线回归通过 | [x] |
| TS-011 | P6 | 聊天页未按后端 `metadata.evidence` 契约读取证据目录 | 先复现证据缺失，恢复类型解析与证据展示；目录缺省/非法形状回归通过 | [x] |
| TS-012 | P4/P5 | EntityLister 被误放到 TYPE_CHECKING，写入后查询词表不刷新 | 全量测试先复现，修正运行时导入；上传与 GraphCommitted 两项回归通过 | [x] |
| TS-013 | P6 | 五个 setup 函数超过 50 行上限 | 拆出状态初始化与独立显示函数；JS AST 复核最大 50 行、复杂度 7、嵌套 3、参数 3；13 项前端回归通过 | [x] |
| TS-014 | P4 | LLM 实体合并决定允许任意字符串，拼错 action 被静默忽略 | 失败回归复现；MergeAction/ResolutionMethod 枚举化；非法决定拒绝，状态回归通过 | [x] |

已执行基线：

- `.venv/bin/ruff check src tests scripts`：通过。
- `.venv/bin/ruff format --check src tests scripts`：通过，316 个文件。
- `.venv/bin/python scripts/check_code_metrics.py`：通过。
- 全量单测与覆盖率：429 通过、7 失败，覆盖率 87.18%；失败均来自旧版前端结构断言。

第一批契约重构：新增共享枚举与时间/分页/融合常量；配置模式通过枚举校验；
规划、执行、生成和抽取代码改用消息角色、提示词和工具枚举。已运行契约、工具
分发与配置相关的 14 个测试，全部通过。此处保留阶段历史；最终结果见下文。

## 修复记录

1. 工具、图节点、提示词和消息角色统一使用枚举；注册表、中间件和事件回调
   使用泛型/协议关联输入与返回类型。扩展工具名使用显式 `ExternalToolName`。
2. 修复 Qdrant `query_points` 适配；融合模式与配置模式拒绝非法值。
3. 状态快照改用 `MemorySnapshot`；图流生成器正确声明返回的最终状态；
   属性名反射从审核过滤与目录创建中移除；应用/知识写入改用端口和具体模型。
4. 校验 LLM 响应中的文本内容；非字符串结果先通过失败测试复现，再修复。
5. 超过行数上限的工具签名、图流执行和 LLM 构造分别拆入职责对应模块。
6. 前端上传复用认证请求；SSE 按完整帧解析多行 data，校验 HTTP 和内容类型，
   保留后端错误码，忽略未知事件并释放 reader。4 项离线 Node 回归全部通过。
7. 前端导航、角色、审核决定、任务状态、路由、分页、截断和时间单位改用共享
   契约引用；纠正导入任务徽标的旧状态名。CSS 按布局、控件、对话、答案和响应式
   职责拆分至各文件不超过 300 行；移除字体网络请求，中文 shell 标记为 zh-CN。
8. 自有前端全部模块使用严格 checkJs；响应数据经 `responses.js` 校验并转为具体
   类型。第三方 Vue 的已用接口有单独声明文件。CI 已加入 Python/JS 类型门禁，
   README、AGENTS、CLAUDE、工程规则与验证脚本同步更新。
9. 需求、子问题、导入和审核状态统一枚举；审核持久化加载移除字段名反射。
   关系/实体列表、删除、合并、快照加载与关闭改用显式可选 `Protocol`；两个图后端
   显式实现能力接口，方法改名或签名不一致会产生静态检查错误。

10. 检查点后端与提示词的 Python 调用改用枚举；注册表接受强类型 `ToolName`。
    编译回归证明错误角色/成员/事件回调/工具名/提示词名/检查点名/返回类型被拒绝。
11. critic 旧位置参数通过 object 与模型校验；文本生成拆为 CompletionClient，
    避免要求只做文本判断的客户端实现 embedding；递归恢复直接捕获异常类。
12. 缓存签名、可选 LLM、金标生成 kwargs、Prometheus 参数和 HTTP Response
    类型修正，删除可修复的类型忽略；计量摘要与响应状态/路由使用具体类型。
13. uv.lock 增加 6 个开发工具及传递依赖，既有依赖版本未改变；实际 wheel
    构建验证 py.typed 已包含。Python、JS 类型检查和前端测试已接入 CI。
14. 前端 setup 按状态初始化、身份读取和显示函数拆分；保持 Vue 生命周期调用
    在 setup 内执行。自有 JS 函数用 TypeScript AST 复核，长度 ≤50、复杂度 ≤7、
    嵌套 ≤3、参数 ≤3；复杂度按各函数独立作用域计算。

## 最终验证结果

| 验证 | 命令或证据 | 结果 |
| --- | --- | --- |
| Python 类型 | `.venv/bin/python -m mypy` | 247 个源文件通过 |
| JS 类型 | `node /tmp/ag-type-audit-tools/node_modules/typescript/bin/tsc -p web/jsconfig.json`（5.9.3） | 全部自有 JS 通过 |
| Lint | `.venv/bin/ruff check src tests scripts` | 通过 |
| 格式 | `.venv/bin/ruff format --check src tests scripts` | 335 个文件通过 |
| 代码指标 | `.venv/bin/python scripts/check_code_metrics.py` | 文件/函数/复杂度等全部通过 |
| 单测与覆盖率 | `.venv/bin/pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` | 457 通过，88.04%；1 项第三方弃用提示 |
| 收尾回归 | `test_graph_recursion_fallback.py` 与 `test_static_contracts.py` | 合计 5 项通过 |
| 锁文件 | `uv lock --check` | 通过 |
| 包类型标记 | `uv build --wheel --out-dir /tmp/ag-type-audit-dist`，检查 wheel | 构建通过，包含 py.typed |
| 模型 Schema | 导出 schema 与 `reasoning_chain_json_schema()` 对比 | 一致 |
| 补丁空白 | `git diff --check` | 通过 |
| 浏览器运行 | Chromium 156.0.8078.4 与真实离线 API | 五个视图通过，无页面异常，无外部资源请求；问答证据、任务徽标、审核与运维可用 |

浏览器使用临时工具与运行库、隔离的任务/审核数据和测试身份；应用保持离线，
未连接 LLM/Neo4j/Qdrant。截图：
[聊天与证据](./assets/type-safety-chat.png)、[任务状态与创建时间](./assets/type-safety-knowledge.png)。

## 离线评测与配对比较

基线版本：`4860b619607132c04151723feb3ef8c2a0398aa1`，归档于
`/tmp/ag-audit-baseline`。G2 两侧使用相同数据集和 pilot 种子图，均无在线 LLM，
没有 LLM 缓存；基线无历史检索索引，候选 eval 显式隔离历史检索索引。

| 指标 | 基线 | 候选 |
| --- | --- | --- |
| G2 正确数 | 167/200 | 167/200 |
| G2 准确率 | 83.5% | 83.5% |
| P95（单次采样） | 101 ms | 82 ms |
| LLM 调用均值 | 0 | 0 |
| POC agentic | 仓库下限 21/31 | 21/31 |
| POC triage | 仓库下限 21/31 | 21/31 |

配对：基线独对 0、候选独对 0、同时正确 167、同时错误 33；McNemar p=1，
无翻转案例。结论为 no_significant_difference，不据单次 P95 声称性能提升。
合成离线结果是工程回归证据，不能代替生产/live 或真实试点域验收。

首次候选 157/200 的调查发现工作区旧 bm25.json 与归档基线输入不同；原始代码
加载同一索引，也仅过 10/20 无答案题。隔离索引后重新比较，不将首次输入不一致
的数字作为类型重构退化结论。

本地报告：`reports/type_audit_candidate.jsonl`、`reports/type_audit_poc.jsonl`、
`reports/type_audit_triage.jsonl`、`reports/type_audit_comparison.json`。
reports 目录被仓库忽略，关键结果已在本文件保留。

复现命令（在仓库根目录，激活开发环境）：

```bash
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_all.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl --run-name type_audit_candidate
agr-run-cases --no-llm --memory-graph --cases evals/datasets/poc_cases.jsonl \
  --run-name type_audit_poc
agr-run-cases --no-llm --memory-graph --enable-triage \
  --cases evals/datasets/poc_cases.jsonl --run-name type_audit_triage
python scripts/eval_compare.py \
  /tmp/ag-audit-baseline/reports/type_audit_baseline.jsonl \
  reports/type_audit_candidate.jsonl --cases evals/datasets/g2_all.jsonl \
  --json reports/type_audit_comparison.json --fail-on-worse
```

SHA-256：G2 数据集 e3068b45ddcfc486dccc4569e59cc40b0cc12de81bc5745916439835f582dcb0；
pilot 图 d1f5717fa99e51daf48e6f4633089573bda24eb413ede3683793fdc248a34efe。

## 验证边界与迁移影响

`JsonObject` 和部分 dict[str, Any] 用于可扩展/序列化 JSON，包括日志、元数据、
评测记录、工具参数和持久化片段。强类型端口和模型不等于所有 JSON 内层键都能
随重构得到编译错误，不宣称“零 Any”或“零字符串”。未安装的可选 OTel/SQLite
只豁免缺失第三方声明，不排除项目源模块。Candidate.type 的 Pydantic computed
property 保留 prop-decorator 忽略，字段函数返回类型仍检查；未新增覆盖率 omit。
Vue 模板由浏览器运行时编译，其字符串表达式不属于 checkJs 的静态检查范围。
Node 与 TypeScript 只参与开发/CI 验证，运行页面仍不需要 npm 清单或构建步骤。

Neo4j 重连、Qdrant 接口通过离线 mock/参数验证，未连接真实服务；SQLite 工厂
使用真实 sqlite3 连接与 fake saver，未运行可选 saver 的持久化联调。部署验证
挂账于 [IMPORTANT.md](./IMPORTANT.md#类型审查后的部署验证)。跨 Python/JS wire
一致性还依赖运行回归。内部 Python 调用应引用枚举和具体端口；扩展工具显式构造
ExternalToolName。有限配置值拒绝非法输入，reasoning schema 已同步；eval 有意
不读本地旧索引，其他 profile 保留原行为。修改留在工作区，未创建提交或 PR。

## 最终验收

- [x] 静态类型检查通过并接入 CI。
- [x] lint、格式、代码指标与补丁空白检查通过。
- [x] 全量离线单测通过，覆盖率不低于 80%。
- [x] POC agentic/triage 各不低于 21/31；G2 不低于 167/200。
- [x] 对效果相关变更完成配对回归，准确率与 McNemar 结果已记录。
- [x] 前端零构建与注入安全断言通过。
- [x] 浏览器验收与截图完成，P0–P7 状态和问题台账已同步。
