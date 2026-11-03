# 文档索引

本文档目录维护当前设计、实现与待验收项；历史变更、旧快照和验证记录集中在
[CHANGELOG.md](../CHANGELOG.md)。更新状态时替换旧结论，将过程记录写入 CHANGELOG。

| 目录 | 文档 | 内容 |
|---|---|---|
| `./` | [ARCHITECTURE.md](./ARCHITECTURE.md) | 当前实现的模块架构图与运行路径 |
| | [EXTERNAL_RUNTIMES.md](./EXTERNAL_RUNTIMES.md) | 前端视觉校验的 headless Chromium 环境 |
| `product/` | [PRD.md](./product/PRD.md) | 产品需求与验收标准 |
| | [WEBAPP_GUIDE.md](./product/WEBAPP_GUIDE.md) | 多租户应用使用指南：启动、注册、邀请、知识导入、问答与常见问题 |
| | [TEN_HOP_QUESTIONS.md](./product/TEN_HOP_QUESTIONS.md) | 已导入语料的三道十跳题目：完整证据链、原文出处与验收方式 |
| | [PROPOSAL.md](./product/PROPOSAL.md) | 项目立项建议书 |
| | [PITCH.md](./product/PITCH.md) | 项目一句话定位与讲述要点 |
| `design/` | [ARCHITECTURE_DESIGN.md](./design/ARCHITECTURE_DESIGN.md) | 目标架构设计 |
| | [SETUP_DESIGN.md](./design/SETUP_DESIGN.md) | 项目初始化（setup）设计与优化：单一入口、锁文件、Python 版本、体检与文档同步 |
| | [BUSINESS_LOGIC.md](./design/BUSINESS_LOGIC.md) | 业务逻辑与完整性审查（BL-xx） |
| | [MULTITENANT_WEB.md](./design/MULTITENANT_WEB.md) | 多租户 Web 应用页面设计记录（账号 / 团队空间，沿用推理笔记本主题） |
| | [REQUIREMENT_REGISTER.md](./design/REQUIREMENT_REGISTER.md) | 需求登记表模式：搜索引导、停止判据与评估口径 |
| | [DESIGN_VS_IMPLEMENTATION.md](./design/DESIGN_VS_IMPLEMENTATION.md) | 设计与实现差异 |
| | [UI_DESIGN.md](./design/UI_DESIGN.md) | Web 工作台视觉规范 |
| | [REFACTOR_PLAN.md](./design/REFACTOR_PLAN.md) | 架构重构计划与逐项进度（core / app / bootstrap / 事件 / 账本） |
| | [ARCH_OPTIMIZATION_V2.md](./design/ARCH_OPTIMIZATION_V2.md) | 架构优化当前实现、后续设计与验收标准 |
| `ops/` | [RUNBOOK.md](./ops/RUNBOOK.md) | 部署、环境变量、告警、排障 |
| | [WEBAPP.md](./ops/WEBAPP.md) | 独立 Web 应用配置、开发邮件、数据库、会话与租户隔离 |
| | [EXTERNAL_RUNTIMES.md](./ops/EXTERNAL_RUNTIMES.md) | Neo4j 等外部运行时 |
| | [REAL_DOMAIN_PLAYBOOK.md](./ops/REAL_DOMAIN_PLAYBOOK.md) | 真实领域语料接入 |
| `reviews/` | [IMPORTANT.md](./reviews/IMPORTANT.md) | 延期工作总账 |
| | [TYPE_SAFETY_AUDIT.md](./reviews/TYPE_SAFETY_AUDIT.md) | 全项目类型安全与常量化检查计划、问题台账和修复验证 |
| | [IMPORTANT_DOCUMENTATION.md](./reviews/IMPORTANT_DOCUMENTATION.md) | BL 修复验证状态与当前验证入口 |
| | [ENTERPRISE_READINESS.md](./reviews/ENTERPRISE_READINESS.md) | 企业级能力审计 |
| | [PRODUCT_READINESS_GAP_ANALYSIS.md](./reviews/PRODUCT_READINESS_GAP_ANALYSIS.md) | 产品交付差距 |
| | [CONTEXT_ENGINEERING_AND_OBSERVABILITY_GAPS.md](./reviews/CONTEXT_ENGINEERING_AND_OBSERVABILITY_GAPS.md) | 上下文工程与可观测性差距 |
| | [REQUIREMENT_REGISTER_VALIDATION.md](./reviews/REQUIREMENT_REGISTER_VALIDATION.md) | 需求登记表工程验证（默认关闭，真实 LLM 评估待做） |
| | [GRAPHRAG_LANDSCAPE_REVIEW.md](./reviews/GRAPHRAG_LANDSCAPE_REVIEW.md) | GraphRAG 业界对比 |
| `assets/` | [architecture.html](./assets/architecture.html) | 可视化架构总图（浏览器打开） |

阶段计划在 [`../plan/`](../plan/README.md)，门禁报告在 [`../reports/`](../reports/)。
