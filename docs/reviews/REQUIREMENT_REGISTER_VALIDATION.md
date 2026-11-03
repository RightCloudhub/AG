# 需求登记表工程验证

工程实现已有离线验证；登记表默认关闭，`--no-llm` 保持确定性流程。
状态不变量、独立 Verifier、证据追加、累计否决退休及收敛提醒的边界修复已完成。
历史逐题对比、测试数量、旧门禁与后续复核见
[CHANGELOG](../../CHANGELOG.md#register-original-validation)；边界修复记录见
[CHANGELOG](../../CHANGELOG.md#integration-updates)。

测试覆盖：状态不消失、证据追加、未知引用与无理由退休被拦截、文本和
依赖不可删改、实体锚与依赖就绪、单 chunk 解决多项、独立 Verifier 否决和
故障、更新 JSON 错误、预算中断、去重与已读集合、checkpoint 恢复、SSE、
种子/更新/delta/Verifier 消融开关、部分答案与严格弃答、失败优先级、
gold 模糊对齐、未对齐排除、按 hop 召回、选择性准确率和重复实验方差。

当前门禁以 [.github/workflows/ci.yml](../../.github/workflows/ci.yml) 为准：
POC agentic / triage 各 ≥30/31、G2 ≥196/200、probe ≥52/54 与 ≥19/20，
金标审计 `--max-flagged 0` 阻塞合并。完整单测与覆盖率门禁包含前端结构测试。

真实 LLM 配对评估仍待完成：HotpotQA / 2Wiki / MuSiQue 各 1000 题、
多种子方差与费用、四跳覆盖率和 Verifier 误判。离线对比不能证明真实模型收益；
段落金标未对齐时召回应为 null，未执行 LLM 判卷时 LLM-Eval 应为 null。
协议见 [REQUIREMENT_REGISTER.md](../design/REQUIREMENT_REGISTER.md)，
当前缺口见 [IMPORTANT.md](IMPORTANT.md) §0 与 §6。
