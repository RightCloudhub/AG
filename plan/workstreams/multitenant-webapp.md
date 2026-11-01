# 独立多租户 Web 应用

**确认来源**：2026-10-31 会话 `01a191c5-c6c0-7d40-ae44-9aa83dc60522`；
用户确认方案、要求与核心独立并批准新建分支。2026-11-01 从该会话恢复实施。

## 边界与契约

- `webapp/` 管理 User、Tenant、Membership、Invite、ApiKey、AuthSession、
  PasswordResetToken、刷新凭证和认证限流记录，拥有独立迁移及种子脚本。
- SQLite 默认，PostgreSQL 可配置；新增库只在 `.[webapp]` 安装组中。
- 账号、JWT、CSRF、邮件、成员角色不进入 GraphRAG 检索或推理。
- 既有 `/v1/query`、`/v1/query/stream` 及 SSE 数据契约保持；CLI / 评测独立运行。
- 身份接口采用 `/v1/auth/*`、`/v1/tenants*`、`/v1/me`，同时提供原方案无前缀别名。
- `/me` 保留 tenant_id / user_id / role / authenticated / capabilities，
  增加 email / display_name / tenants；原有五个 capability 键不变。
- Cookie 会话支持注册、登录、刷新、退出、找回/修改密码、会话撤销。
  仅保存密码 Argon2id 哈希及 API Key / reset / refresh / invite 的摘要。
- 租户支持创建、切换、改名；admin 管理成员、邀请和 API Key；保护最后一名 admin。
- 所有业务数据、审计、指标、预算和缓存按租户隔离；跨租户资源与不存在同形 404。
  历史空租户数据只在 default 租户兼容；离线评测保留无租户执行路径。

## 前端路由与状态

- 公开：`/`、`/login`、`/register`、`/forgot`、`/reset`、`/invite/:token`。
- 登录后：`/app/chat/:id?`、`/app/graph`、`/app/knowledge`、`/app/review`、`/app/ops`。
- 设置：`/app/settings/{profile,security,members,api-keys,tenant}`。
- 本地 Vue Router ESM / history，后端深链接回退，提供 403 / 404；旧 `/web` 可兼容。
- 登录注册为居中双栏卡片，左品牌右表单；应用保留推理笔记本三栏与靛夜/晨纸主题。
- credentials include、内存 CSRF、401 单次合并刷新；不持久化密码或认证 token。
- 切换/退出取消请求、SSE、轮询，清理视图与检查器；迟到响应不可回填。
  笔记按 user_id + tenant_id 分区，旧无归属记录不导入，多标签页同步身份变化。

## 分步交付

1. [x] 后端认证：数据库、迁移、Cookie / CSRF、密码流程、限流、独立工厂及种子。
2. [x] 租户隔离：租户成员、邀请、API Key，核心边界与跨租户回归。
3. [x] 前端路由与认证页面：请求层、history、公开页、认证、权限错误页。
4. [x] 设置页：资料、安全、成员、密钥、租户完整操作。
5. [x] 应用内界面：顶栏身份、切换与退出、分区历史、请求失效和浏览器验证。

每一步独立提交，记录 pytest 覆盖率、Ruff、mypy、代码指标结果；前端另检查 JS、
Vue 模板和浏览器截图。隔离改动跑离线评测。真实 PostgreSQL / Neo4j / Qdrant 的
部署验证与离线证据分开记录。

### 第一步验证（2026-11-01）

- `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`：537 passed，88.81%。
- `ruff check src tests scripts`、`ruff format --check src tests scripts`：通过。
- `mypy`：280 个源模块通过；`python scripts/check_code_metrics.py`：通过。
- 覆盖 SQLite Alembic schema 一致性、注册原子性、登录、刷新轮换/重放撤销、退出、
  密码找回/修改、CSRF、共享限流、跨账号会话操作和种子幂等。
- 核心依赖方向检查及禁止加载 Web 可选依赖的离线查询通过。
- 多标签页 CSRF 初始化保持稳定；认证/切换操作仍更新 Cookie；密码与链接凭证不写普通访问日志。

### 第二步验证（2026-11-01）

- 全量 `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q`：
  567 passed，89.37%；Ruff、format、mypy（290 模块）、代码指标通过。
- 先复现再修复：同名文档与索引覆盖、空租户共享、全局运维数据、审核合并查错租户、
  检索与预算 key 分隔符碰撞、无租户缓存与 default 混用、实体词表混用。
- 两个 Cookie 用户同名上传 → worker 入库 → 图谱 → 查询 / SSE → 审核 / 任务端到端隔离；
  API Key 与 Cookie 运维接口同样过滤。验证邀请重发、过期、撤销、一次消费及不隐式提权，
  密钥仅显示一次、立即撤销，成员移除立即撤销会话，并发降权保持至少一个 admin。
