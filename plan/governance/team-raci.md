# 团队分工与 RACI

**编制依据**：立项建议书第七节资源需求。角色为职能而非人头 —— 小团队可一人兼多角色，但 G 门禁评审的 A（负责决策者）不可缺位。

## 1. 角色与人力（建议配置）

| 角色 | 人数 | 职责范围 | 主要工作流 |
|---|---|---|---|
| 项目负责人（PM/TL） | 1（可兼任） | 排期、门禁评审组织、风险管理、资源协调 | roadmap、governance |
| 架构师 | 1（可兼任） | 选型决策（ADR）、接口契约、跨工作流技术仲裁 | tech-stack、repo-structure |
| Agent 工程师 | 1-2 | Planner/Executor/Critic/Memory、Prompt 工程、分诊、生成 | agent-orchestration |
| 图谱工程师 | 1-2 | 抽取管线、Schema、消歧、增量更新、质量治理 | knowledge-graph |
| 检索工程师 | 1 | 三路检索、融合排序、性能优化、API 服务 | retrieval、api-and-ui |
| 知识运营/标注 | 按需 | 评测集标注、抽取审核、badcase 复核 | evaluation、审核队列 |
| 前端（阶段四借调） | 0.5 | 试用界面 | api-and-ui §2 |

## 2. RACI 矩阵

R=执行 A=负责 C=咨询 I=知会

| 事项 | 项目负责人 | 架构师 | Agent | 图谱 | 检索 | 运营 |
|---|---|---|---|---|---|---|
| 领域/语料确定（P1-GOV-01） | A | C | I | R | I | C |
| 技术选型 ADR | C | A/R | C | C | C | — |
| 图谱 Schema 定义 | I | C | C | A/R | I | C |
| 抽取管线与质量 | I | C | — | A/R | — | R(审核) |
| 三路检索与融合 | I | C | C | C | A/R | — |
| Agent 循环与 Prompt | I | C | A/R | — | C | — |
| 护栏与预算控制 | C | C | A/R | — | R(网关) | — |
| 评测集构建 | A | C | C | C | C | R |
| 评测脚本与报告 | A | C | R | R | R | C |
| API 与界面 | I | C | C | — | A/R | — |
| 安全检查与上线 | A | R | C | C | R | — |
| 门禁评审（G1-G4） | A | R | R | R | R | C |
| 风险登记维护 | A/R | C | C | C | C | C |

## 3. 协作机制

| 机制 | 频率 | 内容 |
|---|---|---|
| 站会 | 每日（POC/MVP 期）→ 隔日（优化期后） | 进度、阻塞、当日目标 |
| 工作流同步会 | 每周 | 跨工作流契约变更（候选项契约、推理链 Schema 等）、依赖对齐 |
| badcase 归因会 | 每轮评测后 / 试点期每周 | 归因分布 → 下轮优化优先级（evaluation.md §6） |
| 门禁评审 | 每阶段末 | roadmap 判据逐项过、风险登记更新、下阶段计划确认 |
| ADR 评审 | 按需 | 选型/契约变更 |

## 4. 契约变更规则（跨工作流协作核心）

以下契约的变更须经架构师评审 + 受影响工作流确认，并同步更新对应计划文档：
1. 统一候选项契约（retrieval.md §2）—— 影响检索 ↔ Agent
2. 推理链 JSON Schema（agent-orchestration.md §3）—— 影响 Agent ↔ API/界面/评测/审计
3. 存储抽象接口（repo-structure：stores/interfaces.py）—— 影响全部
4. 图谱 Schema —— 影响图谱 ↔ 检索 ↔ 评测集
