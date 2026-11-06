# AgenticGraphRAG Python SDK

独立 HTTP 客户端，依赖 `httpx` 与 Pydantic；不安装图数据库、LangGraph 或 AG 服务端。
Python 3.12+。服务端须提供扩展后的 `/v1` 接口。

从本仓库开发与验证：

```bash
cd sdk/python
./scripts/setup.sh
uv run --locked pytest --cov=ag_client -q
uv run --locked mypy
uv run --locked uv build --no-sources
```

发布后的 wheel 可用标准 Python 包安装工具安装。本仓库尚未发布到 PyPI。

```python
from ag_client import Client, QueryRequest

with Client("http://localhost:8000", api_key="<AG API key>", kb_id="contracts") as ag:
    result = ag.query(QueryRequest(
        question="这些合同有哪些风险？",
        kb_id="contracts",
        strategy="global",
        audit_required=True,
    ))
    print(result.answer, result.audit_status)
    audit = ag.get_audit(result.query_id, kb_id="contracts")
    hits = ag.search("无限赔偿", top_k=5, audit_required=True)
    evidence = ag.get_evidence(hits.candidates[0].id)
    print(evidence.content, evidence.source_refs)
```

密钥示例只是占位符；实际由调用程序的密钥管理或环境配置传入。SDK 发送 Bearer
凭据，租户由服务端身份决定。请求不能自行切换租户。无鉴权本地 AG 可省略密钥。
构造器的 `kb_id` 用于字符串请求与读取接口；显式 `QueryRequest` / `SearchRequest`
保留自己的知识库配置。完整参数通过这两个模型传递，非法参数在发送前报错。

同步与异步接口相同：`query`、`search`、`get_evidence`、`get_audit`、
`list_knowledge_bases`、`iter_knowledge_bases` 和 `stream`。
`get_audit(kind="search")` 返回检索审计，默认 `kind="query"` 返回查询推理链。
列表返回 `KnowledgeBasePage(items, total, page, limit)`，迭代接口自动分页。
证据读取接受 `offset` / `limit`；`has_more` 指示是否还有后续内容。

```python
import asyncio
from ag_client import AsyncClient, StreamInterrupted

async def main():
    async with AsyncClient("http://localhost:8000") as ag:
        try:
            async with ag.stream("Apex Holdings 的 CEO 是谁？") as events:
                async for event in events:
                    if event.result is not None:
                        print(event.result.answer)
        except StreamInterrupted as error:
            print("连接中断；可按 query_id 回查：", error.query_id)

asyncio.run(main())
```

流必须放在 `with` / `async with` 内；提前退出或取消会关闭 HTTP 响应。
已发送的模型调用仍可能计费。普通查询返回 `QueryResultData`，流式 `answer` 保留
服务端 `ReasoningChain`；`event.result` 给出统一 `QueryResult` 视图，原始数据在
`event.data`。新增响应字段保留在 `model_extra`，未知 SSE 事件被忽略。

`APIError` 暴露业务 `code`、`message`、HTTP 状态与 request ID。
协议不匹配抛 `ProtocolError`，通信失败抛 `TransportError`。
缺少终态的流抛 `StreamInterrupted` 并保留已知 query ID。
SDK 不实现断点重放，不自动重发查询、检索或流式 POST；确定安全的 GET 遇到网络错误
或 502/503/504 默认重试一次，可用 `read_retries=0` 关闭。超时默认 120 秒。

独立包测试使用假 HTTP；核心联合契约测试连接实际 FastAPI 路由，覆盖查询、SSE、原文、
审计和作用域拒绝。独立 wheel 与完整 stdio/HTTP 链路的验证见
[交付记录](../../docs/reviews/GRAPH_PLATFORM_EXTENSION.md)。真实领域、供应商模型和生产预算
仍需部署方另行验收。
