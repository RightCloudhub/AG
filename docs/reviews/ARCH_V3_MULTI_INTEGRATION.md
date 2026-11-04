# 架构 V3、多 Agent 与设计分支集成记录

日期：2026-11-04。集成分支：`merge/arch-v3-multi-agent`。

本轮按用户确认的「先完成可靠集成，剩余缺陷挂账」执行。
三个分支的提交历史均通过 merge 保留；multi 仍为显式开启的实验路径。
本记录证明组合后的工程验证，不代表线上效果或审查中全部缺陷已经解决。

## 1. 来源与冲突处理

| 来源 | 固定提交 | 处理 |
|---|---|---|
| `merge/integration` | `1fc56898` | 新分支基线 |
| `docs/multi-agent-design` | `39958a61` | 保留新增设计；IMPORTANT 冲突只引入新增状态，不恢复已迁入 CHANGELOG 的旧历史表 |
| `feat/arch-v3-batch-1` | `d880247f` | 保留 skip/递归/reset、共享熔断、预算计量、脱敏和遥测 |
| `feat/multi_agent` | `c9adb45f` | 合入黑板、Send 子图、角色配置、缓存、v4 状态、评测及 UI 事件支持，并修复下列组合问题 |

多 Agent 合并的文本冲突为 `CLAUDE.md`、`docs/ARCHITECTURE.md` 和 `llm/provider.py`。
provider 保留 A 的预留/结算、transport、半开许可和 `circuit_for()`，同时接入 B 的
`completion_payload()`、`for_role()`、`for_budget()` 及 scoped 输出限制。

源分支及对应 worktree 保持原提交。原审查文档现为根目录未跟踪文件 `advice.md`，
本轮保留其内容和跟踪状态；测试原始输出写入 `/tmp`。

## 2. 本轮集成修复

| 范围 | 最终行为 | 验证 |
|---|---|---|
| 默认 live 路由（B-01 / I-06） | 删除强制启用 register；single 尊重 `requirement_register.enabled`。默认 Fast Path 保留原客户端/额度；multi 的角色选用、小档预算和总结规则只在显式 multi 中生效 | direct graph / pipeline 分别验证开关两态；默认 entry 客户端和预算保持原值 |
| 根预算（I-01） | 所有 multi 节点的局部 tracker 通过 `parent` 同步预留、结算、释放及记录 embedding/失败；完成时不再直接覆盖根计数 | 三分支竞争一个调用槽，只允许一个成功；传输失败释放两级额度；畸形 choices 的已完成 usage 和解析失败进入根预算 |
| 角色熔断（I-02 / I-03） | 角色模型从原共享 breaker 作用域派生；同模型 researcher / writer 复用状态，健康 verifier 独立 | 跨两个查询创建 provider，第一次故障会阻止第二次同模型调用 |
| 归因（I-04） | `LLMRole` 继续表示操作阶段；ledger 的 `role` 表示模型分工。研究者和初始化使用 `register_update`，批量复核使用 `register_verify`；embedding 次数进入 receipt | 带入口 chat/embedding 的完整 multi 查询，根计量与 chain 一致，阶段指标完整且无 unknown |
| 状态（I-05） | 保留 B 的 v4 reducer/迁移与 A 的完整 reset；补上新查询 `deadline=0` 初始化 | A 的 every-channel 回归先失败后通过；v4 / 旧状态迁移与恢复测试通过 |
| SSE 组合 | 保留终止 executor 不重复发事件和 multi 事件分流；抽出 critic 事件 helper | 组合后的圈复杂度从 11 降至门禁内，事件相关单测通过 |
| 文档（I-07，部分） | 同步实际路由、预算、配置、设计差异和验收边界；修正非劣判据与不可用的环境变量命令 | 源码、配置、CLAUDE、ARCHITECTURE、设计与 workstream 对照复核；UI 的 B-07 另行挂账 |

`restore_usage()` 只在图边界按 checkpoint 中已有凭据恢复计数下限，绝不降低本次已计量值。
它不是并发聚合器，也不能重建未持久化的失败凭据或旧 checkpoint 的完整阶段指标。
现有原生失败分支恢复能力保留；B-03 的新 runtime 费用恢复缺口未宣称关闭。

新增 `tests/unit/test_arch_multi_integration.py`，并将迁移测试改为验证显式开关。
修复前新回归出现 8 个失败，另有已有全通道初始化回归失败；修复后均通过。

## 3. 组合后的验证

