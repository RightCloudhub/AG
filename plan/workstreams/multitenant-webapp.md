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
2. [~] 租户隔离：租户成员、邀请、API Key，核心边界与跨租户回归。
3. [ ] 前端路由与认证页面：请求层、history、公开页、认证、权限错误页。
4. [ ] 设置页：资料、安全、成员、密钥、租户完整操作。
5. [ ] 应用内界面：顶栏身份、切换与退出、分区历史、请求失效和浏览器验证。

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
