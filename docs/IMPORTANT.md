# IMPORTANT — 延期工作总账

**用途：** 汇总所有**有意延期、被阻塞、未完成或明确不做**的事项，便于一眼扫完。  
**不是**路线图重写——细节仍以各阶段计划为准；本文件是债务 / 缺口总览。  
**最近汇总：** 2026-07-22  
**来源：** `plan/roadmap.md`、各阶段计划、`reports/G1_review.md`、`reports/G1_to_G2_status.json`、PRD 开放问题、风险登记册、`pyproject.toml` 覆盖率 omit、代码注释。

**符号约定**

| 符号 | 含义 |
|------|------|
| 🔴 | 阻塞下一门禁 / 关键路径 |
| 🟠 | 本阶段承诺仍未关闭 |
| 🟡 | 部分完成 / 仅脚手架 / 质量口径有保留 |
| ⚪ | 排在后续阶段（未逾期） |
| ⬛ | V1 明确不做 / 仅规模化立项 |

门禁翻转、任务关闭或评审接受新延期时，请同步更新本文件。

---

## 0. 当前态势（快照）

| 项 | 状态 |
|----|------|
| G1（POC 出口） | **Conditional-Go**；过渡门禁工程 **PASS**（2026-07-20） |
| G1 → G2 门禁 | **PASS**（`reports/G1_to_G2_status.json`）— 见 closeout |
| P2-ENTRY-01 | **工程入场允许**（合成语料 + C2 live 配额 caveat） |
| 阶段二 MVP 代码 | **P0 代码 + 金标 ≥200 + dev offline 评测** 已齐；**正式 G2 效果**仍 Conditional |
| 阶段三代码 | PERF/OP/KG/AN **代码 [x]**；P3-EV **offline 脚手架**见 `scripts/p3_ev_offline.py` |
| 仍开 | 真域签字、live fair baseline、**生产** P95≤8s、G4 灰度流程 |
| Live heldout（合成） | agentic **rescored 93.6%** / +70pp / recall 0.94；P95 **~92s**（未达 AC-4） |
| 2026-07-22 | badcase：no_answer 评分 + critic 续跳；真域剧本 `docs/REAL_DOMAIN_PLAYBOOK.md`；P95 脚手架 `p3_load_http.py` |
| 本环境 | SenseNova chat OK；embedding 易 401（需 `LLM_EMBEDDING_*`）；Qdrant 常未起 |

```bash
./scripts/g2_formal_eval.sh --with-llm
PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py --n 20
# 真域：docs/REAL_DOMAIN_PLAYBOOK.md · ./scripts/import_real_corpus.sh
.venv/bin/python scripts/check_code_metrics.py
```

---

## 1. 🟡 G1 → G2 过渡 — 工程已关 / 产品 caveat 仍开

剧本：[`plan/phases/g1-to-g2-transition.md`](../plan/phases/g1-to-g2-transition.md)  
状态：[`reports/G1_to_G2_status.json`](../reports/G1_to_G2_status.json) · closeout

| ID | 任务 | 状态 | 说明 |
|----|------|------|------|
| **C1** | P1-GOV-01 | 工程 PASS | 合成 226 篇；**产品真域未锁** |
| **C2** | P1-EV-04 | 自动化 PASS | live 准确率 caveat（403） |
| **C3** | P1-EV-05 | pass_partial | Neo4j offline 14/20 |
| 入场 | P2-ENTRY-01 | ALLOWED | 效果门禁勿用合成 offline 冒充 |

**G1 保留项**：离线启发式 ≠ 生产 LLM；seed 抽检 ≠ 人工 LLM 审计；合成语料 ≠ 正式试点域。

---

## 2. 🟠 阶段二 MVP — 效果门禁仍 Conditional

权威：[`plan/phases/phase-2-mvp.md`](../plan/phases/phase-2-mvp.md)

| ID | 状态 |
|----|------|
| P2-KG-04 / EV-02/05/06 | **工程关闭**（dev offline + 自动金标） |
| 人工抽检签字 / heldout 正式锁定 / live | **仍开** |
| heldout offline 脚手架 | **已补** `reports/g3_offline/`（P3-EV 脚本复用 heldout） |

### G2 门禁清单

来自 [`plan/roadmap.md`](../plan/roadmap.md)：

- [ ] 全部 P0 需求在**真实试点条件**下实现并通过测试  
- [ ] 评测集 ≥200 条已标注；一键评测脚本端到端可用  
- [ ] Accuracy 相对 Baseline 趋势 **≥ +15pp**（为最终 +25pp 留空间）  
- [ ] 证据 Recall **≥ 75%**（最终目标 85%）