| 检查 | 结果 |
|---|---|
| `ruff check src tests scripts` | 通过 |
| `ruff format --check src tests scripts` | 542 文件通过 |
| `mypy` | 379 源文件通过 |
| TypeScript 5.9.3 / `web/jsconfig.json` | 通过 |
| `python scripts/check_code_metrics.py` | 通过，无新增豁免 |
| `pytest tests/unit --cov=agentic_graphrag --cov-fail-under=80 -q` | **1149 passed、4 xfailed、1 warning；92.07% coverage** |
| 金标审计 G2 / pilot | 200 题、0 flagged |

四个 xfail 为已有 planner 债务。使用现有 `.venv`，没有变更依赖/锁文件。
候选评测经 QueryApp 路径，显式设置 multi / parallel=3，但以 `--no-llm --memory-graph`
验证确定性回退。基线为上轮已验证的固定 `1fc56898` 输出。

| 离线数据集 | 基线 | 集成候选 | CI 下限 |
|---|---:|---:|---:|
| POC agentic | 31/31 | 31/31 | 30/31 |
| POC triage | 31/31 | 31/31 | 30/31 |
| G2 | 200/200 | 200/200 | 196/200 |
| compositional | 54/54 | 54/54 | 52/54 |
| negative | 20/20 | 20/20 | 19/20 |

五组 `check_eval_floor.py` 和 `eval_compare.py --fail-on-worse` 全部退出 0；
配对正误翻转为 0，精确 McNemar p=1，LLM 调用均为 0。
该结果不能验证真实模型 Prompt、研究者效果或上线 P95。

### 离线耗时复核

首轮候选评测与全量单测同时运行，其耗时不用于比较。随后停止其它验证负载，
用相同 G2 / pilot 数据及无缓存配置交错运行基线、原 B 和集成候选：

| 版本 | 次数 | 各轮 P50 的中位数 | 各轮 P95 的中位数 | 各轮 P95 范围 |
|---|---:|---:|---:|---:|
| 原 integration `1fc56898` | 3 | 38.00 ms | 62.05 ms | 59.05–69.10 ms |
| 原 B `c9adb45f` | 3 | 45.50 ms | 73.00 ms | 72.00–77.05 ms |
| 集成候选 | 4 | 45.00 ms | 75.65 ms | 75.00–77.00 ms |

各轮均 200/200、零 LLM 调用。相对原 integration，候选 P95 增加约 13.60 ms（21.9%）；
较大部分差异在原 B 中已存在。候选相对 B 约 +2.65 ms，范围重叠，不能据此归因到某项
整合改动。此结果已作为性能问题挂账，**不认定性能验收通过**；本轮按用户指定范围完成
实验分支集成，主集成/生产启用前仍需定位开销、复测并按工程规则验收。
原始结果见 `timing-summary.json` 和 `evals/{base1…3,source1…3,candidate1…4}/g2/`。

本机复核附件：`/tmp/ag-arch-multi-integration-0IvZ8W82/`，含 `pytest.log`、
`mypy.log`、`ruff.log`、`format.log`、`metrics.log`、`typescript.log`、`gold.json`，
以及 `evals/multi/<dataset>/` 下的逐题输出、accuracy、floor 和 paired 文件。
临时目录可能被系统清理，主要数值已保留在本文。

## 4. 后续修复与启用条件

完整挂账位于 [IMPORTANT.md](IMPORTANT.md) §6，本轮仍保留：

- A-01：HTTP 200 外层解码/结构错误的调用额度遗漏。
- A-02：在途 token 没有原子预留，不能保证严格并发 token 上限。
- B-02 / B-03：可恢复研究者异常隔离、失败凭据持久化和 API 恢复入口。
- B-04：入口到排队、工具、LLM、Writer 的统一 deadline 与取消。
- B-05 / B-06：schema/时间约束下的冲突判定、verifier 分批和 Writer 上下文预算。
- B-07：预算停止的 UI 状态、最终链稳定分支身份。
- 设计剩余项：no_progress、冲突状态迁移、集中 update 的取舍和跨图恢复契约。
- 离线性能：定位上述约 13.60 ms 的 P95 增量，性能验收通过前不向主集成/生产推广。
- 真实效果：dev Prompt 回归及原 live DAG / register / multi-1 / multi-3 四臂评测；
  预先约定非劣界限、样本量、费用与 P95 门槛。

本轮不改变 multi 默认值，不把已有 31 题 live 报告作为原 DAG 兼容性证明。
后续修复应追加独立提交、对应回归和更新本账，再单独决定线上启用或默认迁移。
