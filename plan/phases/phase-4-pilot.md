# 阶段四：试点上线（2-3周）

**目标**：接入真实业务场景小范围灰度，完成全部验收（AC-1~7），建立反馈与人工审核回路。
**前提**：G3 通过；试点业务方与灰度用户名单确认。

## 周计划

| 周 | 重点 |
|---|---|
| W1 | 问答界面完成 + 生产环境部署 + 安全检查 + 内部试用 |
| W2 | 灰度放量 + 反馈收集 + badcase 快速修复 |
| W3 | 验收测试全量执行 + G4 评审 + 试点总结 |

## 任务清单

### 界面与接入（UI）
- [x] `P4-UI-01` 问答 Web 界面 — Claude 风格浅色对话布局（`web/`）；SSE 进度、推理链、反馈；`/web`（**后续框架化见 P5-UI-01** / [p5-ui-01-vue-refactor.md](./p5-ui-01-vue-refactor.md)）
- [x] `P4-UI-01b` UI 增强 — 内联引用角标、子问题分解树、图路径 chips（非编辑器）；见 `api-and-ui.md` §2.3；实现已并入 P5-UI-01 组件层
- [x] `P4-UI-02` API 鉴权 + 速率限制 — `api/auth.py`（`AGR_REQUIRE_AUTH` / `AGR_API_KEYS` / QPS+并发）；**ENT-04 增补（2026-07-25）**：三角色 RBAC（`api/rbac.py`，key 三段式 `tenant:key:role`）+ `require_role()` 路由守卫 + key 过期

### 上线准备（REL）
- [ ] `P4-REL-01` 生产环境部署：环境隔离、密钥管理、数据租户隔离核查（NFR-06）— **代码侧已由 ENT-06 承接**（`tenant_id` 贯穿 stores/检索/agent，跨租户零命中单测）；**运维侧仍开**：物理分库/标签隔离、真实 Neo4j/Qdrant 跨租户回归
- [x] `P4-REL-02` 安全基础：鉴权中间件、参数化 Cypher、envelope 不泄栈；**ENT-06 增补**：应用层上传治理（5MB/20/白名单/413）+ PII 脱敏开关 + live 凭据启动 fail-fast；完整 security-reviewer 生产签发仍待
- [x] `P4-REL-03` 监控指标 API — `GET /v1/metrics`（admin）+ `GET /metrics-prom` Prometheus 抓取（ENT-08）；告警规则示例已入 ops-runbook，部署侧落地仍待
- [x] `P4-REL-04` 运维手册 — `docs/ops-runbook.md`（**ENT 全量增补 2026-07-25**：错误码对照 / 日志字段字典 / 四点回查 / worker / 保留清理 / 告警规则）

### 灰度与反馈（OPS)
- [ ] `P4-OPS-01` 灰度计划执行：内部用户 → 试点业务方 5-10 人 → 试点全组 — **流程项，非代码**
- [x] `P4-OPS-02` 反馈回路 — `POST /v1/feedback` → review queue + audit metadata
- [x] `P4-OPS-03` 人工审核回路 — 负反馈入 `ReviewQueue`；复核 API 已有
- [ ] `P4-OPS-04` badcase 周会机制 — **流程项**

### 验收（AC）
- [ ] `P4-AC-01` 执行 PRD 第7节全部验收项 AC-1 ~ AC-7，逐项留存证据
- [ ] `P4-AC-02` 生产环境审计回查抽样验证（AC-3）
- [ ] `P4-AC-03` G4 评审：验收报告 + 试点用户反馈汇总 + 规模化建议

## 交付物

1. 试点上线的生产系统（含界面）
2. 验收报告（AC-1~7 逐项证据）
3. badcase 库与人工审核回路
4. 试点总结与规模化立项建议

## 出口标准

见 [roadmap.md](../roadmap.md) G4 门禁。核心：**AC-1~7 全部通过，灰度反馈 ≥2 周**。

## 回滚预案

- 效果类事故（编造、错误率飙升）：分诊阈值调高，全量走 Fast Path 或降级为纯向量 RAG
- 成本类事故：预算熔断已有硬上限；必要时下调单查询上限
- 图谱数据事故：图谱按版本快照回滚，索引重建
