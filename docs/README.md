# 文档地图

全仓库文档的**唯一导航入口**：每份文档一行说明，并声明各类状态的权威出处。新文档入库时在对应分组登记一行（文档同步的强制约定见 [`plan/engineering/rules.md`](../plan/engineering/rules.md)）。

> ⚠️ **命名陷阱**：`ARCHITECTURE.md`（描述实际实现）≠ `ARCHITECTURE_DESIGN.md`（洁净室设计稿，"should be"）；`IMPORTANT.md` 是债务总账，不是"重要文档合集"。

**语言约定**：实现 / 运维 / 计划文档为中文；洁净室设计与部分门禁评审包为英文。

## 状态口径（谁是权威）

| 问题 | 权威出处 |
|---|---|
| 当前进度、债务 / 延期 / 不做事项 | [`IMPORTANT.md`](./IMPORTANT.md) §0 状态快照（**必读总账**） |
| ENT-01…08 实施状态 | [`ENTERPRISE_READINESS.md`](./ENTERPRISE_READINESS.md) §3.5 |
| 门禁判据 G1–G4 | [`plan/roadmap.md`](../plan/roadmap.md) |
| 业务逻辑断链 BL-01…14 | [`BUSINESS_LOGIC.md`](./BUSINESS_LOGIC.md) §7 |
| 金标人工签字 | [`evals/datasets/GOLD_SIGNOFF.md`](../evals/datasets/GOLD_SIGNOFF.md)（尚未签署） |
| 验收项 AC-1~7 / FR / NFR | [`PRD.md`](../PRD.md) |

## 架构与设计

| 文档 | 说明 |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | **实际实现**的模块地图、查询生命周期、离线/在线双轨（随代码更新） |
| [`ARCHITECTURE_DESIGN.md`](./ARCHITECTURE_DESIGN.md) | 洁净室**设计**（英文，从一句话前提盲写，"should be"） |
| [`DESIGN_VS_IMPLEMENTATION.md`](./DESIGN_VS_IMPLEMENTATION.md) | 上两者的逐项 diff：D1–D10 差异 / E1–E3 代码反超 |

## 业务逻辑与审计

| 文档 | 说明 |
|---|---|
| [`BUSINESS_LOGIC.md`](./BUSINESS_LOGIC.md) | 业务逻辑流图 + BL-01…14 完整性审计（file:line 证据），§7 为修复落地状态 |
| [`BL_FIX_VERIFICATION.md`](./BL_FIX_VERIFICATION.md) | ⏳ BL 修复变更集的一次性验证清单（2026-08-08 变更未实跑门禁；逐条实跑确认后即可归档） |
| [`IMPORTANT.md`](./IMPORTANT.md) | 债务 / 延期 / 不做总账（本目录唯一的长期台账） |

## UI / 运维 / 语料

| 文档 | 说明 |
|---|---|
| [`UI_DESIGN.md`](./UI_DESIGN.md) | P5-UI-02 角色工作台的视觉规范（唯一视觉权威） |
| [`ops-runbook.md`](./ops-runbook.md) | 运维手册：启动、环境变量、错误码、worker、保留清理、告警建议 |
| [`EXTERNAL_RUNTIMES.md`](./EXTERNAL_RUNTIMES.md) | 无 Docker 环境的外部运行时（tarball Neo4j + JDK、Vue vendor 钉版） |
| [`REAL_DOMAIN_PLAYBOOK.md`](./REAL_DOMAIN_PLAYBOOK.md) | 真实领域语料接入剧本（决策清单 → 导入管线） |
| [`G1_review.md`](./G1_review.md) | G1 评审的指针存根（正文在 [`reports/`](../reports/)） |

## docs/ 之外的文档

| 文档 | 说明 |
|---|---|
| [`README.md`](../README.md) | 项目门面：特性、快速开始、API 一览、当前状态表 |
| [`PRD.md`](../PRD.md) · [`lixiang.md`](../lixiang.md) | 产品需求（FR/NFR/AC-1~7）· 立项建议书（历史源头，目标/里程碑已被 PRD 形式化） |
| [`Spec.md`](../Spec.md) | 系统级不变量 S1–S7 + 人工核查清单 |
| [`PITCH.md`](../PITCH.md) | 项目动机、一句话概括、面试金句（非需求文档） |
| [`CLAUDE.md`](../CLAUDE.md) | Agent 工作指引：命令、架构导览、约定 |
| [`plan/README.md`](../plan/README.md) | 实施计划入口（阶段 × 工作流双视角） |
| [`reports/`](../reports/) | 门禁评审包（G1 / G1→G2 / G2，冻结的历史证据，不再更新） |
| [`evals/datasets/ANNOTATION_SPEC.md`](../evals/datasets/ANNOTATION_SPEC.md) | 金标评测集标注规范 |
| [`configs/prompts/`](../configs/prompts/) | 五个 Agent 角色（planner/executor/critic/answer/extract）的 LLM 系统提示词 |
