# 需求登记表：控制搜什么与何时停止

登记表模式接入现有 `planner → executor → critic → answer` 状态机，流式与
非流式查询共用实现。它只改变搜索引导与停止判断，复用现有工具注册表、
向量检索、BM25、图检索、融合、语料与答案引用校验。

## 启用与预算

在 `configs/default.yaml` 设置 `guardrails.requirement_register.enabled: true`。
默认关闭，便于在当前检索栈完成同配置对照后再灰度开启。还必须显式启用
LLM；`--no-llm` 或没有 LLM 的查询继续使用原来的确定性流程。
登记表不会自行联网、切换存储或开启 LLM。

| 配置 | 默认 | 行为 |
|---|---|---|
| `seed_top_k` | 5 | 原问题的向量种子检索 |
| `max_rounds` | 15 | 轮数上限，同时受 `guardrails.max_hops` 限制 |
| `context_limit_tokens` | 128000 | 用完整提示内容的 UTF-8 字节数保守估算上下文上限 |
| `convergence_ratio` | 0.8 | 实际轮数上限的 80% 时注入收敛提醒 |
| `max_requirements` | 32 | 拒绝超限更新，保留已有需求 |
| `verifier_enabled` | true | 独立提示与 LIGHT 模型档位验证拟解决项 |
| `answer_policy` | partial | `partial` 给有引用的部分答案；`abstain` 有未解决项则弃答 |
| `seed_enabled` / `updates_enabled` / `delta_enabled` | true | 种子、更新、delta 消融开关 |

当前默认护栏仍为 4 跳、16 次 LLM 调用、50000 token、45 秒。因此只开启
登记表不会把一次请求的搜索预算提高到 15 轮。需要 15 轮实验时，先为基线和
候选同时配置 `max_hops: 15`、足够的调用/时间预算；服务端仍限制请求覆盖值。
初始化、更新、Verifier、最终生成都计入既有 LLM 预算。LangGraph 的递归
上限继续由既有护栏根据跳数计算。

## 状态与执行

每条需求有 `req_id`、`text`、`status`、`notes`、`passages`、`retire_reason`、
`anchors` 和 `depends_on`。只有 `pending / active / resolved / retired`
四种状态；部分进展写入 `notes`。LLM 可以返回 `evidence_notes` 作为别名。

初始化只能产生未完成项。更新返回受新证据触及的条目，每条必须用
`touched_passages` 指向本轮新内容；省略的旧条目自动恢复。代码强制：

- 支撑证据追加，引用只能来自本次查询实际取回的 Candidate。
- 已有需求的文本和依赖保持不变，细化写入笔记；前提改变须显式退休并新增需求。
- 无支撑不能 resolved，无非空理由不能 retired。
- 实体锚必须出现在所引用的证据文本中。
- 拒绝不存在的依赖与依赖环；仅调度依赖已 resolved 的未完成项。
- 有可调度项时恰好一条 active；依赖被 retired 阻塞时保留未完成项并显式停止。

调度优先选择尝试次数较少的就绪需求，避免一条卡住的需求饿死其他需求。
每次 query 包含原问题、当前子问题、笔记和已确认的实体锚。工具选择复用
现有确定性策略，不修改检索器。一次更新可同时解决多条需求。

Verifier 只收到子问题和拟引用的完整证据，不收到原问题、其他需求、
更新模型的推理或建议结论。它使用另一套提示和配置中的 LIGHT 档位；
如果 STRONG 与 LIGHT 配成同一个模型，这属于独立提示校验，并非独立模型。
否决、无效 JSON、服务错误或上下文超限都不能让拟解决项结案。预算异常时
保留上一张已通过校验的登记表并停止检索。

同一查询内的缓存按工具和参数隔离：BM25 查询小写、压缩空白、词排序；
语义查询小写、压缩空白、保留词序；图参数使用稳定 JSON。失败的调用保持
可重试，成功的空结果也缓存。已进入更新流程的 Candidate 不再次投给更新
提示。初始化后只保留初始状态与代码批准的 delta，不累积旧证据正文。
`delta_enabled: false` 消融会改为注入完整当前状态，避免让更新模型读到过期表。

