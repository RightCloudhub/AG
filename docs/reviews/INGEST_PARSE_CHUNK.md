# BL-08 文档解析与分块验证（2026-11-03）

`feat/ingest-parse-chunk` 在 API/CLI 引入统一 `DocumentParser`，解析结果保存为 Markdown，
含解析器版本、源 MIME 与编码信息。分块按标题层级与 token 预算处理，保留整行表格、
围栏代码及列表项，每块带 breadcrumb。MinerU 默认关闭，通过 `/file_parse` 显式接入。

基线提交：`844d2b0f56432303836dd525fa00ef78e7a5469b`。基线在实现变更前运行，候选使用
同一组数据、seed/pilot triples、既有检索 fixtures 及 `--no-llm --memory-graph`。
未修改 doc_id 规则、chunk ID 格式、索引/embedding 实现或 CI 分数下限。

## 离线评测

| 数据集 / 路径 | 变更前 | 变更后 | 准确率变化 | CI 下限 |
|---|---:|---:|---:|---:|
| POC / agentic | 31/31 | 31/31 | 0 pp | 30/31 |
| POC / triage | 31/31 | 31/31 | 0 pp | 30/31 |
| G2 | 200/200 | 200/200 | 0 pp | 196/200 |
| Compositional probe | 54/54 | 54/54 | 0 pp | 52/54 |
| Negative probe | 20/20 | 20/20 | 0 pp | 19/20 |

五组逐题分数也均无变化。以上为查询回归结果，复用仓库既有图谱/检索 fixtures；
不作为真实 OCR 质量或重新建立索引后的检索效果证明。解析能力由离线文件字节、
HTML 和 mocked MinerU Markdown fixture 回归验证。

对应命令（先激活 `.venv`）：

```bash
agr-run-cases --no-llm --memory-graph --cases evals/datasets/poc_cases.jsonl --run-name parse_chunk_poc
agr-run-cases --no-llm --memory-graph --enable-triage --cases evals/datasets/poc_cases.jsonl --run-name parse_chunk_triage
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_all.jsonl --seed-triples data/processed/pilot_triples.jsonl --run-name parse_chunk_g2
agr-run-cases --no-llm --memory-graph --cases evals/datasets/probe_compositional.jsonl --seed-triples data/processed/pilot_triples.jsonl --run-name parse_chunk_compositional
agr-run-cases --no-llm --memory-graph --cases evals/datasets/probe_negative.jsonl --seed-triples data/processed/pilot_triples.jsonl --run-name parse_chunk_negative
```

本次通过 `cfg.eval.report_dir` 将原始报告隔离至
`reports/ingest_parse_chunk/{before,after}/{数据集}/`，比较汇总为
`reports/ingest_parse_chunk/summary.json`。五次 `scripts/check_eval_floor.py` 全部通过。
`scripts/audit_gold.py evals/datasets/g2_all.jsonl --triples data/processed/pilot_triples.jsonl
--max-flagged 0`：200 cases，0 flagged。

## 工程门禁

| 命令 | 结果 |
|---|---|
| `ruff check src tests scripts` | 通过 |
| `ruff format --check src tests scripts` | 427 files，全部已格式化 |
| `mypy` | 309 source files，无错误 |
| `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` | 714 passed，89.82% coverage |
| `python scripts/check_code_metrics.py` | 通过，未增加豁免 |
| `uv lock --check --offline` | 通过 |
| `uv build --offline --wheel --out-dir /tmp/agr-parse-chunk-dist` | 通过 |

wheel 已检查默认词表、MIT 许可和说明文件均被打包，并从解包后的 wheel 使用空 tokenizer
cache 完成 tokenization。CLI 另在临时 processed/index/cache 目录运行样例语料摄取，
未写入既有索引或 embedding。单测唯一 warning 来自既有 Starlette/httpx 弃用提示。

## 配置与边界

- `chunk_size_tokens: 512` / `chunk_overlap_tokens: 64` 取代旧 `*_chars`；旧键明确报错。
  breadcrumb 包含在 token 上限内，重叠只取同一 section 的正文尾部。
- 表格可按整行拆分并重复表头，MinerU 的 HTML 表格先转为 Markdown；围栏里的 HTML
  示例不转换。单个代码块、列表项、引用块或表头加一行超限时明确报 failed 并提示增大预算。
- HTTP 类型/签名不符或 MinerU 未启用为 415；无法可靠解码/无有效正文为 422；服务错误为
  503；文件/批量超限保留 413。
- MinerU 仅验证了 mock HTTP 合约，真实部署需验证 PDF、Office 和 OCR 能力；服务配置见
  [RUNBOOK §6.1](../ops/RUNBOOK.md#61-文档解析与分块bl-08)。现有 Web 文件选择器仍限定 MD/TXT。
