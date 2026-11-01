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

## 验证记录

分步提交和实测结果记录在 [多租户实施记录](../../plan/workstreams/multitenant-webapp.md)。
SQLite 单测不代表 PostgreSQL 或真实图／向量后端已完成部署验证。