> 说明：离线 interim 对比（`reports/eval_comparison.md`）在 **20 条离线 case** 上已约 +15pp / 高 recall——**不能**当作 G2 held-out 证据。

### 阶段二「已完成但未完」

| 领域 | 已交付 | 仍延期 |
|------|--------|--------|
| P2-AG-03 Memory | typed state + `MemorySaver` checkpointer + 节点 hydrate | 磁盘 SQLite checkpointer 可选（需 `langgraph-checkpoint-sqlite`）；跨进程审计 API 仍在 P3-AN-01 |
| P2-EV-01 | Case schema + 确定性金标生成器 | ≥200 人工/精选集（EV-02） |
| P2-RT-01 图 beam | 词法 cue + beam 上限 + `blend_relation_score` 嵌入钩子 | 生产 embedder 接入（live cosine 相似度） |
| P2-KG-01 抽取管线 | journal / retry / quarantine / 溯源 | 试点语料上的 live 抽取质量；人工隔离区审核 UX |
| 覆盖率门禁 P2-ARCH-04 | fail_under 80% | 仍有模块 **omit**（见 §6） |

### 阶段二明确不做（计划原文）

- 复杂度分诊、SSE 流式、缓存/并行融合 → **阶段三**  
- 实体消歧界面、增量更新 → **阶段三**  
- 问答 Web 界面 → **阶段四**

---

## 3. 🟡 质量 / 环境替代（POC 可接受，G2/G3 须替换）

| 替代 | 用途 | 须在此前替换 |
|------|------|--------------|
| InMemoryGraphStore + seed 三元组 | 离线多跳演示 / CI | C3 Neo4j 回归 + 试点规模图 |
| `--no-llm` + 离线答案启发式 | 确定性 20 case 准确率 | C2 live Planner/Critic/Answer；生产 LLM 路径 |
| Seed 三元组「抽检」（schema 合法即 correct） | G1 抽取门禁 | 人工标注 LLM 抽取样本 ≥70% |
| 合成试点语料（`scripts/generate_pilot_corpus.py`） | 工程 C1 规模冒烟 | 产品授权真实领域 |
| Interim 6 篇 `data/raw/` | 早期 POC 叙事 | 试点重建 |
| 无 live Qdrant 的 BM25 / 进程内向量 | 本地检索测试 | 试点部署 Qdrant（+ embedding） |
| 非融合的多路候选拼接 | ~~Executor 工具选择~~ | **已关** — `retrieval/fusion.py` RRF + `Reranker` 协议 |

---

## 4. ⚪ 阶段三 — 工程优化（代码已落地；G3 效果门禁仍开）

权威：[`plan/phases/phase-3-optimization.md`](../plan/phases/phase-3-optimization.md)  
**前提：** G2 通过（效果数字仍 Conditional）。

| 轨道 | 状态 | 说明 |
|------|------|------|
| PERF 01–07 | **代码 [x]** | 分诊/Fast Path、并行检索、RRF、缓存、SSE、压测脚手架 |
| OP 01–04 | **代码 [x]** | metrics、三级预算、trace、护栏脚本 |
| KG 01–04 | **代码 [x]** | incremental / resolution / review queue / knowledge API |
| AN 01–02 | **代码 [x]** | audit store + web 推理链展示 |
| EV 01–03 | **仍开** | held-out + live 达标 + G3 正式材料 |

**仍延期：** G3 门禁数字（AC-1/2 live held-out、生产级压测 P95）。

---

## 5. ⚪ 阶段四 / 五 — 试点与规模化

### 阶段四试点（[`phase-4-pilot.md`](../plan/phases/phase-4-pilot.md)）

| ID | 状态 |
|----|------|
| P4-UI-01/02 | **代码 [x]** — Claude 风格 `/web` 对话 UI + auth/rate-limit |
| P4-UI 增强 | **代码 [x]** — 内联引用角标 + 论断列表、子问题分解树、图路径 chips（P5-UI-01 后见 `web/static/js/`；非路径编辑器） |
| P4-REL-02…04 | **部分 [x]** — ops-runbook + metrics；生产部署/告警接部署侧 |
| P4-OPS-02/03 | **代码 [x]** — feedback → review queue |
| P4-OPS-01/04、P4-AC-* | **流程/验收仍开** |

