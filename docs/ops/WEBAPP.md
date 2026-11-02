# 独立 Web 应用

身份模块位于 `src/agentic_graphrag/webapp/`；核心 `agr-api`、CLI 与离线评测仍可单独运行。
Web 应用默认使用相同的离线业务后端；在线服务仍由既有 Profile 显式启用。
首次使用请从 [Web 使用指南](../product/WEBAPP_GUIDE.md)开始；下文记录启动与运维细节。

## 开发启动

```bash
./scripts/setup.sh --extras dev,webapp      # 默认已包含 Web 依赖
AGR_WEB_ORIGIN=http://localhost:8000 \
AGR_PROFILE=offline AGR_ALLOW_LLM=0 AGR_INGEST_INLINE=1 agr-webapp
```

默认地址 `http://localhost:8000`，数据库 `data/identity/webapp.db`。
首次启动通过 Alembic 升级身份库，开发签名密钥以 0600 权限保存在
`data/identity/signing.key`。整个 identity 目录已忽略，不提交数据库或密钥。
只测试身份层可调用 `webapp.app.create_app(include_business=False)`，无需推理引擎。
在仓库根目录运行，并先激活 `.venv`。`AGR_INGEST_INLINE=1` 启动与 API 共用存储的
上传处理器；没有启用处理器时，上传只入队。`AGR_PROFILE`、`AGR_ALLOW_LLM`、
`AGR_INGEST_INLINE` 从进程环境读取，按命令前缀设置或 `export`，不能仅写入 `.env`。

`AGR_WEB_ORIGIN` 必须与浏览器地址的协议、主机、端口一致。所有 Cookie 写请求先
`GET /v1/auth/csrf`，再以 `X-CSRF-Token` 和同源 `Origin` 提交。
认证接口同时提供 `/v1` 与无前缀路径；业务接口保留 `/v1` 原契约。

Web 入口 `/` 提供登录和注册，`/app/chat` 是受保护的笔记本；`/web` 重定向到它。
深链接由独立 Web 工厂回退到 `web/application.html`，API 和静态资源的 404 不受影响。
核心 `agr-api` 仍使用原 `web/index.html`，两者共享本地 Vue 和业务组件。
浏览器不需要 npm 构建；Vue Router 来源与许可证见 `web/static/vendor/README.md`。

`/app/settings/` 下提供 profile、security、members、api-keys、tenant 五个页面。
菜单与路由都按 `/v1/me.capabilities` 控制；成员、邀请、密钥和空间管理仅向管理员开放。
改密码会结束登录并返回成功提示；服务密钥创建后仅显示一次，离开页面即清除明文。

顶栏可直接创建 / 切换空间，账号菜单提供资料、安全、主题和退出。
笔记历史按 `user_id + tenant_id` 保存在当前浏览器，键为
`agr_notebook_v2:<encoded user_id>:<encoded tenant_id>`；不导入旧无归属历史。
退出清除该账号在所有空间的本地笔记，保留其它账号的数据与外观 / 推理偏好。
`/app/chat/:id` 只打开当前分区已有笔记；不属于当前分区的链接显示 404。

## 更换监听地址和端口

`agr-webapp` 固定监听 `127.0.0.1:8000`，不接收 `--port` 参数。
需要另一个端口时直接调用 Uvicorn，并同步设置浏览器来源：

```bash
AGR_WEB_ORIGIN=http://127.0.0.1:8001 AGR_WEB_SECURE_COOKIES=false \
AGR_PROFILE=offline AGR_ALLOW_LLM=0 AGR_INGEST_INLINE=1 \
uvicorn agentic_graphrag.webapp.app:create_app \
  --factory --host 127.0.0.1 --port 8001
```

浏览器打开 `http://127.0.0.1:8001`。对外部署时，`--host` 是监听地址，
`AGR_WEB_ORIGIN` 是用户实际访问的公开来源；不要把 `0.0.0.0` 当作浏览器来源。

## 查看开发邮件

默认 `AGR_WEB_MAIL_BACKEND=log`，邀请和密码重置邮件只写入
`agr.webapp.mail` 的 INFO 日志，不会发送真实邮件。
普通 `agr-webapp` 启动未配置应用 INFO 日志；仅设置 Uvicorn 的 `--log-level info`
也不能保证显示邮件。先停止已有服务，再用下面的命令启用开发邮件日志：

```bash
AGR_WEB_ORIGIN=http://localhost:8000 AGR_WEB_SECURE_COOKIES=false \
AGR_WEB_MAIL_BACKEND=log \
AGR_PROFILE=offline AGR_ALLOW_LLM=0 AGR_INGEST_INLINE=1 python - <<'PY'
import logging
from agentic_graphrag.webapp.app import run_server

logging.basicConfig(level=logging.INFO)
run_server()
PY
```

在网页发送 / 重发邀请，或提交“忘记密码”后，终端的 `Development email`
消息会显示收件人和完整链接。邀请链接以 `/invite/` 开头，重置链接以 `/reset?token=`
开头。访问日志会隐藏凭证；查找完整链接应看开发邮件日志。
重启前未输出的邮件不会补发，需要重新操作。

需要发送真实邮件时，在本地 `.env` 中填写自己的 SMTP 配置并重启服务：

```dotenv
AGR_WEB_MAIL_BACKEND=smtp
AGR_WEB_MAIL_FROM=graphrag@example.com
AGR_WEB_SMTP_HOST=smtp.example.com
AGR_WEB_SMTP_PORT=587
AGR_WEB_SMTP_USER=your-smtp-user
AGR_WEB_SMTP_PASSWORD=your-smtp-password
```

