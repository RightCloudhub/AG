# 工作流：评测体系

**覆盖需求**：FR-OP-04、AC-1/2/6/7、NFR-02 · **相关阶段任务**：P1-EV-*、P2-EV-*、P3-EV-*
**负责人**：全员参与设计，标注由人工/众包资源执行（立项书第七节）

## 1. 评测集构成（≥200 条）

| 类别 | 数量 | 说明 |
|---|---|---|
| 2 跳问题 | ~90 | 如"A 的母公司的 CEO 是谁" |
| 3 跳问题 | ~60 | 跨 3 层关系链 |
| 开放路径问题 | ~30 | 交集/聚合/因果链，跳数不定 |
| 无答案问题 | ≥20 | 图谱确认无此知识，考察诚实性（AC-7） |

另设**护栏专项集**（AC-6，不计入 200 条）：发散问题、循环诱导、超长输入、注入尝试。

## 2. 构建方法（PRD 开放问题 #3 的落地方案）

**合成 + 人工修订**（推荐，降低标注成本）：
1. 从已建图谱采样多跳路径（2/3 跳、交集模式）→ 模板/LLM 生成自然语言问题
2. 人工修订问题表述自然度，剔除歧义/退化问题（实际单跳可答的"伪多跳"）
3. 金标 = 答案 + 支持证据（图上节点/边 ID + 对应文档片段），由路径采样自动携带、人工确认
4. 参考 HotpotQA / 2WikiMultihopQA 的构造模式迁移到自有领域

**防过拟合**：评测集分 dev（~150，日常调优可见）与 held-out（~50，仅门禁评审时跑）；badcase 回流只进 dev。

## 3. 指标定义（口径一次定死，避免评审争议）

| 指标 | 口径 |
|---|---|
| Answer Accuracy | **确定性判卷**：包含匹配（归一化 + 词边界）为主、token 重叠为回退（`eval/scoring.py`、`evals/judge.py`）；无答案问题答"不知道"记为正确。LLM 判卷延后（`evals/judge.py` 注明 deferred），无 20% 人工校准 |
| 证据 Recall | 金标证据条目被推理链引用的比例，按题平均；仅统计跳数 ≥2 的题（AC-2 口径） |
| 编造率 | 严格口径：论断须（1）带 `evidence_ids`、（2）id 在检索集合内、（3）与所引证据词项支持且**关系一致**（`eval/metrics_evidence.py`），目标 0（AC-7）。"仅看 `evidence_ids` 是否为空"的历史口径保留为 `unbound_claim_rate` |
| 延迟 | 端到端 P50/P95；当前输出总体值 + 按跳数（`by_hops`）分解（`eval/metrics.py`），未按 Fast Path / Agentic 路由分解 |
| 成本 | 单题 LLM 调用次数、token 消耗；同样按跳数分解，未按路由统计 |

## 4. Baseline（P2-EV-03）

需求登记表的归因实验另使用同配置 agentic 基线，仅关闭登记表开关；
不得以纯向量 baseline 替代。新增分析脚本 `scripts/analyze_search_drift.py`
报告 Contain-Match、可选 LLM 标签、弃答与选择性准确率、失败归因与可达性。
段落召回要求先做 gold→chunk 对齐；当前 G2 的实体/关系金标不能充当段落 id。
协议与字段见 [需求登记表评估](../../docs/design/REQUIREMENT_REGISTER.md#评估)。

纯向量 RAG：同语料 chunk → 同 embedding → Top-K 召回 → 同款生成模型单轮生成。
**公平性约束**：与 Agentic 用同一 LLM 档位、同一判卷器、同一评测集。

## 5. 评测脚本（P2-EV-04）

```bash
agr-run-cases --no-llm --memory-graph --cases evals/datasets/g2_dev.jsonl --run-name dev   # 产出 reports/*.jsonl
agr-run-baseline --no-llm --cases evals/datasets/g2_dev.jsonl                              # 纯向量 Baseline
agr-eval --agentic reports/dev.jsonl --baseline reports/baseline_run.jsonl \
  --cases evals/datasets/g2_dev.jsonl --out reports --stem eval_comparison                  # 对比报告（JSON + MD）
```

入口是 `src/agentic_graphrag/cli/` 的 `run_cases_main` / `run_baseline_main` / `eval_main`；不存在 `evals run --system/--set` 形式。
输出：指标汇总表（含与 Baseline 差值）、分跳数分解、badcase 清单（附推理链链接）、成本延迟分布。报告存档，门禁评审引用。

## 6. badcase 归因分类（P2-EV-05）

| 归因 | 责任工作流 |
|---|---|
| 图谱缺失/错误 | knowledge-graph |
| 检索未召回 / 排序沉底 | retrieval |
| 分解错误 / Critic 误判 / 循环触顶 | agent-orchestration |
| 生成错误（证据对但答错/编造） | agent-orchestration（生成层） |
| 金标错误 | 评测集修订 |

实现口径（`eval/badcase.py`）：`graph_missing` / `retrieval` / `decomposition` / `generation` / `correct` / `gold_error`。没有独立的"分诊错误"桶，且新增 `correct`（答对）与 `gold_error`（金标问题）两类。

每轮评测后归因分布进入报告，指导下一轮优化优先级。

## 7. 节奏

- **POC**：31 条 case 人工跑测（P1-EV-*；数据集已由 20 条扩至 31 条，CI 门禁 30/31）
- **MVP**：两轮全量评测（P2-EV-05/06），G2 用 dev 集
- **优化**：回归评测常态化，G3 用 held-out 集出最终达标结论（P3-EV-01）
- **试点后**：评测纳入 CI 周期回归（cicd-observability.md），线上反馈按季回流 dev 集（P5-GOV-02）