### 阶段五规模化（[`phase-5-scale.md`](../plan/phases/phase-5-scale.md)）— 方向 + 脚手架

| ID | 状态 |
|----|------|
| P5-CAP-01…04、EXT-03 | **脚手架 [x]** — graph entities API、tools registry、confidence、Reranker Protocol、多租户预算 |
| **P5-UI-01** | **[x] 代码完成** — Vue 3 零构建重构 + 交互增强（会话历史 / 中止 / 逐 turn 反馈 / 健康点）；ADR-006 已入 tech-stack；计划见 [`plan/phases/p5-ui-01-vue-refactor.md`](../plan/phases/p5-ui-01-vue-refactor.md) |
| P5-EXT-01/02、GOV-* | **立项后** |

---

## 6. 工程债务与有意覆盖率缺口

### 覆盖率 omit（`pyproject.toml` — 扩测后再移除）

| 省略路径 | 记录原因 | 延期动作 |
|----------|----------|----------|
| `cli/*`、`__main__.py` | 入口胶水 | 补薄测或保留 omit 并写清理由 |
| `stores/neo4j_store.py` | 在线适配器 | Docker 可用时做集成测（挂钩 C3） |
| `llm/provider.py` | 在线 HTTP 客户端 | 契约/mock 测；C2 下 live 冒烟 |
| `generation/answer.py` | LLM 生成路径 | live 生成测试 |
| `generation/offline_answer.py` | 大启发式；靠 agent 离线 E2E 覆盖 | 拆分后优先单测 heuristics 模块 |

另延期：**分支覆盖**（`branch = false`）。

### 产品面部分完成

| 面 | 当前状态 | 延期 |
|----|----------|------|
| `POST /v1/query` | 已有 + 鉴权/限流/SSE（P3/P4） | 租户**数据级**隔离（P4-REL-01 运维） |
| 推理链 | Schema + 响应内 chain + audit store API | 生产抽样审计（P4-AC-02） |
| BudgetTracker | 单次 + 租户/用户三级（`MultiLevelBudget`） | 生产告警接部署侧 |
| 图关系打分 | 词法 cue + `BeamConfig.relation_embed_sim` 接线（`layer_edges`/`GraphRetriever` 调用；无 scorer 时退回词法） | 生产 cosine embedder 实现（API 钩子已通） |
| SSE 流式 | **真·增量** — LangGraph `stream([updates,values])` → hops；`force_agentic` 仍发 triage；空 stream 失败不二次 invoke | — |
| 接入格式 | 偏 MD/TXT | 若试点需要，PDF 文本作为一等路径（PRD 列了 PDF 文本） |
| 评测集布局 | `evals/datasets/poc_cases.jsonl` | `dev` / `heldout` / `guardrail` 分集（R7） |
| LLM 判卷 | 不存在 | 结构计划中的 `evals/judge.py` |

### 规范 / 结构备注

- 超大文件合规拆分（2026-07-20）**已完成**；新模块保持约 200–400 行（`plan/engineering/repo-structure.md`）。  
- 进一步包布局（如 `knowledge/extraction/` 包、`ingest/` 包等）为可选对齐完整目录树。

---

## 7. PRD 开放问题（产品层仍未拍板）

来自 [`PRD.md`](../PRD.md) §9：

| # | 问题 | 代码当前默认 | 需要 |
|---|------|--------------|------|
| 1 | 图库：Neo4j vs NebulaGraph | tech-stack ADR 选 Neo4j；适配器已有 | 确认试点规模假设 |
| 2 | 延迟 P95 ≤8s Agentic / ≤3s Fast Path | 未压测 | 业务确认 + P3-PERF-07 |
| 3 | ≥200 条金标谁来标 | 仅 substrate + 合成生成 | 人力/流程（R6） |
| 4 | 首个真实试点领域 | 合成公司关系脚手架 | 产品锁定（R5、C1） |
| 5 | 各角色是否分级用模 | 有 Tier 枚举；生产策略未调优 | P3-PERF-05 + 预算 |

PRD 仍为**初稿待评审**；AC 数值指标需结合试点业务最终确认。

---

## 8. 活跃风险中编码的延期缓解

权威：[`plan/governance/risk-register.md`](../plan/governance/risk-register.md)

