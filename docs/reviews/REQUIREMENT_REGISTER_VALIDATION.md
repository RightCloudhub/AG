# 需求登记表工程验证（2026-10-23）

状态：工程实现已验证；真实 LLM 多跳效果评估待完成。默认关闭登记表。

| 验证 | 结果 |
|---|---|
| Ruff lint / format、代码指标、diff whitespace | 通过 |
| CI 范围单测 | 428 passed，覆盖率 ≥87% |
| 完整单测 | 既有前端 7 项失败；CI 已明确排除此模块 |
| POC agentic / triage | 两条路径均 21/31，达到 21/31 门槛 |
| G2 dev，pilot graph，no LLM | 120/153 |
| 改动前 G2 / 改动后默认路径 | 157/200 → 157/200；逐题判分一致，McNemar p=1 |
| 开启登记表配置但禁用 LLM | 157/200；仍走原流程，逐题判分一致，p=1 |

G2 157/200 低于仓库 167/200 门槛，改动前即存在。此次没有降低门槛或
修改演示答案启发式。默认路径基线 P95 113ms，改动后 106ms；登记表配置
开启但禁用 LLM 的回归为 102ms。这些是单次离线机器测量，不能解释为
登记表带来效果或延迟改善，三次实验的平均 LLM 调用数均为 0。

新增测试覆盖：状态不消失、证据追加、未知引用与无理由退休被拦截、文本和
依赖不可删改、实体锚与依赖就绪、单 chunk 解决多项、独立 Verifier 否决和
故障、更新 JSON 错误、预算中断、去重与已读集合、checkpoint 恢复、SSE、
种子/更新/delta/Verifier 消融开关、部分答案与严格弃答、失败优先级、
gold 模糊对齐、未对齐排除、按 hop 召回、选择性准确率和重复实验方差。

实际验证命令：

```bash
ruff check src tests scripts
ruff format --check src tests scripts
python scripts/check_code_metrics.py
pytest tests/unit --ignore=tests/unit/test_web_claude_ui.py \
  --cov=agentic_graphrag --cov-fail-under=80 -q
agr-run-cases --no-llm --memory-graph --cases evals/datasets/poc_cases.jsonl
agr-run-cases --no-llm --memory-graph --enable-triage --cases evals/datasets/poc_cases.jsonl
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_all.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_dev.jsonl \
  --seed-triples data/processed/pilot_triples.jsonl
PYTHONPATH=src python scripts/eval_compare.py BASE.jsonl CAND.jsonl \
  --cases evals/datasets/g2_all.jsonl
PYTHONPATH=src python scripts/analyze_search_drift.py CAND.jsonl \
  --base BASE.jsonl --cases evals/datasets/g2_all.jsonl --out DRIFT.json
```

评估原始 JSONL 写在 `/tmp/agr-register-*`，避免覆盖仓库已有报告。开启配置
但禁用 LLM 的回归在单独进程中设置
`get_config().guardrails.requirement_register.enabled = True`，其余配置相同。

G2 实体/关系金标未对齐为段落，因此新增分析正确报告段落召回 null、
180 题未对齐；LLM 判卷未执行，LLM-Eval 为 null、样本数 0。尚未运行
HotpotQA / 2Wiki / MuSiQue 三个各 1000 题的真实模型、多种子对照，不能
声称复现参考文档中的收益。详见
[设计与评估协议](../design/REQUIREMENT_REGISTER.md)。
