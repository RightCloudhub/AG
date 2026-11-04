# AgenticGraphRAG MCP Server

此包通过官方 Python MCP SDK 提供 stdio 传输，业务调用全部使用 AG Python SDK
和公共 HTTP API。首版只读，默认要求查询与检索审计持久化。

开发安装、运行和验证：

```bash
cd integrations/mcp
./scripts/setup.sh
AG_MCP_BASE_URL=http://localhost:8000 uv run --locked ag-mcp
uv run --locked pytest --cov=ag_mcp -q
uv run --locked mypy
uv run --locked uv build --no-sources
```

`uv.lock` 使用仓库内的独立 SDK。两个 wheel 可一起交付；尚未发布至 PyPI。
AG 服务必须另行启动，MCP 进程不直接加载服务端、数据库或业务模型。

配置由本包 `Settings` 从环境读取：

| 变量 | 默认 | 用途 |
|---|---|---|
| `AG_MCP_BASE_URL` | `http://localhost:8000` | AG API 地址 |
| `AG_MCP_API_KEY` | 无 | AG Bearer 凭据；服务端据此确定租户 |
| `AG_MCP_KB_ID` | `default` | 工具未指定知识库时使用的知识库 |
| `AG_MCP_TIMEOUT_SECONDS` | `120` | HTTP 超时 |

可在支持 stdio 的 Claude Desktop / Cursor 配置中登记：

```json
{
  "mcpServers": {
    "agentic-graphrag": {
      "command": "/absolute/path/to/integrations/mcp/.venv/bin/ag-mcp",
      "env": {
        "AG_MCP_BASE_URL": "http://localhost:8000",
        "AG_MCP_KB_ID": "contracts"
      }
    }
  }
}
```

使用客户端的安全环境配置注入 `AG_MCP_API_KEY`；不要把真实密钥提交到配置示例。
默认 stdout 只包含 MCP JSON-RPC，日志输出到 stderr。

工具包括 `list_knowledge_bases`、`search`、`query`、`get_evidence`、`get_audit`。
输入和结构化输出均有 JSON Schema；`query` 与 `search` 强制 `audit_required=true`，
持久化失败返回明确的工具错误。查询有费用且会生成审计，因此不声明幂等。
工具不能覆盖 API 地址、密钥或租户；同租户知识库权限仍由服务端执行。
`get_evidence` 支持限长分页，`get_audit(kind="query"|"search")` 回查相应审计。

SDK 与 MCP 不自动重发计费 POST。取消会传递至异步 HTTP 客户端；服务端已发出的
模型调用仍会结算。AG 查询 SSE 与 MCP stdio 是不同协议；此版本没有远程 MCP
Streamable HTTP、SSE 持久化重放或跨知识库查询。

包内自动化使用官方通用 MCP 客户端与假 HTTP 完成 stdio 工具发现和取证；核心联合测试
再连接实际 FastAPI 路由。独立 wheel 的完整 stdio/HTTP 验证见
[交付记录](../../docs/reviews/GRAPH_PLATFORM_EXTENSION.md)。Claude / Cursor 的真实版本接入
结果仍须部署方记录，本文的配置示例不代表已完成这些客户端的验收。
