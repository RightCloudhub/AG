# AgenticGraphRAG 实施计划总览

**关联文档**：[../PRD.md](../PRD.md) · [../lixiang.md](../lixiang.md)
**版本**：V1.0（2026-07）

本目录是项目实施计划的唯一入口，按"阶段（时间维度）× 工作流（能力维度）"双视角组织。

## 目录导航

```
plan/
├── README.md                     # 本文件：导航与使用说明
├── roadmap.md                    # 总路线图：时间线、阶段门禁、依赖关系
├── phases/                       # 按阶段的执行计划（做什么、何时算完成）
│   ├── phase-1-poc.md            # 阶段一：POC 验证（3-4周）
│   ├── g1-to-g2-transition.md    # G1→G2 过渡条件（C1 语料 / C2 LLM / C3 Neo4j）
│   ├── phase-2-mvp.md            # 阶段二：MVP 构建（4-6周）
│   ├── phase-3-optimization.md   # 阶段三：工程优化（3-4周）
│   ├── phase-4-pilot.md          # 阶段四：试点上线（2-3周）
│   └── phase-5-scale.md          # 阶段五：规模化推广（持续）
├── workstreams/                  # 按工作流的技术方案与任务分解（怎么做）
│   ├── knowledge-graph.md        # 图谱构建：抽取、消歧、增量更新
│   ├── retrieval.md              # 混合检索：向量/图/全文 + 融合排序
│   ├── agent-orchestration.md    # Agent 编排：Planner/Executor/Critic/Memory
│   ├── api-and-ui.md             # 服务接口与问答界面
│   └── evaluation.md             # 评测体系：评测集、指标、回归
├── engineering/                  # 工程规范与基础设施
│   ├── tech-stack.md             # 技术选型与决策记录
│   ├── repo-structure.md         # 代码仓库结构
│   ├── testing-strategy.md       # 测试策略（TDD、80% 覆盖率）
│   └── cicd-observability.md     # CI/CD 与可观测性
└── governance/                   # 项目治理
    ├── risk-register.md          # 风险登记册
    └── team-raci.md              # 团队分工与 RACI
```

## 使用说明

- **看进度/排期** → [roadmap.md](./roadmap.md)，每阶段有明确的 Go/No-Go 门禁。
- **领任务** → `phases/` 下对应阶段文件，任务带编号（如 `P1-KG-01`），编号规则：`P<阶段>-<工作流>-<序号>`。
- **查技术方案** → `workstreams/` 下对应工作流文件，需求引用 PRD 编号（FR-*）。
- **改选型** → 先在 [engineering/tech-stack.md](./engineering/tech-stack.md) 追加决策记录（ADR），再动代码。
- **报风险** → 更新 [governance/risk-register.md](./governance/risk-register.md)。

## 计划维护约定

1. 计划文件随评审结论更新，重大变更在文件头部记录版本与变更原因。
2. 任务状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 完成 / `[-]` 取消（注明原因）。
3. 阶段门禁未通过不进入下一阶段；门禁判据见 roadmap.md。