- 离线门禁：POC 31/31，triage 31/31，G2 200/200，compositional 54/54，negative 20/20；
  金标审计 flagged=0。G2 与第一步提交配对比较：200/200 → 200/200，McNemar p=1.0，
  P95 54ms → 51ms，LLM 均值均为 0；无效果提升声明。
- 在线索引需按租户重建、文档 v2 布局及 default 历史数据归属见 `docs/ops/WEBAPP.md`；
  PostgreSQL / Neo4j / Qdrant 真实部署验证独立挂账，不将 mock 当部署证据。

### 第三步验证（2026-11-02）

- 全量 pytest：579 passed，89.39%；Ruff、format、mypy（291 模块）、代码指标通过。
- 本地 Vue Router 4.5.1 与 Vue 3.5.13 共用实例；TypeScript 和所有 Vue 模板通过。
- 离线 JS 验证安全跳转、密码策略、历史分区、迟到响应失效、并发单次刷新，
  以及流开始后的网络错误不会重放查询。
- Chromium 验证登录 / 注册双主题与 1440、768、390px 布局，字段错误、受保护页面跳转、
  注册进入工作台；公开 HTML、静态资源及 API 404 边界有回归测试。
- 先复现并修复已登录用户加载多个 ESM 资源耗尽业务 QPS 配额的问题。
- 设计技能静态与浏览器检查均 0 FAIL；三种字体角色沿用用户指定品牌。
- ⚠ 依赖调整：浏览器 Router 唯一包装修改为本地 Vue import，来源与许可证已记录。
  历史分区与取消机制的基础部分提前到第三步，完整切换和多标签页验证仍在第五步。

### 第四步验证（2026-11-02）

- 全量 pytest：579 passed，89.39%；Ruff、format、mypy（291 模块）、代码指标通过。
  严格 JS 类型、全部 Vue 模板和 8 项离线前端行为检查通过。
- Chromium 实际操作：资料保存、双主题、最后管理员保护、邀请接受 / 重发 / 撤销、
  成员角色变更、密钥一次显示 / 即时撤销、撤销另一个登录会话、密码变更、空间创建 / 改名。
  设置页在 1440、768、390px 浏览；手机导航完整换行，表格改为带标签的行。
- 浏览器先复现密码变更后的自动跳转覆盖成功提示，修复后保留显式返回地址，并加离线回归。
- 密钥明文只留在当页内存，离开页面清除；失效时间与未使用状态按实际响应呈现。

### 第五步验证（2026-11-02）

- 全量 pytest：583 passed，89.39%；Ruff、format、mypy（291 模块）、代码指标通过。
  严格 JS 类型、全部 Vue 模板和 21 项离线前端行为检查通过。
- 顶栏呈现当前空间、用户名与账号菜单；支持直接创建并进入空间、切换、退出、主题、
  命令面板和推理模式。笔记可新建、打开、删除、刷新深链接和使用浏览器返回。
- 实际 Chromium 双标签验证切换 / 退出 / 再登录、资料更新通知、账号与租户历史分区，
  退出清除该账号全部分区并保留偏好；同步网络失败时隐藏历史，重试恢复当前所属笔记。
- 浏览器实测 Cookie 上传 → 后台处理 → 审核通过、查询、图谱与运维页面；
  reader / operator / admin 的菜单、路由与 API 权限一致，跨租户任务返回 404。
  邀请经注册或登录返回并接受、错误账号切换、过期 / 已使用链接、找回 / 重置密码均通过。
  邮件使用临时本地邮箱，邀请过期用临时数据库到期时刻验证；不作为 SMTP 部署证据。
- 先复现再修复：空可选笔记 ID 误报 404、迟到澄清与反馈、删除中的流目标回填、
  同步失败重新显示旧历史、撤销会话与其它 Cookie 写入并发；有离线回归。
  在浏览器注入延迟澄清与 SSE，确认切换后不回填，并关闭旧流读取器。
- 浏览器前置身份头绑定请求发起时的 user / tenant；Cookie 已被其它标签更改时返回
  `409 IDENTITY_CHANGED`，重新读取身份而不重放写入。验证无跨标签通知时仍拒绝旧写入，
  以及 access 过期的旧账号不能退出新账号；服务 API Key 不受浏览器前置头影响。
- 1440 / 768 / 390px、靛夜 / 晨纸实测无溢出与浏览器异常。设计检查从 1 FAIL 修至
  0 FAIL；保留 2 项工作区留白提醒，原因及品牌字体例外见 `docs/design/MULTITENANT_WEB.md`。
- 五步实现均为本地提交。部署相关 PostgreSQL、SMTP 和在线索引验证继续按
  `docs/reviews/IMPORTANT.md` 的既有记录执行。
