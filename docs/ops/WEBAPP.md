# 独立 Web 应用

身份模块位于 `src/agentic_graphrag/webapp/`；核心 `agr-api`、CLI 与离线评测仍可单独运行。
Web 应用默认使用相同的离线业务后端；在线服务仍由既有 Profile 显式启用。

## 开发启动

```bash
uv pip install -e ".[dev,webapp]"
agr-webapp
```

默认地址 `http://localhost:8000`，数据库 `data/identity/webapp.db`。
首次启动通过 Alembic 升级身份库，开发签名密钥以 0600 权限保存在
`data/identity/signing.key`。整个 identity 目录已忽略，不提交数据库或密钥。
只测试身份层可调用 `webapp.app.create_app(include_business=False)`，无需推理引擎。

`AGR_WEB_ORIGIN` 必须与浏览器地址的协议、主机、端口一致。所有 Cookie 写请求先
`GET /v1/auth/csrf`，再以 `X-CSRF-Token` 和同源 `Origin` 提交。
认证接口同时提供 `/v1` 与无前缀路径；业务接口保留 `/v1` 原契约。

Web 入口 `/` 提供登录和注册，`/app/chat` 是受保护的笔记本；`/web` 重定向到它。
深链接由独立 Web 工厂回退到 `web/application.html`，API 和静态资源的 404 不受影响。
核心 `agr-api` 仍使用原 `web/index.html`，两者共享本地 Vue 和业务组件。
浏览器不需要 npm 构建；Vue Router 来源与许可证见 `web/static/vendor/README.md`。

## 数据库与部署配置

WebSettings 从 `.env` 或进程环境读取；字段见 `.env.example`。

- `AGR_DATABASE_URL`：默认 SQLite；PostgreSQL 使用 `postgresql+psycopg://...`。
- `AGR_WEB_ORIGIN`：唯一允许的浏览器来源；反向代理保留此公开来源。
- `AGR_WEB_SECURE_COOKIES=true`：HTTPS 部署启用，使用 `__Host-` Cookie 名称。
- `AGR_WEB_SECRET`：HTTPS 部署必填，至少 32 个随机字符，各 worker 相同。
- `AGR_WEB_AUTO_MIGRATE=false`：部署时先单独运行迁移，再启动 worker。
- `AGR_WEB_MAIL_BACKEND=log|smtp`：开发邮件只送到 `agr.webapp.mail` 日志；SMTP 使用 STARTTLS。

```bash
alembic -c configs/webapp-alembic.ini upgrade head
uvicorn agentic_graphrag.webapp.app:create_app --factory --host 0.0.0.0 --port 8000
agr-webapp-seed --email admin@example.com
```

种子命令提示输入密码，也接受 `AGR_DEMO_PASSWORD`；创建 demo 租户和管理员，可重复运行，
不修改已有密码或成员角色。它不写入业务语料，也不启用在线服务。

## 会话与恢复

Access JWT 默认 15 分钟，refresh 凭证绝对有效期 7 天。刷新轮换并消费旧凭证；
重放已消费凭证会撤销该会话。退出、密码重置和修改密码立即撤销相应会话。
数据库只存 refresh / reset 凭证摘要；密码采用 Argon2id。登录限流同时按 IP 和
账号摘要计数，状态保存在身份库，各 worker 共享，默认 5 分钟内 10 次。

开发日志邮件包含一次性找回链接；生产使用 SMTP 并限制日志访问。
找回接口始终返回相同受理结果；邮箱不可通过该响应枚举。

## 租户、成员与服务密钥

注册自动创建个人租户；`POST /v1/tenants` 创建团队，显式调用
`POST /v1/tenants/{id}/switch` 切换当前 Cookie 会话。切换轮换凭证并使旧 access 失效。
身份与租户接口同时支持无 `/v1` 前缀的别名。创建团队、切换、账户修改只接受用户会话。

成员角色为 admin / operator / reader；仅 admin 可以管理成员、邀请与密钥。
成员写入先锁租户，在同一事务中校验最后一名管理员；移除成员即时撤销其在该租户的会话。
邀请默认 7 天，可通过 `AGR_WEB_INVITE_SECONDS` 调整；重发轮换链接，原链接立即失效。
接受者须用邀请邮箱登录，链接仅能使用一次，已有成员不会因接受旧邀请自动升级权限。

`POST /v1/tenants/{id}/api-keys` 仅在创建响应的 `data.key` 返回明文，后续列表只有前缀和状态。
每个 key 固定到一个租户，可设到期时间并即时撤销。`AGR_API_KEYS` 继续作为运维服务身份使用；
admin 服务 key 可以管理身份库中对应的租户，但不能执行用户账户或切换操作。
跨租户 URL 与不存在资源统一返回 404，当前租户内角色不足返回 403。

## 业务数据兼容与升级

- 空租户历史数据仅属于 `default`。新建租户初始为空，通过上传导入自己的知识；
  demo 身份种子不会把全局演示语料复制给新租户。
- 文档新写入位于文档目录的 `v2/`，文件名为租户与原始 ID 的摘要，采用原子替换；
  原有 JSON 文件可继续按其内部 ID 与租户读取。相同 ID 在多个租户存在时，无租户的
  `DocStore.get` 返回空；业务 worker 必须传递任务租户。不要按文件名推断文档 ID。
- BM25 快照保留租户字段；索引合并与词频统计都按租户。内存向量与 Qdrant point ID
  采用租户 + chunk ID，Neo4j 节点、边及端点 MERGE 同时匹配 ID 和租户。
  **已有在线 Qdrant collection / Neo4j 图升级前需备份，并从带租户的源文档重建索引**，
  避免旧 point ID 或缺少 tenant 属性的节点重复；若部署另设了仅 ID 唯一约束，先改为
  tenant_id + ID 组合约束。本变更不自动清空或迁移在线数据。
- 查询、SSE、实体词表、审核、任务、审计、trace、预算和 Web 指标均按当前租户过滤。
  `/metrics-prom` 在 Web 应用中仅限 admin，输出当前租户；`/healthz` 不含业务计数。
- CLI 评测使用自己的隔离 StoreBundle 和显式无租户路径，仍可读取演示语料。
  检索缓存将无租户与 default 分开；缓存 key 使用结构化编码，避免分隔符碰撞。

## 验证记录

分步提交和实测结果记录在 [多租户实施记录](../../plan/workstreams/multitenant-webapp.md)。
SQLite 单测不代表 PostgreSQL 或真实图／向量后端已完成部署验证。
