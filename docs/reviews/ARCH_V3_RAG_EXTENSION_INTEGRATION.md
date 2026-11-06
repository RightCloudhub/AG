# 架构 V3、多 Agent 与图谱扩展集成

日期：2026-11-06。分支：`merge/arch-v3-multi-agent-rag-extend`。

## 来源与范围

| 来源 | 固定提交 |
|---|---|
| `merge/arch-v3-multi-agent`，新 worktree 的基线 | `e628ea537ba528e123011e49fdcd1b18b3e22797` |
| `feat/rag-triage-bench-ops-extend`，第二个 merge parent | `2f729be01a43e0b0a0d9faa1c2a48c81dfa0e2c7` |

按用户确认，只合入已提交的 tip。第二个来源 worktree 中尚未完成的 merge、
暂存和未提交修改不属于本次集成；原分支和原 worktree 保持不变。

## 冲突与组合边界

21 个文本冲突已解决，并保留两侧提交历史。主要组合处理：

- QueryApp 同时保留知识库/文档/Global 路由、取消和持久化，以及显式 multi 配置、
  根预算上限和仅记录问题长度的 trace。`build_query_deps` 保留为兼容导出。
- 七角色模型配置支持 multi 的字符串角色选择和子预算；所有角色与 embedding
  共享根预算，子作用域不修改原 provider。legacy 单模型的语义角色选择保持原行为。
- HTTP 连接池与共享熔断、半开许可、调用预留/结算组合；拒绝、截断和无效返回仍记账。
  embedding usage 无效仍计已完成调用；rerank 在发请求前检查 token 上限。
- 结构化输出采用模型能力配置，同时保留操作阶段归因、解析失败计数和输出预算。
- REST/SSE 导出增加已有的 `embed_calls` 成本字段；兼容检查支持 OpenAPI 的布尔
  `required`，并递归检查同名业务字段。新回归先复现该检查器崩溃，再验证修复。

## 验证

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` / `ruff format --check src tests scripts` | 通过，702 文件格式一致 |
| `mypy` / TypeScript 5.9.3 `tsc --project web/jsconfig.json` | 通过，503 Python 源文件 |
| `python scripts/check_code_metrics.py` | 通过，无新增豁免 |
| `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` | 1327 passed、4 既有 xfailed；92.24% |
| 后续契约检查器回归：`pytest tests/unit/test_api_contract_exports.py -q` | 4 passed，包含新增布尔 required 回归 |
| `export_api_contracts.py --check --base-ref 2f729be01a43e0b0a0d9faa1c2a48c81dfa0e2c7` | 无漂移，与扩展分支兼容 |
| SDK 独立锁定环境：lint / format / mypy / coverage / wheel 与 sdist | 39 passed；98.90% |
| MCP 独立锁定环境：lint / format / mypy / coverage / wheel 与 sdist | 22 passed；95.06% |
| G2 金标审计 | 200 cases、0 flagged |

离线配对使用同一解释器、依赖、数据集和 seed/pilot 图；基线为第一个 parent 的干净导出，
关闭 LLM 与缓存，并将 `agent.mode=multi` 验证为确定性离线回退。

| 数据集 | 基线 / 候选 | McNemar p |
|---|---|---|
| POC agentic / triage | 均 31/31 | 1 |
| G2 | 均 200/200 | 1 |
| compositional / negative | 均 54/54、20/20 | 1 |
| G2 dev | 均 153/153 | 1 |

所有下限和 `eval_compare.py --fail-on-worse` 通过，LLM 调用均值为 0。
G2 首轮链内 P95 为 81→117 ms；两次复测为 78→68、67→70 ms，三轮中位数为
78→70 ms，未复现持续退化。这些本地链内计时不代表 HTTP 端到端或真实模型性能。

原始日志和逐题输出在本机 `/tmp/ag-arch-rag-merge-l3nxm98x/`，临时文件可能被清理。
本次没有运行真实供应商验收，也没有改变 multi 的默认开关。
来源分支的产品验收和已知限制继续见 [IMPORTANT.md](IMPORTANT.md)、
[ARCH_V3_MULTI_INTEGRATION.md](ARCH_V3_MULTI_INTEGRATION.md) 与
[GRAPH_PLATFORM_EXTENSION.md](GRAPH_PLATFORM_EXTENSION.md)。