| ID | 风险 | 仍未落地的缓解 |
|----|------|----------------|
| R5 | 试点领域/语料不定 | 产品签字关闭 C1 |
| R1 | 图谱构建成本/周期 | C1 后试点规模抽取；质量治理在 P3 |
| R2 | Agent 多轮成本失控 | 完整 FR-OP-02 三级；分诊 FR-AG-01（P3） |
| R3 | 效果提升 &lt;25pp | EV-02 后可信 held-out；EV-05/06 badcase 闭环 |
| R6 | 标注资源不足 | EV-02 排人 / 合成+复核剧本 |
| R7 | 评测过拟合 | held-out 分集 + 线上反馈（P4） |
| R8 | 路径爆炸 | beam 上限已上；大子图压测仍在 P3 |
| R9 | 真实场景编造 | 引用绑定已有基础；人工复核回路 P4-OPS-03；真实流量 AC-7 |
| R10 | 增量更新质量回退 | 整条增量路径在 P3 |
| R11 | LangGraph 依赖风险 | 版本钉死 + 回归；自研退路未实战验证 |

---

## 9. ⬛ 明确不做（V1 / PRD）

未改 PRD 前，**不要**当阶段二/三「漏项」来排：

- 开放域全网问答（仅已接入语料）  
- 多模态（图片 / 表格图像）  
- 面向终端消费者的开放注册产品（V1 面向企业内部与集成方）  
- 完整 NebulaGraph 多集群故事（规模化立项前）  
- 阶段五 P2 能力（图谱浏览 FR-KG-07、外部工具 FR-AG-08、置信度分级 FR-AN-05、学习型 re-ranker）— 试点后再立项  

---

## 10. 验收项（AC）— 证据仍延期

| AC | 目标（PRD） | 当前证据 | 延期证明 |
|----|-------------|----------|----------|
| AC-1 Accuracy | 相对向量 Baseline +≥25pp | 离线 interim 约 +15pp / 20 case | ≥200 held-out + live agentic/baseline |
| AC-2 证据 Recall | ≥85%（跳数 ≥2） | 离线 interim 约 0.96 / 20 | 同上 + 金标证据标注 |
| AC-3 审计 | 可按查询 ID 回查链 | 响应内 / run JSONL 中有 chain | 落库 + API（P3-AN-01）；生产抽样（P4） |
| AC-4 延迟 | Agentic P95 ≤8s；Fast Path ≤3s | 仅离线毫秒级 | 压测 P3-PERF-07 |
| AC-5 增量 | 更新不中断查询 | offline smoke：`reports/g3_offline/incremental_drill.json` | 生产演练签字 |
| AC-6 护栏/预算 | 硬上限 + 专项集 | 离线可配跳数/token 护栏 | P3-OP-02/04；生产告警 P4 |
| AC-7 无引用不编造 | 编造率 0 | 离线引用绑定 + 报告字段 | live LLM + 人工抽样；灰度流量 |

---

## 11. 优先下一步（建议顺序）

1. **产品真域** — 按 `docs/REAL_DOMAIN_PLAYBOOK.md` 授权语料 + MANIFEST 签字（工程无法代替）。  
2. **配 `LLM_EMBEDDING_*`** — fair live baseline；消 OpenAI 401。  
3. **Live 重跑 3 道 3-hop badcase**（验证 critic_guard）或全量 `--with-llm`。  
4. **Staging P95** — `p3_load_http.py --target ...` + triage，目标 Agentic ≤8s。  
5. 人工金标抽检 + G2 材料诚实标注合成/真域。  

---

## 12. 索引

| 主题 | 权威文档 |
|------|----------|
| 路线图与门禁 | [`plan/roadmap.md`](../plan/roadmap.md) |
| G1 评审 | [`reports/G1_review.md`](../reports/G1_review.md) |
| G1→G2 剧本 | [`plan/phases/g1-to-g2-transition.md`](../plan/phases/g1-to-g2-transition.md) |
| 阶段一…五任务 | [`plan/phases/`](../plan/phases/) |
| 风险 | [`plan/governance/risk-register.md`](../plan/governance/risk-register.md) |
| PRD / AC / 开放问题 | [`PRD.md`](../PRD.md) |
| 目标目录树 | [`plan/engineering/repo-structure.md`](../plan/engineering/repo-structure.md) |
| 门禁 JSON | [`reports/G1_to_G2_status.json`](../reports/G1_to_G2_status.json) |
| 外部运行时（JDK/Neo4j/镜像，非 pip/npm） | [`docs/EXTERNAL_RUNTIMES.md`](./EXTERNAL_RUNTIMES.md) |

---

*关闭某项时：在此勾记，并同步阶段清单；门禁 JSON / 风险状态尽量同一变更集更新。*