以上值是占位示例；实际服务必须支持 STARTTLS。当前实现不使用 465 端口的隐式 TLS。
切换 SMTP 时使用普通启动命令，移除命令前缀中的 `AGR_WEB_MAIL_BACKEND=log`，
否则进程环境仍会覆盖 `.env`。开发邮件含一次性凭证，不应公开日志。

## 连接真实模型

离线模式使用演示语料规则。接入自己的文档时，先在仓库根目录的本地 `.env` 中配置：

| 配置项 | 填写内容 |
| --- | --- |
| `LLM_API_KEY` | 模型服务商的 API Key |
| `LLM_BASE_URL` | 服务商的兼容 API 基地址 |
| `LLM_STRONG_MODEL` | 用于规划、复杂推理的可用模型名称 |
| `LLM_LIGHT_MODEL` | 用于轻量任务的可用模型名称 |
| `LLM_EMBEDDING_MODEL` | 服务商支持的 embedding 模型名称 |
| `LLM_EMBEDDING_BASE_URL` / `LLM_EMBEDDING_API_KEY` | embedding 使用独立服务时填写，否则沿用主服务 |

服务商模型密钥与“设置 → 服务密钥”中创建的本应用密钥用途不同，不能互换。
模型名称需与服务商提供的名称一致；参考 `.env.example`，保留已有 `.env` 中的配置。

```bash
AGR_WEB_ORIGIN=http://localhost:8000 \
AGR_PROFILE=offline AGR_ALLOW_LLM=1 AGR_INGEST_INLINE=1 agr-webapp
```

这里的 `offline` 选择内存业务存储；`AGR_ALLOW_LLM=1` 独立启用真实模型，可能产生调用费用。
该组合不需要 Docker，但仍需模型网络连接。通用语料接入还需核对领域 schema，见
[真实领域接入手册](REAL_DOMAIN_PLAYBOOK.md)。

身份库默认持久化，离线图谱和向量 / 全文索引在进程内存中；重启后，已完成的上传任务
不会自动重放到内存存储。长期运行需按 [运维手册](RUNBOOK.md)选择 `live` 等持久化配置，
并确保 API 与处理器共享相应存储。启用真实模型本身不会启用 Neo4j 或 Qdrant。

## 数据库与部署配置

WebSettings 从 `.env` 或进程环境读取；字段见 `.env.example`。

- `AGR_DATABASE_URL`：默认 SQLite；PostgreSQL 使用 `postgresql+psycopg://...`。
- `AGR_WEB_ORIGIN`：唯一允许的浏览器来源；反向代理保留此公开来源。
- `AGR_WEB_SECURE_COOKIES=true`：HTTPS 部署启用，使用 `__Host-` Cookie 名称。
- `AGR_WEB_SECRET`：HTTPS 部署必填，至少 32 个随机字符，各 worker 相同。
- `AGR_WEB_AUTO_MIGRATE=false`：部署时先单独运行迁移，再启动 worker。
- `AGR_WEB_ACCESS_SECONDS`（900）/ `AGR_WEB_REFRESH_SECONDS`（604800）/ `AGR_WEB_INVITE_SECONDS`（604800）：访问、刷新与邀请令牌有效期（秒）。
- `AGR_WEB_AUTH_ATTEMPTS`（10）/ `AGR_WEB_AUTH_WINDOW_SECONDS`（300）：登录尝试次数与窗口。
- `AGR_WEB_SECRET_FILE`（`data/identity/signing.key`）：未设 `AGR_WEB_SECRET` 时的本地开发签名密钥路径。
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
会话列表与单会话撤销：`GET /v1/auth/sessions`、`DELETE /v1/auth/sessions/{session_id}`（`webapp/routes_account.py`；无 `/v1` 前缀的别名同样可用）。
数据库只存 refresh / reset 凭证摘要；密码采用 Argon2id。登录限流同时按 IP 和
账号摘要计数，状态保存在身份库，各 worker 共享，默认 5 分钟内 10 次。

开发日志邮件包含一次性找回链接；生产使用 SMTP 并限制日志访问。
找回接口始终返回相同受理结果；邮箱不可通过该响应枚举。

浏览器请求使用 Cookie，CSRF 仅保存在内存。并发 401 合并刷新，刷新后仅重试一次；
CSRF 失效时重新获取并仅重试一次。SSE 开始消费后不重放查询。
支持 Web Locks 的浏览器会在同源标签间串行执行刷新与 Cookie 写入。

Cookie 业务请求附带发起时的 `X-AGR-User` / `X-AGR-Tenant` 前置条件。
服务端仍从 Cookie 或 API Key 鉴权，前置头不能指定或提升身份。
若 Cookie 已切换，服务端在写入前返回 `409 IDENTITY_CHANGED`；前端冻结旧工作区、
取消请求并重读 `/v1/me`，不将原写入重试到新空间。此条件不影响服务 API Key。
带前置身份的退出请求在 access 过期时先刷新验证，不能直接撤销其它账号的 refresh 会话。

切换与退出会取消 fetch、流、轮询和旧视图的异步回写，并清空检查器。
BroadcastChannel 和 storage 事件同步账号状态，通知仅含事件类型与随机 nonce；
标签重新聚焦也会校验 `/v1/me`。同步失败时显示重试入口并隐藏旧历史，验证身份后再恢复。

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
使用指南的离线流程已用临时 SQLite 和运行目录验证：注册、空间切换、导入两份样例、
开发邮件链接和服务密钥均通过；`doc_01` / `doc_02` 入图后，快速与多跳问答均返回 Elena Varga。
SQLite 单测不代表 PostgreSQL 或真实图／向量后端已完成部署验证。
