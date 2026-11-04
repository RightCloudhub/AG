# 角色模型配置与版本发布

AG 保留默认离线运行与已有 strong/light 配置。配置 `models.roles`，或为知识库绑定已发布的
模型方案后，使用七个角色：triage、researcher、verifier、writer、extractor、embedding、rerank。
Planner 使用 researcher；社区/文档摘要使用 writer。rerank 可以显式设为 `null`。

## 登记可用模型

部署管理员在应用 YAML 中登记 `models.providers` 与 `models.catalog`。
[配置示例](../../configs/model_profiles.example.yaml)展示无鉴权本地服务；模型名称与向量维数
必须按本地已部署的模型修改，AG 不会自动下载模型。

`ProviderConfig.base_url` 必须是没有内嵌凭据、查询参数或 fragment 的 HTTP(S) 地址。
`allowed_tenants` 列出可使用该服务的租户；`["*"]` 表示部署管理员明确允许所有租户。
管理 API 只接受目录 ID，不允许浏览器传入任意地址、密钥或超时上限。

远程鉴权服务设置 `auth_required: true` 与 `secret_ref`。引用 `LLM_API_KEY` 时读取已有
环境变量；其他引用从 `MODEL_SECRETS` 的 JSON 对象解析，例如在本地环境中配置
`MODEL_SECRETS={"provider-a":"<部署密钥>"}`。密钥值不得写入 YAML 或提交到仓库。
管理接口的目录列表不返回地址或密钥引用，更不会返回密钥值。

真实调用仍要求显式 `AGR_ALLOW_LLM=1`。兼容旧 strong/light 配置的无鉴权本地服务还需
设置 `LLM_AUTH_REQUIRED=false`；七角色目录则使用每个 provider 的 `auth_required`。
默认 `--no-llm` 不触发任何服务请求。

## 管理端操作

在“系统运维 → 角色模型”选择七个角色的模型，并保存草稿。草稿不影响已有查询。
点击“测试连通性”后，服务器用固定非业务文本执行真实的小请求：

- 聊天角色分别测试文本生成与已声明的结构化输出模式。
- embedding 用两个输入测试数量、顺序、有限值和声明的维数。
- rerank 要求每个输入索引恰好出现一次，且分数为有限数值。

界面显示每项结果、耗时与用量。测试不会发布配置。全部必需角色通过后，点击“发布版本”。
发布和回滚都检查预期活动版本；另一位管理员已发布新版本时返回 409，请刷新后操作。
回滚只能选择已通过测试且仍匹配部署目录的历史版本。

聊天、验证与抽取模型可以发布新角色绑定。embedding 的 provider/model revision、维数及
归一化约定构成独立向量空间，不能把相同维数当作相同模型。已经发布的方案禁止直接切换
embedding 目录 ID；应创建新的方案，重建知识库对应索引后再切换。部署目录改变后，旧方案
不会静默指向新模型，必须重新保存并测试草稿。

## API 与持久化

以下接口全部要求管理员权限，身份来自已认证的租户：

| 接口 | 用途 |
|---|---|
| `GET /v1/model-profiles/catalog` | 列出当前租户可用的模型与能力 |
| `GET /v1/model-profiles` | 最新草稿、活动版本和测试记录 |
| `POST /v1/model-profiles` | 以 `expected_revision` 保存不可变的新草稿 |
| `POST /v1/model-profiles/{id}/test` | 测试指定 `revision` |
| `POST /v1/model-profiles/{id}/publish` | 以 `expected_active_revision` 发布指定版本 |
| `POST /v1/model-profiles/{id}/rollback` | 以同样的并发检查回滚到历史版本 |

配置、测试和发布记录位于核心运行目录的 `model_profiles.sqlite3`，不依赖可选 Web 身份库。
备份时同时保留该文件与部署 YAML；密钥由部署的密钥管理方案独立备份。
知识库绑定固定 `model_profile_revision`，不会自动追随方案的新活动版本。更新绑定须通过
知识库 PATCH 的 `expected_revision` 检查与影子 generation 发布；重建失败保留旧版本。
配置与索引恢复步骤见 [知识库运行手册](GRAPH_PLATFORM.md)。
调用缓存包含租户/知识库、端点、模型与配置版本。角色用量分开记录，HTTP 连接在查询内复用。
`json_schema` 对包含自由字典的响应（工具参数、三元组属性等）使用兼容的 `json_object`
模式，并保留 Pydantic 校验；固定字段响应继续使用 strict schema，避免把字典限制为 `{}`。
拒答、截断与空输出各自失败，不作为 JSON 解析错误自动重试；付费 POST 不进行自动传输重试。

## 验证范围

单测使用假 HTTP transport，覆盖角色选择、审计用量、能力检查、故障、版本冲突、租户隔离
和默认兼容。浏览器流程使用本机固定响应服务检查保存、测试与发布，不能证明某个厂商或
生产模型已兼容；已验证第二版发布后回滚至第一版。该固定响应器只接受非业务探测文本，
没有执行真实模型的抽取或问答。上线前需要对实际模型版本执行管理端测试，再运行领域
留出集与成本评测。截图和验证范围见 [交付记录](../reviews/GRAPH_PLATFORM_EXTENSION.md)。