所有需求关闭或任一预算达到上限后，最终生成不再回到检索节点。仍未解决
的需求会写入 `missing_info` 和答案正文，并禁止将最终状态标为 answered。
最终答案仍须通过原有引用与论断校验。

## 审计

AgentState 版本为 2；新增字段均可选，旧的版本 0/1 状态仍可读取。
登记表、缓存、已读集合、尝试次数、上下文和进度事件进入 checkpoint。
最终 `ReasoningChain.metadata` 包含：

- `requirement_register`：最后一张代码批准的表。
- `register_events`：初始化、逐轮状态变化、拟解决事件与 Verifier 结果。
- `register_stop`、`register_rounds`：停止原因和实际轮数。
- `register_retrieved_ids`、`register_evidence`：全部取回的证据及原文。
- `register_context_byte_bound`：保守上下文估算。

每个进度事件携带轮次、需求 id、前后状态、笔记和支撑 id。被代码或 Verifier
拒绝的解决提议也会记录 `accepted: false`。普通 SSE `thinking` 事件的
`stage: progress` 发送相同进展，兼容现有客户端。递归兜底也保留表和未完成项。
提示词通过现有注册机制记录内容哈希，未增加另一套审计存储。

## 评估

同配置基线是 `requirement_register.enabled: false` 的现有 agentic 流程，
不是纯向量 RAG。固定生成器、工具、语料、embedding、融合和预算；只切换
登记表。先通过离线回归，再在真实 LLM 与已对齐的多跳数据上做配对实验。

```bash
PYTHONPATH=src python scripts/eval_compare.py BASE.jsonl CAND.jsonl \
  --cases CASES.jsonl --json reports/register_paired.json
PYTHONPATH=src python scripts/analyze_search_drift.py CAND.jsonl \
  --base BASE.jsonl --cases CASES.jsonl --out reports/register_drift.json
```

分析脚本报告 Contain-Match、选择性准确率、弃答率、全部取回证据的召回、
逐题按 hop 召回、轮数和实际 token/call 均值。`summarize_repeats()` 汇总
多次实验均值与样本方差。费用应结合实验时实际模型价格换算，不硬编码价格。

LLM-Eval 作为 run 行的可选布尔字段 `llm_eval` 输入；未提供时报告 null 与
样本数 0，不把 substring 得分伪装成 LLM 判卷。独立标注的
`false_resolution` 可指出登记表误结案；给基线添加 `posthoc_sufficient`
可以识别证据不足却提前 answered 的情况。Verifier 的否决本身不等于一次
最终 false resolution，也不能当作 gold 判卷标签。

失败优先级为 judge_error → abstention → reasoning_error → evidence_missing。
无可用 gold 对齐或判卷信息的条目单列 `unaligned_or_unlabeled`。成功条目
不参与失败归因。只有新取回的 gold 才标记 `reachable_unasked`；没取到
不能证明不可达。

`eval/drift_alignment.py` 提供 40 字符文本指纹和模糊窗口对齐。把对齐后的
chunk id 写入 case 的 `metadata.aligned_gold_chunks`，未对齐段落索引写入
`metadata.unaligned_gold_passages`，按 hop 的 chunk 写入
`metadata.gold_evidence_by_hop`。也接受明确 `chunk:` 前缀的 gold_evidence。
仓库 G2 的 gold_evidence 是实体/关系名称，不能直接拿来计算段落召回。

⚠ 差异：保留当前检索 top-k 与图检索策略，未改成文档示例的全文/语义 top-5；
工具已返回正文，因此没有新增 read_chunk 后端。上下文使用保守字节估算，
未绑定特定 tokenizer。为保证产品路径的证据约束，没有提供关闭不变量的
运行配置，也没有加入无证据的“最可能答案”策略。

HotpotQA、2WikiMultiHopQA、MuSiQue 各 1000 题与真实模型多种子结果仍需
独立评估语料、对齐和配额；本次工程验证不能证明论文中的准确率收益。
待验证项见 `docs/reviews/IMPORTANT.md` 的需求登记表条目。
本次实测结果见 [工程验证记录](../reviews/REQUIREMENT_REGISTER_VALIDATION.md)。
